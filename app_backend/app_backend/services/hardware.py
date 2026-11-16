from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.adapters.live import canonical_plate
from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.errors import AppError, conflict, forbidden, not_found


ACTIVE_STATUSES = {
    "RESERVED",
    "ATTENTION_REQUIRED",
    "BLOCKED",
    "CHECKED_IN",
    "CHARGING",
    "CHARGE_COMPLETED",
}


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class HardwareService:
    """Serializes safety-sensitive hardware writes and records their outcome."""

    def __init__(
        self, database: Database, adapter: EcuriousAdapter, settings: Settings
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings
        self._locks: dict[str, asyncio.Lock] = {}
        self._owner = f"hardware-{uuid.uuid4().hex}"
        self._maintenance_hashes: dict[str, str] = {}

    @property
    def live(self) -> bool:
        return self.adapter.mode == "live"

    def _lock(self, lock_id: str) -> asyncio.Lock:
        return self._locks.setdefault(lock_id, asyncio.Lock())

    def _acquire_lease(self, lock_id: str) -> bool:
        key = f"hardware_operation:{lock_id}"
        now = datetime.now(timezone.utc)
        lease_seconds = max(
            self.settings.sync_lease_seconds,
            int(self.settings.ecurious_timeout_seconds * 2) + 5,
            15,
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT lease_owner, lease_until FROM sync_state WHERE key = ?", (key,)
            ).fetchone()
            if row and row["lease_until"]:
                current = _as_utc(row["lease_until"])
                if current > now and row["lease_owner"] != self._owner:
                    return False
            connection.execute(
                """
                INSERT INTO sync_state(key, value, lease_owner, lease_until, updated_at)
                VALUES (?, '', ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET lease_owner = excluded.lease_owner,
                    lease_until = excluded.lease_until, updated_at = excluded.updated_at
                """,
                (key, self._owner, lease_until.isoformat(), now.isoformat()),
            )
        return True

    def _release_lease(self, lock_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sync_state SET lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE key = ? AND lease_owner = ?
                """,
                (now_iso(), f"hardware_operation:{lock_id}", self._owner),
            )

    def operation_in_progress(self, lock_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT lease_until FROM sync_state WHERE key = ?",
                (f"hardware_operation:{lock_id}",),
            ).fetchone()
        return bool(
            row
            and row["lease_until"]
            and _as_utc(row["lease_until"]) > datetime.now(timezone.utc)
        )

    async def _serialized(self, lock_id: str, operation):
        async with self._lock(lock_id):
            if not self._acquire_lease(lock_id):
                raise ProviderError(
                    409,
                    "HARDWARE_UPDATE_IN_PROGRESS",
                    "Another hardware operation is already in progress",
                )
            try:
                return await operation()
            finally:
                self._release_lease(lock_id)

    def _authorized_rows(
        self, lock_id: str, at: datetime | None = None
    ) -> list[Any]:
        instant = at or datetime.now(timezone.utc)
        grace = timedelta(minutes=self.settings.reservation_grace_minutes)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT app_reservations.*, vehicles.license_plate
                FROM app_reservations
                JOIN vehicles ON vehicles.id = app_reservations.vehicle_id
                WHERE bay_id = ? AND status NOT IN (
                    'COMPLETED','CANCELLED','EXPIRED',
                    'RELEASE_PENDING','RELEASE_FAILED','RELEASE_UNKNOWN'
                )
                ORDER BY start_time, created_at
                """,
                (lock_id,),
            ).fetchall()
        return [
            row
            for row in rows
            if _as_utc(row["start_time"]) - grace <= instant < _as_utc(row["end_time"])
        ]

    def _desired_whitelist(self, lock_id: str) -> tuple[list[Any], list[str], str]:
        rows = self._authorized_rows(lock_id)
        plates = list(
            dict.fromkeys(canonical_plate(row["license_plate"]) for row in rows)
        )
        if len(plates) > 10:
            raise conflict(
                "WHITELIST_CAPACITY_EXCEEDED",
                "The hardware whitelist supports at most 10 active plates",
            )
        digest = hashlib.sha256(
            json.dumps(plates, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return rows, plates, digest

    async def sync_whitelist(self, lock_id: str, *, force: bool = False) -> dict[str, Any]:
        if not self.live:
            return {"status": "NOT_REQUIRED", "plates": 0}

        async def operation() -> dict[str, Any]:
            rows, plates, desired_hash = self._desired_whitelist(lock_id)
            self._maintenance_hashes[lock_id] = desired_hash
            with self.database.connect() as connection:
                current = connection.execute(
                    "SELECT * FROM hardware_whitelist_state WHERE lock_id = ?", (lock_id,)
                ).fetchone()
            if (
                not force
                and current
                and current["applied_hash"] == desired_hash
                and current["sync_status"] == "SYNCED"
            ):
                self._mark_reservations_synced(lock_id, rows, now_iso())
                physical = await self.adapter.get_bay_status(lock_id)
                self.finalize_safe_releases(lock_id, physical)
                return {"status": "UNCHANGED", "plates": len(plates)}
            if (
                not force
                and current
                and current["desired_hash"] == desired_hash
                and current["sync_status"] == "FAILED"
            ):
                return {"status": "FAILED", "plates": len(plates)}
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO hardware_whitelist_state(
                        lock_id, desired_hash, sync_status, updated_at
                    ) VALUES (?, ?, 'PENDING', ?)
                    ON CONFLICT(lock_id) DO UPDATE SET desired_hash = excluded.desired_hash,
                        sync_status = 'PENDING', last_error = NULL, updated_at = excluded.updated_at
                    """,
                    (lock_id, desired_hash, now_iso()),
                )
            try:
                status = await self.adapter.get_bay_status(lock_id)
                if status.get("hardware_online") is False:
                    raise ProviderError(
                        503, "ECURIOUS_DEVICE_OFFLINE", "The physical parking lock is offline"
                    )
                if self._has_release_in_progress(lock_id) and status.get("occupancy") != "free":
                    raise conflict(
                        "RELEASE_NOT_SAFE",
                        "Release authorization cannot be changed without a confirmed free bay",
                    )
                result = await self.adapter.set_whitelist(lock_id, plates)
                if result.get("success") is False:
                    raise ProviderError(
                        502,
                        "HARDWARE_RESULT_UNKNOWN",
                        "The hardware did not confirm the whitelist update",
                    )
            except (ProviderError, AppError) as exc:
                code = getattr(exc, "code", getattr(exc, "error", "HARDWARE_SYNC_FAILED"))
                self._record_sync_failure(lock_id, str(code))
                raise
            timestamp = now_iso()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE hardware_whitelist_state SET applied_hash = ?, sync_status = 'SYNCED',
                        last_error = NULL, updated_at = ? WHERE lock_id = ?
                    """,
                    (desired_hash, timestamp, lock_id),
                )
                connection.execute(
                    """
                    UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
                    WHERE bay_id = ? AND alert_type = 'HARDWARE_SYNC_FAILED'
                      AND status != 'RESOLVED'
                    """,
                    (timestamp, lock_id),
                )
            self._mark_reservations_synced(lock_id, rows, timestamp)
            self.finalize_safe_releases(lock_id, status)
            return {
                "status": "SYNCED",
                "plates": len(plates),
                "command_accepted": bool(result.get("success", True)),
            }

        return await self._serialized(lock_id, operation)

    def _mark_reservations_synced(
        self, lock_id: str, active_rows: list[Any], timestamp: str
    ) -> None:
        active_ids = [row["id"] for row in active_rows]
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE app_reservations SET hardware_sync_status = 'SCHEDULED',
                    hardware_sync_error = NULL, provider_confirmed = 0
                WHERE bay_id = ? AND status NOT IN (
                    'COMPLETED','CANCELLED','EXPIRED',
                    'RELEASE_PENDING','RELEASE_FAILED','RELEASE_UNKNOWN'
                )
                """,
                (lock_id,),
            )
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                connection.execute(
                    f"""
                    UPDATE app_reservations SET hardware_sync_status = 'SYNCED',
                        hardware_sync_error = NULL, hardware_sync_at = ?, provider_confirmed = 1
                    WHERE id IN ({placeholders})
                    """,
                    (timestamp, *active_ids),
                )
                connection.execute(
                    f"""
                    UPDATE app_reservations SET status = 'RESERVED', attention_message = NULL
                    WHERE id IN ({placeholders}) AND status = 'ATTENTION_REQUIRED'
                      AND attention_message = ?
                    """,
                    (
                        *active_ids,
                        "Reservation saved, but the physical lock whitelist could not be updated",
                    ),
                )
            connection.execute(
                """
                UPDATE app_reservations SET hardware_sync_status = 'REMOVED',
                    hardware_sync_error = NULL, hardware_sync_at = ?, provider_confirmed = 1
                WHERE bay_id = ? AND status IN (
                    'COMPLETED','CANCELLED','EXPIRED',
                    'RELEASE_PENDING','RELEASE_FAILED','RELEASE_UNKNOWN'
                ) AND hardware_sync_status IN ('PENDING','FAILED','UNKNOWN')
                """,
                (timestamp, lock_id),
            )

    def _has_release_in_progress(self, lock_id: str) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT 1 FROM app_reservations WHERE bay_id = ?
                   AND status IN ('RELEASE_PENDING','RELEASE_FAILED','RELEASE_UNKNOWN')
                   LIMIT 1""",
                (lock_id,),
            ).fetchone() is not None

    def finalize_safe_releases(self, lock_id: str, physical: dict[str, Any]) -> int:
        """Finalize only after the whitelist and a free physical snapshot are confirmed."""
        if physical.get("hardware_online") is False or physical.get("occupancy") != "free":
            return 0
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                """SELECT id, release_operation_id FROM app_reservations
                   WHERE bay_id = ?
                     AND status IN ('RELEASE_PENDING','RELEASE_FAILED','RELEASE_UNKNOWN')
                     AND hardware_sync_status = 'REMOVED'""",
                (lock_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """UPDATE app_reservations SET status = 'COMPLETED',
                           ecurious_status = 'released', provider_confirmed = 1,
                           attention_message = NULL, hardware_sync_error = NULL,
                           updated_at = ? WHERE id = ?""",
                    (timestamp, row["id"]),
                )
                if row["release_operation_id"]:
                    connection.execute(
                        """UPDATE release_operations SET status = 'SUCCEEDED',
                               error_code = NULL, updated_at = ?, completed_at = ?
                           WHERE id = ?""",
                        (timestamp, timestamp, row["release_operation_id"]),
                    )
            return len(rows)

    def _record_sync_failure(self, lock_id: str, code: str) -> None:
        timestamp = now_iso()
        source_id = f"WHITELIST-{lock_id}-{int(datetime.now(timezone.utc).timestamp() // 60)}"
        with self.database.transaction(immediate=True) as connection:
            affected = connection.execute(
                """
                SELECT id FROM app_reservations WHERE bay_id = ?
                  AND (status NOT IN ('COMPLETED','CANCELLED','EXPIRED')
                       OR hardware_sync_status = 'PENDING')
                """,
                (lock_id,),
            ).fetchall()
            connection.execute(
                """
                UPDATE hardware_whitelist_state SET sync_status = 'FAILED', last_error = ?,
                    updated_at = ? WHERE lock_id = ?
                """,
                (code, timestamp, lock_id),
            )
            for row in affected:
                connection.execute(
                    """
                    UPDATE app_reservations SET hardware_sync_status = 'FAILED',
                        hardware_sync_error = ?, provider_confirmed = 0,
                        status = CASE WHEN status = 'RESERVED' THEN 'ATTENTION_REQUIRED' ELSE status END,
                        attention_message = ?, updated_at = ? WHERE id = ?
                    """,
                    (
                        code,
                        "Reservation saved, but the physical lock whitelist could not be updated",
                        timestamp,
                        row["id"],
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO alerts(
                    id, bay_id, reservation_id, source_event_id, alert_type,
                    status, message, created_at
                ) VALUES (?, ?, ?, ?, 'HARDWARE_SYNC_FAILED', 'OPEN', ?, ?)
                """,
                (
                    f"ALT-{uuid.uuid4().hex[:12].upper()}",
                    lock_id,
                    affected[0]["id"] if affected else None,
                    source_id,
                    "Physical lock whitelist synchronization failed",
                    timestamp,
                ),
            )

    async def reconcile_due(self, *, force: bool = False) -> dict[str, int]:
        if not self.live:
            return {"synced": 0, "failed": 0, "expired": 0, "review_required": 0}
        expired, reviews = await self._expire_due_safely()
        lock_ids = self._reservation_lock_ids()
        synced = failed = 0
        for lock_id in lock_ids:
            try:
                result = await self.sync_whitelist(lock_id, force=force)
                synced += int(result["status"] == "SYNCED")
            except (ProviderError, AppError):
                failed += 1
        return {
            "synced": synced,
            "failed": failed,
            "expired": expired,
            "review_required": reviews,
        }

    def prime_background_monitor(self) -> None:
        """Capture local desired state at startup without contacting hardware."""
        if not self.live:
            return
        for lock_id in self._reservation_lock_ids():
            _rows, _plates, desired_hash = self._desired_whitelist(lock_id)
            self._maintenance_hashes[lock_id] = desired_hash

    async def reconcile_transitions(self) -> dict[str, int]:
        """Write only when an authorization time boundary changes after startup."""
        if not self.live:
            return {"synced": 0, "failed": 0, "expired": 0, "review_required": 0}
        expired, reviews = await self._expire_due_safely()
        synced = failed = 0
        for lock_id in self._reservation_lock_ids():
            _rows, _plates, desired_hash = self._desired_whitelist(lock_id)
            previous = self._maintenance_hashes.get(lock_id)
            self._maintenance_hashes[lock_id] = desired_hash
            if previous is None or previous == desired_hash:
                continue
            try:
                result = await self.sync_whitelist(lock_id)
                synced += int(result["status"] == "SYNCED")
            except (ProviderError, AppError):
                failed += 1
        return {
            "synced": synced,
            "failed": failed,
            "expired": expired,
            "review_required": reviews,
        }

    async def _expire_due_safely(
        self, *, at: datetime | None = None
    ) -> tuple[int, int]:
        """Expire at start + grace only after a fresh, safe physical snapshot."""
        instant = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        grace = timedelta(minutes=self.settings.reservation_grace_minutes)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id, bay_id, start_time FROM app_reservations
                   WHERE status IN ('RESERVED','ATTENTION_REQUIRED')
                   ORDER BY start_time"""
            ).fetchall()
        due = [row for row in rows if _as_utc(row["start_time"]) + grace <= instant]
        expired = reviews = 0
        for row in due:
            try:
                physical = await self.adapter.get_bay_status(row["bay_id"])
            except (ProviderError, AppError) as exc:
                code = getattr(exc, "code", getattr(exc, "error", "HARDWARE_STATE_UNKNOWN"))
                self._mark_no_show_review(row["id"], row["bay_id"], str(code))
                reviews += 1
                continue
            if physical.get("hardware_online") is False or physical.get("occupancy") != "free":
                self._mark_no_show_review(
                    row["id"], row["bay_id"], "HARDWARE_STATE_NOT_SAFE"
                )
                reviews += 1
                continue
            with self.database.transaction(immediate=True) as connection:
                changed = connection.execute(
                    """UPDATE app_reservations
                       SET status = 'EXPIRED', ecurious_status = 'expired',
                           hardware_sync_status = 'PENDING', hardware_sync_error = NULL,
                           attention_message = NULL, updated_at = ?
                       WHERE id = ? AND status IN ('RESERVED','ATTENTION_REQUIRED')""",
                    (now_iso(), row["id"]),
                ).rowcount
            expired += int(bool(changed))
        return expired, reviews

    def _mark_no_show_review(self, reservation_id: str, lock_id: str, code: str) -> None:
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE app_reservations
                   SET status = 'ATTENTION_REQUIRED', hardware_sync_status = 'REVIEW_REQUIRED',
                       hardware_sync_error = ?, attention_message = ?, updated_at = ?
                   WHERE id = ? AND status IN ('RESERVED','ATTENTION_REQUIRED')""",
                (
                    code,
                    "No-show deadline passed, but physical occupancy must be reviewed",
                    timestamp,
                    reservation_id,
                ),
            ).rowcount
            if changed:
                connection.execute(
                    """INSERT OR IGNORE INTO alerts(
                           id, bay_id, reservation_id, source_event_id, alert_type,
                           status, message, created_at
                       ) VALUES (?, ?, ?, ?, 'NO_SHOW_REVIEW', 'OPEN', ?, ?)""",
                    (
                        f"ALT-{uuid.uuid4().hex[:12].upper()}",
                        lock_id,
                        reservation_id,
                        f"NO-SHOW-REVIEW-{reservation_id}",
                        "No-show deadline passed without a safe physical state confirmation",
                        timestamp,
                    ),
                )

    def _reservation_lock_ids(self) -> list[str]:
        with self.database.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT bay_id FROM app_reservations"
                ).fetchall()
            ]

    def _mapped(self, lock_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM bay_mappings WHERE bay_id = ?", (lock_id,)
            ).fetchone()
        if row is None:
            raise not_found("Mapped parking lock was not found")

    def _driver_reservation(self, lock_id: str, user_id: str) -> Any:
        now = datetime.now(timezone.utc)
        grace = timedelta(minutes=self.settings.reservation_grace_minutes)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM app_reservations WHERE bay_id = ? AND user_id = ?
                  AND status NOT IN ('COMPLETED','CANCELLED','EXPIRED')
                ORDER BY start_time
                """,
                (lock_id, user_id),
            ).fetchall()
        return next(
            (
                row
                for row in rows
                if _as_utc(row["start_time"]) - grace <= now < _as_utc(row["end_time"])
            ),
            None,
        )

    async def manual_unlock(self, lock_id: str, user: Any) -> dict[str, Any]:
        if not self.live:
            raise ProviderError(501, "LIVE_HARDWARE_REQUIRED", "Manual unlock is available only in live mode")
        self._mapped(lock_id)
        reservation = self._driver_reservation(lock_id, user["id"])
        if user["role"] == "ADMIN" and reservation is None:
            active = self._authorized_rows(lock_id)
            reservation = active[0] if active else None
        if user["role"] != "ADMIN" and reservation is None:
            self._record_command(
                f"CMD-{uuid.uuid4().hex[:12].upper()}",
                lock_id,
                "UNLOCK",
                user["id"],
                None,
                "DENIED",
                "No currently valid reservation",
                now_iso(),
            )
            raise forbidden("A currently valid reservation is required to unlock this bay")

        async def operation() -> dict[str, Any]:
            command_id = f"CMD-{uuid.uuid4().hex[:12].upper()}"
            requested_at = now_iso()
            try:
                status = await self.adapter.get_bay_status(lock_id)
                if status.get("hardware_online") is False:
                    raise ProviderError(503, "ECURIOUS_DEVICE_OFFLINE", "The physical parking lock is offline")
                result = await self.adapter.unlock(lock_id)
                outcome = "ACCEPTED" if result.get("command_accepted") else "REJECTED"
                message = "Command accepted; physical movement awaits a status event"
            except ProviderError as exc:
                self._record_command(
                    command_id, lock_id, "UNLOCK", user["id"], reservation, "FAILED", exc.code, requested_at
                )
                raise
            self._record_command(
                command_id, lock_id, "UNLOCK", user["id"], reservation, outcome, message, requested_at
            )
            return {
                "command_id": command_id,
                "lock_id": lock_id,
                "command_status": outcome,
                "physical_state": "pending",
                "reservation_id": reservation["id"] if reservation else None,
            }

        return await self._serialized(lock_id, operation)

    async def admin_lock(self, lock_id: str, user: Any) -> dict[str, Any]:
        if not self.live:
            raise ProviderError(501, "LIVE_HARDWARE_REQUIRED", "Lock control is available only in live mode")
        if user["role"] != "ADMIN":
            raise forbidden("Administrator access is required")
        self._mapped(lock_id)

        async def operation() -> dict[str, Any]:
            command_id = f"CMD-{uuid.uuid4().hex[:12].upper()}"
            requested_at = now_iso()
            status = await self.adapter.get_bay_status(lock_id)
            if status.get("hardware_online") is False:
                raise ProviderError(503, "ECURIOUS_DEVICE_OFFLINE", "The physical parking lock is offline")
            if status["occupancy"] == "occupied":
                raise conflict(
                    "LOCK_OCCUPIED",
                    "The barrier cannot be raised while the bay is occupied",
                )
            try:
                result = await self.adapter.lock(lock_id)
            except ProviderError as exc:
                self._record_command(
                    command_id, lock_id, "LOCK", user["id"], None, "FAILED", exc.code, requested_at
                )
                raise
            outcome = "ACCEPTED" if result.get("command_accepted") else "REJECTED"
            self._record_command(
                command_id,
                lock_id,
                "LOCK",
                user["id"],
                None,
                outcome,
                "Command accepted; physical movement awaits a status event",
                requested_at,
            )
            return {
                "command_id": command_id,
                "lock_id": lock_id,
                "command_status": outcome,
                "physical_state": "pending",
            }

        return await self._serialized(lock_id, operation)

    async def departure_lock(
        self, lock_id: str, user: Any, reservation: Any
    ) -> dict[str, Any]:
        """Raise the lock for a completed departure, never on an occupied bay."""
        if not self.live:
            raise ProviderError(
                501,
                "LIVE_HARDWARE_REQUIRED",
                "Departure lock control is available only in live mode",
            )
        if user["role"] != "ADMIN" and reservation["user_id"] != user["id"]:
            raise forbidden("Drivers can only confirm departure for their own reservation")
        self._mapped(lock_id)

        def existing_command() -> Any:
            with self.database.connect() as connection:
                return connection.execute(
                    """SELECT * FROM hardware_commands
                       WHERE lock_id = ? AND reservation_id = ? AND command = 'LOCK'
                         AND result_status IN ('ACCEPTED','CONFIRMED')
                       ORDER BY requested_at DESC LIMIT 1""",
                    (lock_id, reservation["id"]),
                ).fetchone()

        existing = existing_command()
        if existing:
            return {
                "command_id": existing["id"],
                "lock_id": lock_id,
                "command_status": existing["result_status"],
                "physical_state": (
                    "confirmed" if existing["result_status"] == "CONFIRMED" else "pending"
                ),
                "reservation_id": reservation["id"],
            }

        async def operation() -> dict[str, Any]:
            duplicate = existing_command()
            if duplicate:
                return {
                    "command_id": duplicate["id"],
                    "lock_id": lock_id,
                    "command_status": duplicate["result_status"],
                    "physical_state": (
                        "confirmed"
                        if duplicate["result_status"] == "CONFIRMED"
                        else "pending"
                    ),
                    "reservation_id": reservation["id"],
                }
            command_id = f"CMD-{uuid.uuid4().hex[:12].upper()}"
            requested_at = now_iso()
            status = await self.adapter.get_bay_status(lock_id)
            if status.get("hardware_online") is not True:
                raise ProviderError(
                    503,
                    "ECURIOUS_DEVICE_OFFLINE",
                    "Departure cannot be confirmed while the physical lock is offline",
                )
            if status.get("occupancy") != "free":
                raise conflict(
                    "DEPARTURE_NOT_CONFIRMED_FREE",
                    "The parking lock cannot rise until the bay sensor confirms it is free",
                )
            try:
                result = await self.adapter.lock(lock_id)
            except ProviderError as exc:
                self._record_command(
                    command_id,
                    lock_id,
                    "LOCK",
                    user["id"],
                    reservation,
                    "FAILED",
                    exc.code,
                    requested_at,
                )
                raise
            outcome = "ACCEPTED" if result.get("command_accepted") else "REJECTED"
            self._record_command(
                command_id,
                lock_id,
                "LOCK",
                user["id"],
                reservation,
                outcome,
                "Departure confirmed; command accepted and physical movement awaits a status event",
                requested_at,
            )
            return {
                "command_id": command_id,
                "lock_id": lock_id,
                "command_status": outcome,
                "physical_state": "pending",
                "reservation_id": reservation["id"],
            }

        return await self._serialized(lock_id, operation)

    async def set_keep_state(self, lock_id: str, state: str, user: Any) -> dict[str, Any]:
        if not self.live:
            raise ProviderError(501, "LIVE_HARDWARE_REQUIRED", "Keep-state control is available only in live mode")
        if user["role"] != "ADMIN":
            raise forbidden("Administrator access is required")
        if state not in {"normal", "keep_open", "keep_closed"}:
            raise AppError(422, "INVALID_KEEP_STATE", "state must be normal, keep_open, or keep_closed")

        async def operation() -> dict[str, Any]:
            status = await self.adapter.get_bay_status(lock_id)
            if status.get("hardware_online") is False:
                raise ProviderError(503, "ECURIOUS_DEVICE_OFFLINE", "The physical parking lock is offline")
            if state == "keep_closed" and status["occupancy"] == "occupied":
                raise conflict("LOCK_OCCUPIED", "keep_closed is unsafe while the bay is occupied")
            result = await self.adapter.set_keep_state(lock_id, state)
            return {
                "lock_id": lock_id,
                "state": state,
                "command_status": "ACCEPTED" if result.get("success") else "REJECTED",
                "physical_state": "pending",
            }

        return await self._serialized(lock_id, operation)

    def _record_command(
        self,
        command_id: str,
        lock_id: str,
        command: str,
        user_id: str,
        reservation: Any,
        status: str,
        message: str,
        requested_at: str,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO hardware_commands(
                    id, lock_id, command, requested_by_user_id, reservation_id,
                    result_status, result_message, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    lock_id,
                    command,
                    user_id,
                    reservation["id"] if reservation else None,
                    status,
                    message,
                    requested_at,
                ),
            )
