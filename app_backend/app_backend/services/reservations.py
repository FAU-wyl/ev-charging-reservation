from __future__ import annotations

import sqlite3
import uuid
from datetime import timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.database import Database, now_iso
from app_backend.errors import AppError, conflict, forbidden, not_found
from app_backend.services.charging import ChargingService
from app_backend.schemas import ReservationCreate
from app_backend.services.hardware import HardwareService


RESERVATION_SELECT = """
SELECT app_reservations.*, users.email AS user_email,
       vehicles.nickname AS vehicle_nickname, vehicles.license_plate,
       stations.name AS station_name,
       charging_sessions.session_id AS charging_session_id,
       charging_sessions.started_at AS charging_started_at,
       charging_sessions.ended_at AS charging_ended_at,
       charging_sessions.duration_seconds AS charging_duration_seconds,
       charging_sessions.estimated_cost_eur AS charging_estimated_cost_eur,
       charging_sessions.status AS charging_session_status,
       charging_sessions.pricing_model AS charging_pricing_model,
       charging_sessions.rate_eur_per_hour AS charging_rate_eur_per_hour,
       charging_sessions.is_simulated AS charging_is_simulated
FROM app_reservations
JOIN users ON users.id = app_reservations.user_id
JOIN vehicles ON vehicles.id = app_reservations.vehicle_id
JOIN stations ON stations.id = app_reservations.station_id
LEFT JOIN charging_sessions ON charging_sessions.session_id = (
    SELECT session_id FROM charging_sessions AS latest_session
    WHERE latest_session.reservation_id = app_reservations.id
    ORDER BY latest_session.started_at DESC LIMIT 1
)
"""


