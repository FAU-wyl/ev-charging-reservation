from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from app.core.exceptions import conflict, not_found
from app.core.state_machine import (
    LifecycleState,
    PUBLIC_STATUS,
    TERMINAL_STATES,
    ensure_transition,
)
from app.db.database import Database
from app.schemas.models import CreateReservation, ReleaseReason
from app.services.common import db_datetime, public_reservation, utc_now
from app.services.event_service import insert_event


ACTIVE_LIFECYCLE_VALUES = tuple(state.value for state in LifecycleState if state not in TERMINAL_STATES)


def _fingerprint(payload: CreateReservation) -> str:
    normalized = {
        "bay_id": payload.bay_id,
        "license_plate": payload.license_plate,
        "start_time": db_datetime(payload.start_time),
        "end_time": db_datetime(payload.end_time),
    }
    raw = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


class ReservationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_bays(self) -> dict[str, list[dict[str, Any]]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT bay_id, reservation_supported, charger_reference FROM bays ORDER BY bay_id"
            ).fetchall()
        return {
            "bays": [
                {
                    "bay_id": row["bay_id"],
                    "reservation_supported": bool(row["reservation_supported"]),
                    "charger_reference": row["charger_reference"],
                }
                for row in rows
            ]
        }

    def get_bay_status(self, bay_id: str) -> dict[str, Any]:
        from app.services.common import public_bay_status

        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM bays WHERE bay_id = ?", (bay_id,)).fetchone()
        if row is None:
            raise not_found(f"Bay {bay_id}")
        return public_bay_status(row)

    def create(self, payload: CreateReservation, idempotency_key: str) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise conflict("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 1 to 128 characters")

        fingerprint = _fingerprint(payload)
        now = utc_now()
        reservation_id = f"RES-{uuid.uuid4().hex[:12].upper()}"
        active_placeholders = ",".join("?" for _ in ACTIVE_LIFECYCLE_VALUES)

        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["idempotency_fingerprint"] != fingerprint:
                    raise conflict(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Idempotency-Key was already used with a different request",
                    )
                return public_reservation(existing)

            bay = connection.execute(
                "SELECT * FROM bays WHERE bay_id = ?", (payload.bay_id,)
            ).fetchone()
            if bay is None:
                raise not_found(f"Bay {payload.bay_id}")
            if not bay["reservation_supported"]:
                raise conflict("RESERVATION_NOT_SUPPORTED", "Bay does not support reservations")
            if bay["occupancy"] == "occupied":
                raise conflict("BAY_OCCUPIED", "Bay is currently physically occupied")
            if bay["protection"] == "unknown":
                raise conflict("HARDWARE_STATE_UNKNOWN", "Bay protection state is unknown")

            overlap = connection.execute(
                f"""
                SELECT reservation_id FROM reservations
                WHERE bay_id = ?
                  AND lifecycle_state IN ({active_placeholders})
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
                """,
                (
                    payload.bay_id,
                    *ACTIVE_LIFECYCLE_VALUES,
                    db_datetime(payload.end_time),
                    db_datetime(payload.start_time),
                ),
            ).fetchone()
            if overlap is not None:
                raise conflict("BAY_ALREADY_RESERVED", "Requested time slot is unavailable")

            created_at = db_datetime(now)
            connection.execute(
                """
                INSERT INTO reservations(
                    reservation_id, bay_id, license_plate, start_time, end_time,
                    public_status, lifecycle_state, hardware_status, created_at,
                    updated_at, idempotency_key, idempotency_fingerprint
                ) VALUES (?, ?, ?, ?, ?, 'reserved', 'RESERVED', 'protected', ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    payload.bay_id,
                    payload.license_plate,
                    db_datetime(payload.start_time),
                    db_datetime(payload.end_time),
                    created_at,
                    created_at,
                    key,
                    fingerprint,
                ),
            )
            connection.execute(
                """
                UPDATE bays
                SET protection = 'protected', active_reservation_id = ?, last_seen_at = ?
                WHERE bay_id = ?
                """,
                (reservation_id, created_at, payload.bay_id),
            )
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            assert row is not None
            return public_reservation(row)

    def get(self, reservation_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
        if row is None:
            raise not_found(f"Reservation {reservation_id}")
        return public_reservation(row)

    def release(self, reservation_id: str, reason: ReleaseReason) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise not_found(f"Reservation {reservation_id}")

            current = LifecycleState(row["lifecycle_state"])
            if current in TERMINAL_STATES:
                return public_reservation(row)

            if reason is ReleaseReason.USER_CANCELLED:
                target = LifecycleState.CANCELLED
                if current is not LifecycleState.RESERVED:
                    raise conflict(
                        "RELEASE_NOT_ALLOWED",
                        "user_cancelled is only valid before check-in",
                    )
            elif reason is ReleaseReason.NO_SHOW:
                target = LifecycleState.EXPIRED
                if current is not LifecycleState.RESERVED:
                    raise conflict(
                        "RELEASE_NOT_ALLOWED", "no_show is only valid before check-in"
                    )
            else:
                target = LifecycleState.RELEASED

            ensure_transition(current.value, target)
            now_value = db_datetime(now)
            connection.execute(
                """
                UPDATE reservations
                SET lifecycle_state = ?, public_status = ?, hardware_status = 'released',
                    released_reason = ?, updated_at = ?
                WHERE reservation_id = ?
                """,
                (target.value, PUBLIC_STATUS[target], reason.value, now_value, reservation_id),
            )
            connection.execute(
                """
                UPDATE bays
                SET active_reservation_id = NULL, protection = 'released', charging = 0,
                    last_seen_at = ?
                WHERE bay_id = ? AND active_reservation_id = ?
                """,
                (now_value, row["bay_id"], reservation_id),
            )
            if target is LifecycleState.EXPIRED:
                insert_event(
                    connection,
                    event_type="reservation.expired",
                    bay_id=row["bay_id"],
                    timestamp=now,
                    outcome="expired",
                    reservation_id=reservation_id,
                )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            assert updated is not None
            return public_reservation(updated)

