from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter
from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.errors import conflict, forbidden, not_found


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def charging_session_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "reservation_id": row["reservation_id"],
        "user_id": row["user_id"],
        "station_id": row["station_id"],
        "bay_id": row["bay_id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_seconds": row["duration_seconds"],
        "pricing_model": row["pricing_model"],
        "rate_eur_per_hour": row["rate_eur_per_hour"],
        "estimated_cost_eur": row["estimated_cost_eur"],
        "status": row["status"],
        "is_simulated": bool(row["is_simulated"]),
    }


class ChargingService:
    def __init__(
        self,
        database: Database,
        adapter: EcuriousAdapter,
        settings: Settings,
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings

    @staticmethod
    def _authorize(reservation: sqlite3.Row, user: Any) -> None:
        if user["role"] != "ADMIN" and reservation["user_id"] != user["id"]:
            raise forbidden("Drivers can only operate their own charging sessions")

    async def start(self, reservation_id: str, user: Any) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            reservation = connection.execute(
                "SELECT * FROM app_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if reservation is None:
                raise not_found("Reservation was not found")
            self._authorize(reservation, user)
            active = connection.execute(
                "SELECT * FROM charging_sessions WHERE reservation_id = ? AND status = 'ACTIVE'",
                (reservation_id,),
            ).fetchone()
            if active is not None:
                return charging_session_view(active)
            completed = connection.execute(
                "SELECT * FROM charging_sessions WHERE reservation_id = ? ORDER BY started_at DESC LIMIT 1",
                (reservation_id,),
            ).fetchone()
            if completed is not None:
                raise conflict("CHARGING_ALREADY_COMPLETED", "Charging session was already completed")
            if reservation["status"] != "CHECKED_IN":
                raise conflict("CHARGING_NOT_ALLOWED", "Vehicle must be checked in before charging")

        if self.adapter.mode == "mock":
            await self.adapter.simulate_charging(
                reservation["ecurious_reservation_id"], start=True
            )

        timestamp = now_iso()
        session_id = f"CHG-{uuid.uuid4().hex[:16].upper()}"
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO charging_sessions(
                        session_id, reservation_id, user_id, station_id, bay_id,
                        started_at, pricing_model, rate_eur_per_hour, status,
                        is_simulated, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'TIME_EUR_PER_HOUR', ?, 'ACTIVE', 1, ?, ?)
                    """,
                    (
                        session_id,
                        reservation_id,
                        reservation["user_id"],
                        reservation["station_id"],
                        reservation["bay_id"],
                        timestamp,
                        self.settings.simulated_charging_rate_eur_per_hour,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    """UPDATE app_reservations
                       SET status = 'CHARGING', simulated_charging = 1, updated_at = ?
                       WHERE id = ? AND status = 'CHECKED_IN'""",
                    (timestamp, reservation_id),
                )
                row = connection.execute(
                    "SELECT * FROM charging_sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                assert row is not None
                return charging_session_view(row)
        except sqlite3.IntegrityError:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM charging_sessions WHERE reservation_id = ? AND status = 'ACTIVE'",
                    (reservation_id,),
                ).fetchone()
            if row is not None:
                return charging_session_view(row)
            raise

    async def stop(self, session_id: str, user: Any) -> dict[str, Any]:
        with self.database.connect() as connection:
            session = connection.execute(
                "SELECT * FROM charging_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise not_found("Charging session was not found")
            reservation = connection.execute(
                "SELECT * FROM app_reservations WHERE id = ?", (session["reservation_id"],)
            ).fetchone()
            assert reservation is not None
        self._authorize(reservation, user)
        if session["status"] == "COMPLETED":
            return charging_session_view(session)

        if self.adapter.mode == "mock":
            await self.adapter.simulate_charging(
                reservation["ecurious_reservation_id"], start=False
            )

        ended_at = datetime.now(timezone.utc)
        duration = max(0, int((ended_at - _utc(session["started_at"])).total_seconds()))
        cost = round(duration / 3600 * float(session["rate_eur_per_hour"]), 2)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE charging_sessions
                   SET ended_at = ?, duration_seconds = ?, estimated_cost_eur = ?,
                       status = 'COMPLETED', updated_at = ?
                   WHERE session_id = ? AND status = 'ACTIVE'""",
                (ended_at.isoformat(), duration, cost, ended_at.isoformat(), session_id),
            )
            connection.execute(
                """UPDATE app_reservations
                   SET status = 'CHARGE_COMPLETED', simulated_charging = 0, updated_at = ?
                   WHERE id = ? AND status = 'CHARGING'""",
                (ended_at.isoformat(), session["reservation_id"]),
            )
            row = connection.execute(
                "SELECT * FROM charging_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            assert row is not None
            return charging_session_view(row)

    def get(self, session_id: str, user: Any) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM charging_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise not_found("Charging session was not found")
            reservation = connection.execute(
                "SELECT user_id FROM app_reservations WHERE id = ?", (row["reservation_id"],)
            ).fetchone()
        assert reservation is not None
        self._authorize(reservation, user)
        return charging_session_view(row)

    async def stop_active_for_release(self, reservation_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM charging_sessions WHERE reservation_id = ? AND status = 'ACTIVE'",
                (reservation_id,),
            ).fetchone()
            reservation = connection.execute(
                "SELECT user_id FROM app_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
        if row is not None and reservation is not None:
            await self.stop(
                row["session_id"],
                {"id": reservation["user_id"], "role": "ADMIN"},
            )