def reservation_view(row: sqlite3.Row) -> dict[str, Any]:
    charging_session = None
    if row["charging_session_id"]:
        charging_session = {
            "session_id": row["charging_session_id"],
            "reservation_id": row["id"],
            "user_id": row["user_id"],
            "station_id": row["station_id"],
            "bay_id": row["bay_id"],
            "started_at": row["charging_started_at"],
            "ended_at": row["charging_ended_at"],
            "duration_seconds": row["charging_duration_seconds"],
            "pricing_model": row["charging_pricing_model"],
            "rate_eur_per_hour": row["charging_rate_eur_per_hour"],
            "estimated_cost_eur": row["charging_estimated_cost_eur"],
            "status": row["charging_session_status"],
            "is_simulated": bool(row["charging_is_simulated"]),
        }
    return {
        "id": row["id"],
        "ecurious_reservation_id": row["ecurious_reservation_id"],
        "user_id": row["user_id"],
        "user_email": row["user_email"],
        "vehicle_id": row["vehicle_id"],
        "vehicle_nickname": row["vehicle_nickname"],
        "license_plate": row["license_plate"],
        "station_id": row["station_id"],
        "station_name": row["station_name"],
        "bay_id": row["bay_id"],
        "status": row["status"],
        "ecurious_status": row["ecurious_status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "attention_message": row["attention_message"],
        "simulated_charging": bool(row["simulated_charging"]),
        "provider_confirmed": bool(row["provider_confirmed"]),
        "hardware_sync_status": row["hardware_sync_status"],
        "hardware_sync_error": row["hardware_sync_error"],
        "hardware_sync_at": row["hardware_sync_at"],
        "charging_session": charging_session,
    }


def app_status_from_provider(provider_status: str, current: str | None = None) -> str:
    if provider_status == "reserved":
        return current if current in {"BLOCKED", "ATTENTION_REQUIRED"} else "RESERVED"
    if provider_status == "checked_in":
        return current if current in {"CHARGING", "COMPLETED"} else "CHECKED_IN"
    return {
        "released": "COMPLETED",
        "expired": "EXPIRED",
        "cancelled": "CANCELLED",
    }.get(provider_status, current or "UNKNOWN")


class ReservationService:
    def __init__(
        self,
        database: Database,
        adapter: EcuriousAdapter,
        hardware: HardwareService | None = None,
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.hardware = hardware

    async def create(
        self,
        user_id: str,
        payload: ReservationCreate,
        client_request_key: str,
    ) -> dict[str, Any]:
        key = client_request_key.strip()
        if not key or len(key) > 128:
            raise conflict("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 1 to 128 characters")
        with self.database.connect() as connection:
            existing = connection.execute(
                RESERVATION_SELECT
                + " WHERE app_reservations.user_id = ? AND client_request_key = ?",
                (user_id, key),
            ).fetchone()
            if existing is not None:
                if (
                    self.adapter.mode == "live"
                    and self.hardware
                    and existing["hardware_sync_status"] == "FAILED"
                ):
                    await self.hardware.sync_whitelist(existing["bay_id"], force=True)
                    return self.get(existing["id"], user_id, "DRIVER")
                return reservation_view(existing)
            vehicle = connection.execute(
                "SELECT * FROM vehicles WHERE id = ? AND user_id = ?",
                (payload.vehicle_id, user_id),
            ).fetchone()
            if vehicle is None:
                raise not_found("Vehicle was not found")
            mapping = connection.execute(
                "SELECT * FROM bay_mappings WHERE bay_id = ? AND station_id = ?",
                (payload.bay_id, payload.station_id),
            ).fetchone()
            if mapping is None:
                raise conflict(
                    "BAY_NOT_MAPPED",
                    "The selected station is not mapped to this Ecurious bay",
                )

        if self.adapter.mode == "live":
            return await self._create_live(user_id, payload, key, vehicle)

        app_id = f"APP-RES-{uuid.uuid4().hex[:12].upper()}"
        provider = await self.adapter.create_reservation(
            bay_id=payload.bay_id,
            license_plate=vehicle["license_plate"],
            start_time=payload.start_time,
            end_time=payload.end_time,
            idempotency_key=app_id,
        )
        timestamp = now_iso()
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO app_reservations(
                        id, ecurious_reservation_id, user_id, vehicle_id, station_id,
                        bay_id, status, ecurious_status, start_time, end_time,
                        created_at, updated_at, provider_confirmed, client_request_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        app_id,
                        provider["reservation_id"],
                        user_id,
                        payload.vehicle_id,
                        payload.station_id,
                        payload.bay_id,
                        app_status_from_provider(provider["status"]),
                        provider["status"],
                        payload.start_time.astimezone(timezone.utc).isoformat(),
                        payload.end_time.astimezone(timezone.utc).isoformat(),
                        timestamp,
                        timestamp,
                        key,
                    ),
                )
                row = connection.execute(
                    RESERVATION_SELECT + " WHERE app_reservations.id = ?", (app_id,)
                ).fetchone()
                assert row is not None
                return reservation_view(row)
        except sqlite3.IntegrityError:
            with self.database.connect() as connection:
                existing = connection.execute(
                    RESERVATION_SELECT
                    + " WHERE app_reservations.user_id = ? AND client_request_key = ?",
                    (user_id, key),
                ).fetchone()
            if existing is not None:
                return reservation_view(existing)
            raise

    async def _create_live(
        self, user_id: str, payload: ReservationCreate, key: str, vehicle: sqlite3.Row
    ) -> dict[str, Any]:
        if self.hardware is None:
            raise RuntimeError("Live reservation mode requires HardwareService")
        app_id = f"APP-RES-{uuid.uuid4().hex[:12].upper()}"
        provider_id = f"LIVE-{app_id}"
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            conflict_row = connection.execute(
                """
                SELECT id FROM app_reservations
                WHERE bay_id = ? AND status NOT IN ('COMPLETED','CANCELLED','EXPIRED')
                  AND start_time < ? AND end_time > ?
                LIMIT 1
                """,
                (
                    payload.bay_id,
                    payload.end_time.astimezone(timezone.utc).isoformat(),
                    payload.start_time.astimezone(timezone.utc).isoformat(),
                ),
            ).fetchone()
            if conflict_row:
                raise conflict("BAY_ALREADY_RESERVED", "Time slot unavailable")
            connection.execute(
                """
                INSERT INTO app_reservations(
                    id, ecurious_reservation_id, user_id, vehicle_id, station_id,
                    bay_id, status, ecurious_status, start_time, end_time,
                    created_at, updated_at, provider_confirmed, hardware_sync_status,
                    client_request_key
                ) VALUES (?, ?, ?, ?, ?, ?, 'RESERVED', 'reserved', ?, ?, ?, ?, 0, 'PENDING', ?)
                """,
                (
                    app_id,
                    provider_id,
                    user_id,
                    payload.vehicle_id,
                    payload.station_id,
                    payload.bay_id,
                    payload.start_time.astimezone(timezone.utc).isoformat(),
                    payload.end_time.astimezone(timezone.utc).isoformat(),
                    timestamp,
                    timestamp,
                    key,
                ),
            )
        await self.hardware.sync_whitelist(payload.bay_id)
        return self.get(app_id, user_id, "DRIVER")

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                RESERVATION_SELECT
                + " WHERE app_reservations.user_id = ? ORDER BY start_time DESC",
                (user_id,),
            ).fetchall()
        return [reservation_view(row) for row in rows]

    def get(self, reservation_id: str, user_id: str, role: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                RESERVATION_SELECT + " WHERE app_reservations.id = ?", (reservation_id,)
            ).fetchone()
        if row is None:
            raise not_found("Reservation was not found")
        if role != "ADMIN" and row["user_id"] != user_id:
            raise forbidden("Drivers can only access their own reservations")
        return reservation_view(row)

    async def cancel(self, reservation_id: str, user_id: str) -> dict[str, Any]:
        current = self.get(reservation_id, user_id, "DRIVER")
        if current["status"] not in {"RESERVED", "ATTENTION_REQUIRED", "BLOCKED"}:
            raise conflict("CANCELLATION_NOT_ALLOWED", "Reservation can no longer be cancelled")
        if self.adapter.mode == "live":
            if self.hardware is None:
                raise RuntimeError("Live reservation mode requires HardwareService")
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE app_reservations SET status = 'CANCELLED',
                        ecurious_status = 'cancelled', cancellation_reason = 'user_cancelled',
                        hardware_sync_status = 'PENDING', updated_at = ? WHERE id = ?
                    """,
                    (now_iso(), reservation_id),
                )
            try:
                await self.hardware.sync_whitelist(current["bay_id"], force=True)
            except Exception:
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(
                        """
                        UPDATE app_reservations SET hardware_sync_status = 'FAILED',
                            hardware_sync_error = 'WHITELIST_REMOVAL_FAILED' WHERE id = ?
                        """,
                        (reservation_id,),
                    )
                raise
            return self.get(reservation_id, user_id, "DRIVER")
        provider = await self.adapter.release_reservation(
            current["ecurious_reservation_id"], "user_cancelled"
        )
        return self._update_from_provider(
            reservation_id,
            provider,
            forced_status="CANCELLED",
            cancellation_reason="user_cancelled",
        )

    async def confirm_departure(
        self, reservation_id: str, user: Any
    ) -> dict[str, Any]:
        current = self.get(reservation_id, user["id"], user["role"])
        if current["status"] not in {
            "CHARGE_COMPLETED",
            "RELEASE_PENDING",
            "RELEASE_FAILED",
            "RELEASE_UNKNOWN",
            "COMPLETED",
        }:
            raise conflict(
                "DEPARTURE_REQUIRES_STOP_CHARGING",
                "Stop charging before confirming that the vehicle has left",
            )

        if self.adapter.mode != "live":
            released = await self.admin_release(reservation_id)
            return {"reservation": released, "hardware_command": None}
        if self.hardware is None:
            raise RuntimeError("Live departure requires HardwareService")

        # Release removes the departing plate only after a fresh free-bay read.
        # The dedicated departure command then repeats that safety read before
        # asking the physical parking lock to rise.
        released = await self.admin_release(reservation_id)
        command = await self.hardware.departure_lock(
            current["bay_id"], user, current
        )
        return {"reservation": released, "hardware_command": command}

    async def admin_release(self, reservation_id: str) -> dict[str, Any]:
        current = self.get(reservation_id, "", "ADMIN")
        if self.adapter.mode == "live":
            if self.hardware is None:
                raise RuntimeError("Live reservation mode requires HardwareService")
            if current["status"] == "COMPLETED":
                return current

            # A prior remote write may have succeeded before a local commit failed.
            # Re-check physical state before safely finalising that persisted pending state.
            if (
                current["status"] == "RELEASE_PENDING"
                and current["hardware_sync_status"] == "REMOVED"
            ):
                status = await self.adapter.get_bay_status(current["bay_id"])
                if status.get("occupancy") == "free" and status.get("hardware_online") is not False:
                    self.hardware.finalize_safe_releases(current["bay_id"], status)
                return self.get(reservation_id, "", "ADMIN")
            if current["status"] == "RELEASE_PENDING":
                if self.hardware.operation_in_progress(current["bay_id"]):
                    return current
                physical = await self.adapter.get_bay_status(current["bay_id"])
                if physical.get("hardware_online") is False or physical.get("occupancy") != "free":
                    return current
                with self.database.connect() as connection:
                    operation = connection.execute(
                        "SELECT release_operation_id FROM app_reservations WHERE id = ?",
                        (reservation_id,),
                    ).fetchone()
                try:
                    await self.hardware.sync_whitelist(current["bay_id"], force=True)
                except (ProviderError, AppError) as exc:
                    self._mark_release_failure(
                        reservation_id,
                        operation["release_operation_id"] if operation else None,
                        exc,
                    )
                    raise
                return self.get(reservation_id, "", "ADMIN")

            physical = await self.adapter.get_bay_status(current["bay_id"])
            if physical.get("hardware_online") is False:
                raise AppError(
                    503,
                    "ECURIOUS_DEVICE_OFFLINE",
                    "Release cannot be confirmed while the physical lock is offline",
                )
            if physical.get("occupancy") != "free":
                raise conflict(
                    "RELEASE_NOT_SAFE",
                    "Release requires a confirmed free bay; no physical lift command was sent",
                )

            await ChargingService(
                self.database, self.adapter, self.hardware.settings
            ).stop_active_for_release(reservation_id)
            operation_id = f"REL-{uuid.uuid4().hex[:16].upper()}"
            timestamp = now_iso()
            with self.database.transaction(immediate=True) as connection:
                latest = connection.execute(
                    "SELECT status FROM app_reservations WHERE id = ?", (reservation_id,)
                ).fetchone()
                assert latest is not None
                if latest["status"] == "COMPLETED":
                    return self.get(reservation_id, "", "ADMIN")
                if latest["status"] == "RELEASE_PENDING":
                    return self.get(reservation_id, "", "ADMIN")
                changed = connection.execute(
                    """
                    UPDATE app_reservations SET status = 'RELEASE_PENDING',
                        ecurious_status = 'release_pending', hardware_sync_status = 'PENDING',
                        hardware_sync_error = NULL, release_operation_id = ?,
                        release_requested_at = ?, updated_at = ?
                    WHERE id = ? AND status NOT IN ('COMPLETED','CANCELLED','EXPIRED','RELEASE_PENDING')
                    """,
                    (operation_id, timestamp, timestamp, reservation_id),
                )
                if not changed.rowcount:
                    return self.get(reservation_id, "", "ADMIN")
                connection.execute(
                    """INSERT INTO release_operations(
                           id, reservation_id, lock_id, previous_status, status,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
                    (
                        operation_id,
                        reservation_id,
                        current["bay_id"],
                        current["status"],
                        timestamp,
                        timestamp,
                    ),
                )
            try:
                await self.hardware.sync_whitelist(current["bay_id"], force=True)
            except (ProviderError, AppError) as exc:
                self._mark_release_failure(reservation_id, operation_id, exc)
                raise
            return self.get(reservation_id, "", "ADMIN")
        if self.hardware is not None:
            await ChargingService(
                self.database, self.adapter, self.hardware.settings
            ).stop_active_for_release(reservation_id)
        provider = await self.adapter.release_reservation(
            current["ecurious_reservation_id"], "operator_action"
        )
        return self._update_from_provider(reservation_id, provider, forced_status="COMPLETED")

    async def admin_retry_hardware_authorization(
        self, reservation_id: str
    ) -> dict[str, Any]:
        current = self.get(reservation_id, "", "ADMIN")
        if self.adapter.mode != "live" or self.hardware is None:
            raise conflict(
                "LIVE_HARDWARE_REQUIRED",
                "Hardware authorization retry is available only in live mode",
            )
        if not (
            current["status"] == "ATTENTION_REQUIRED"
            and current["hardware_sync_status"] == "FAILED"
        ):
            raise conflict(
                "HARDWARE_RETRY_NOT_REQUIRED",
                "Only a reservation with failed hardware synchronization can be retried",
            )

        await self.hardware.sync_whitelist(current["bay_id"], force=True)
        updated = self.get(reservation_id, "", "ADMIN")
        if not (
            updated["status"] == "RESERVED"
            and updated["hardware_sync_status"] == "SYNCED"
            and updated["provider_confirmed"]
        ):
            raise conflict(
                "RESERVATION_OUTSIDE_AUTHORIZATION_WINDOW",
                "The reservation is not currently inside its hardware authorization window",
            )
        return updated

    def _mark_release_failure(
        self, reservation_id: str, operation_id: str | None, exc: Exception
    ) -> None:
        code = getattr(exc, "code", getattr(exc, "error", "HARDWARE_SYNC_FAILED"))
        release_status = (
            "RELEASE_UNKNOWN"
            if code in {"ECURIOUS_TIMEOUT", "HARDWARE_RESULT_UNKNOWN"}
            else "RELEASE_FAILED"
        )
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE app_reservations
                   SET status = ?, ecurious_status = ?, hardware_sync_status = ?,
                       hardware_sync_error = ?, attention_message = ?, updated_at = ?
                   WHERE id = ? AND status = 'RELEASE_PENDING'""",
                (
                    release_status,
                    release_status.lower(),
                    "UNKNOWN" if release_status == "RELEASE_UNKNOWN" else "FAILED",
                    code,
                    "Release requires hardware reconciliation before completion",
                    timestamp,
                    reservation_id,
                ),
            )
            if operation_id:
                connection.execute(
                    """UPDATE release_operations SET status = ?, error_code = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        "UNKNOWN" if release_status == "RELEASE_UNKNOWN" else "FAILED",
                        code,
                        timestamp,
                        operation_id,
                    ),
                )

    async def refresh_active(self) -> int:
        if self.adapter.mode == "live":
            if self.hardware is None:
                return 0
            result = await self.hardware.reconcile_due()
            return result["synced"] + result["expired"]
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, ecurious_reservation_id FROM app_reservations
                WHERE status NOT IN ('COMPLETED', 'CANCELLED', 'EXPIRED')
                """
            ).fetchall()
        refreshed = 0
        for row in rows:
            provider = await self.adapter.get_reservation(row["ecurious_reservation_id"])
            self._update_from_provider(row["id"], provider)
            refreshed += 1
        return refreshed

    def _update_from_provider(
        self,
        reservation_id: str,
        provider: dict[str, Any],
        *,
        forced_status: str | None = None,
        cancellation_reason: str | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT status FROM app_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if current is None:
                raise not_found("Reservation was not found")
            status = forced_status or app_status_from_provider(
                provider["status"], current["status"]
            )
            connection.execute(
                """
                UPDATE app_reservations
                SET status = ?, ecurious_status = ?, updated_at = ?,
                    cancellation_reason = COALESCE(?, cancellation_reason),
                    attention_message = CASE WHEN ? IN ('CHECKED_IN','CHARGING','COMPLETED')
                                             THEN NULL ELSE attention_message END
                WHERE id = ?
                """,
                (
                    status,
                    provider["status"],
                    now_iso(),
                    cancellation_reason,
                    status,
                    reservation_id,
                ),
            )
            row = connection.execute(
                RESERVATION_SELECT + " WHERE app_reservations.id = ?", (reservation_id,)
            ).fetchone()
            assert row is not None
            return reservation_view(row)
