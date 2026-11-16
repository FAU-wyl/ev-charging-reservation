from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.adapters.live import canonical_plate
from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.security import mask_plate
from app_backend.services.reservations import ReservationService


logger = logging.getLogger("ecurious_app.event_sync")


class EventSyncService:
    def __init__(
        self, database: Database, adapter: EcuriousAdapter, settings: Settings
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings
        self.owner = f"sync-{uuid.uuid4().hex}"
        self.reservations = ReservationService(database, adapter)

    async def sync_once(self) -> dict[str, Any]:
        cursor = self._acquire_lease()
        if cursor is False:
            return {"processed": 0, "cursor": None, "lease_acquired": False}
        try:
            page = await self.adapter.poll_events(cursor or None)
            processed = 0
            for event in page.get("events", []):
                if await self._process(event):
                    processed += 1
            next_cursor = page.get("next_cursor") or cursor
            self._save_cursor_and_release(next_cursor)
            return {
                "processed": processed,
                "cursor": next_cursor,
                "lease_acquired": True,
            }
        except Exception:
            self._release_lease()
            raise

    async def process_event(self, event: dict[str, Any]) -> bool:
        """Process one normalized polling or SSE event through the same dedupe path."""
        return await self._process(event)

    def _acquire_lease(self) -> str | bool:
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.settings.sync_lease_seconds)
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM sync_state WHERE key = 'ecurious_events'"
            ).fetchone()
            if row and row["lease_until"]:
                current_lease = datetime.fromisoformat(row["lease_until"])
                if current_lease > now and row["lease_owner"] != self.owner:
                    return False
            if row:
                cursor = row["value"] or ""
                connection.execute(
                    """
                    UPDATE sync_state SET lease_owner = ?, lease_until = ?, updated_at = ?
                    WHERE key = 'ecurious_events'
                    """,
                    (self.owner, lease_until.isoformat(), now.isoformat()),
                )
            else:
                cursor = ""
                connection.execute(
                    """
                    INSERT INTO sync_state(key, value, lease_owner, lease_until, updated_at)
                    VALUES ('ecurious_events', '', ?, ?, ?)
                    """,
                    (self.owner, lease_until.isoformat(), now.isoformat()),
                )
            return cursor

    def _save_cursor_and_release(self, cursor: str | None) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sync_state SET value = ?, lease_owner = NULL, lease_until = NULL,
                    updated_at = ?
                WHERE key = 'ecurious_events' AND lease_owner = ?
                """,
                (cursor or "", now_iso(), self.owner),
            )

    def _release_lease(self) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE sync_state SET lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE key = 'ecurious_events' AND lease_owner = ?
                """,
                (now_iso(), self.owner),
            )

    async def _process(self, event: dict[str, Any]) -> bool:
        event_time = self._event_time(event.get("timestamp"))
        stored_timestamp = event_time.isoformat() if event_time else now_iso()
        with self.database.transaction(immediate=True) as connection:
            claimed = connection.execute(
                """INSERT OR IGNORE INTO processed_events(
                       external_event_id, event_type, bay_id, reservation_id,
                       masked_plate, confidence, processing_result, timestamp, processed_at
                   ) VALUES (?, ?, ?, NULL, ?, ?, 'PROCESSING', ?, ?)""",
                (
                    event["event_id"],
                    event["type"],
                    event["bay_id"],
                    mask_plate(event.get("license_plate")),
                    event.get("confidence"),
                    stored_timestamp,
                    now_iso(),
                ),
            ).rowcount
            if not claimed:
                return False
            candidates = connection.execute(
                """
                SELECT app_reservations.*, vehicles.license_plate
                FROM app_reservations
                JOIN vehicles ON vehicles.id = app_reservations.vehicle_id
                WHERE bay_id = ?
                  AND status NOT IN ('COMPLETED', 'CANCELLED', 'EXPIRED')
                ORDER BY start_time, created_at
                """,
                (event["bay_id"],),
            ).fetchall()
        grace = timedelta(minutes=self.settings.reservation_grace_minutes)
        reservation = (
            next(
                (
                    row
                    for row in candidates
                    if self._row_time(row["start_time"]) - grace
                    <= event_time
                    < self._row_time(row["end_time"])
                ),
                None,
            )
            if event_time
            else None
        )

        event_type = event["type"]
        result = "RECORDED"
        reservation_id = reservation["id"] if reservation else None
        try:
            if event_time is None:
                result = "INVALID_TIMESTAMP_IGNORED"
            elif event_type == "plate.detected":
                result = await self._process_plate(event, reservation)
            elif event_type == "occupancy.changed":
                result = await self._process_occupancy(event, reservation)
            elif event_type == "reservation.expired":
                result = await self._process_expiry(event, reservation)
            elif event_type == "hardware.status_changed":
                result = self._process_hardware_status(event)
            elif event_type == "hardware.alarm":
                result = self._process_alarm(event, reservation)
        except Exception:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "DELETE FROM processed_events WHERE external_event_id = ? AND processing_result = 'PROCESSING'",
                    (event["event_id"],),
                )
            raise

        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE processed_events
                   SET reservation_id = ?, processing_result = ?, processed_at = ?
                   WHERE external_event_id = ?""",
                (reservation_id, result, now_iso(), event["event_id"]),
            )
        return True

    @staticmethod
    def _row_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _event_time(value: Any) -> datetime | None:
        if isinstance(value, str):
            try:
                return EventSyncService._row_time(value)
            except ValueError:
                pass
        return None

    async def _process_plate(self, event: dict[str, Any], reservation) -> str:
        event, empty_result = await self._resolve_empty_plate(event)
        if empty_result:
            return empty_result
        accepted = self._upsert_hardware_state(
            event["bay_id"],
            latest_plate=event.get("license_plate"),
            latest_plate_at=event["timestamp"],
            observed_at=event["timestamp"],
        )
        current_snapshot = not accepted and self._is_current_plate_snapshot(event)
        if not accepted and not current_snapshot:
            return "STALE_PLATE_EVENT_IGNORED"
        if reservation is None:
            return "NO_ACTIVE_RESERVATION"
        plate_matches = canonical_plate(event.get("license_plate")) == canonical_plate(
            reservation["license_plate"]
        )
        if current_snapshot and (
            reservation["status"] in {"CHECKED_IN", "CHARGING", "CHARGE_COMPLETED"}
            or (reservation["status"] == "ATTENTION_REQUIRED" and not plate_matches)
        ):
            return "PLATE_SNAPSHOT_ALREADY_PROCESSED"
        if not plate_matches:
            self._create_alert(
                event["event_id"],
                event["bay_id"],
                reservation["id"],
                "WRONG_PLATE",
                "A plate different from the reserved vehicle was detected. Occupancy is not yet confirmed.",
            )
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE app_reservations
                    SET status = 'ATTENTION_REQUIRED', attention_message = ?, updated_at = ?
                    WHERE id = ? AND status = 'RESERVED'
                    """,
                    ("Wrong plate detected; occupancy is not confirmed", now_iso(), reservation["id"]),
                )
            return "PLATE_MISMATCH"

        # The ECURIOUS camera is the arrival signal for this integration. Once
        # its normalized plate matches the active reservation, the driver is
        # checked in immediately; carPresence remains useful telemetry but is
        # not reliable enough to block a valid camera check-in.
        checked_in = self._mark_checked_in(reservation)
        return "CHECK_IN_SUCCESSFUL" if checked_in else "PLATE_MATCHED"

    async def _resolve_empty_plate(
        self, event: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if canonical_plate(event.get("license_plate")):
            return event, None
        try:
            latest = await self.adapter.get_latest_plate(event["bay_id"])
        except ProviderError as exc:
            logger.warning(
                "Latest plate lookup failed for %s: %s", event["bay_id"], exc.code
            )
            return event, "EMPTY_PLATE_LOOKUP_FAILED"
        latest_plate = str(latest.get("licensePlate") or "").strip().upper()
        latest_time = self._event_time(latest.get("timestamp"))
        event_time = self._event_time(event.get("timestamp"))
        if (
            not canonical_plate(latest_plate)
            or latest_time is None
            or event_time is None
            or abs(latest_time - event_time) > timedelta(seconds=5)
        ):
            return event, "EMPTY_PLATE_IGNORED"
        resolved = {
            **event,
            "license_plate": latest_plate,
            "timestamp": latest_time.isoformat(),
        }
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE processed_events SET masked_plate = ?, timestamp = ?
                   WHERE external_event_id = ?""",
                (mask_plate(latest_plate), latest_time.isoformat(), event["event_id"]),
            )
        return resolved, None

    def _is_current_plate_snapshot(self, event: dict[str, Any]) -> bool:
        """Allow an exact latest-photo replay to recover check-in after restart."""
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT latest_plate, latest_plate_at FROM hardware_locks WHERE lock_id = ?",
                (event["bay_id"],),
            ).fetchone()
        return bool(
            state
            and canonical_plate(state["latest_plate"])
            == canonical_plate(event.get("license_plate"))
            and self._event_time(state["latest_plate_at"])
            == self._event_time(event.get("timestamp"))
        )

    async def _process_occupancy(self, event: dict[str, Any], reservation) -> str:
        if self.adapter.mode == "live" and event.get("occupancy"):
            status = {"occupancy": event["occupancy"]}
        else:
            status = await self.adapter.get_bay_status(event["bay_id"])
        accepted = self._upsert_hardware_state(
            event["bay_id"],
            car_presence=status["occupancy"],
            hardware_online=True,
            observed_at=event["timestamp"],
        )
        if not accepted:
            return "STALE_OCCUPANCY_EVENT_IGNORED"
        if reservation is None:
            return f"OCCUPANCY_{status['occupancy'].upper()}_NO_RESERVATION"
        if status["occupancy"] == "occupied":
            if self._try_combined_check_in(event["bay_id"], reservation):
                return "CHECK_IN_SUCCESSFUL"
            with self.database.connect() as connection:
                manual_command = connection.execute(
                    """
                    SELECT requested_at FROM hardware_commands
                    WHERE lock_id = ? AND reservation_id = ? AND command = 'UNLOCK'
                      AND result_status IN ('ACCEPTED','CONFIRMED')
                    ORDER BY requested_at DESC LIMIT 1
                    """,
                    (event["bay_id"], reservation["id"]),
                ).fetchone()
            recent_manual_unlock = False
            if manual_command:
                event_time = self._event_time(event["timestamp"])
                command_time = self._event_time(manual_command["requested_at"])
                recent_manual_unlock = bool(
                    event_time
                    and command_time
                    and timedelta(0) <= event_time - command_time <= timedelta(minutes=5)
                )
            if recent_manual_unlock:
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(
                        """
                        UPDATE app_reservations SET status = 'CHECKED_IN',
                            ecurious_status = 'checked_in', attention_message = NULL,
                            updated_at = ? WHERE id = ?
                        """,
                        (now_iso(), reservation["id"]),
                    )
                self._resolve_alerts(reservation["id"], "WRONG_PLATE")
                self._resolve_alerts(reservation["id"], "BLOCKED_BAY")
                return "MANUAL_UNLOCK_CHECK_IN"
        if status["occupancy"] == "occupied" and reservation["status"] in {
            "RESERVED",
            "ATTENTION_REQUIRED",
        }:
            self._create_alert(
                event["event_id"],
                event["bay_id"],
                reservation["id"],
                "BLOCKED_BAY",
                "A reserved bay is physically occupied without a successful check-in.",
            )
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    UPDATE app_reservations
                    SET status = 'BLOCKED', attention_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("Reserved bay is occupied by an unauthorised or unconfirmed vehicle", now_iso(), reservation["id"]),
                )
            return "BLOCKED_RESERVED_BAY"
        if status["occupancy"] == "free":
            self._resolve_alerts(reservation["id"], "BLOCKED_BAY")
        return f"OCCUPANCY_{status['occupancy'].upper()}"

    def _process_hardware_status(self, event: dict[str, Any]) -> str:
        hardware_status = event.get("hardware_status", "unknown")
        accepted = self._upsert_hardware_state(
            event["bay_id"],
            barrier_status=hardware_status,
            hardware_online=True,
            observed_at=event["timestamp"],
        )
        if not accepted:
            return "STALE_STATUS_EVENT_IGNORED"
        expected = "UNLOCK" if hardware_status == "lowered" else "LOCK"
        with self.database.transaction(immediate=True) as connection:
            command = connection.execute(
                """
                SELECT id FROM hardware_commands
                WHERE lock_id = ? AND command = ? AND result_status = 'ACCEPTED'
                  AND confirmed_at IS NULL ORDER BY requested_at DESC LIMIT 1
                """,
                (event["bay_id"], expected),
            ).fetchone()
            if command:
                connection.execute(
                    """
                    UPDATE hardware_commands SET result_status = 'CONFIRMED', confirmed_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), command["id"]),
                )
                return "PHYSICAL_STATE_CONFIRMED"
        return f"HARDWARE_STATUS_{hardware_status.upper()}"

    def _process_alarm(self, event: dict[str, Any], reservation) -> str:
        if not self._upsert_hardware_state(
            event["bay_id"], alarm=True, observed_at=event["timestamp"]
        ):
            return "STALE_ALARM_EVENT_IGNORED"
        error = str(event.get("error") or "unknown_hardware_alarm")
        self._create_alert(
            event["event_id"],
            event["bay_id"],
            reservation["id"] if reservation else None,
            "HARDWARE_ALARM",
            f"Physical parking lock alarm: {error}",
        )
        return "HARDWARE_ALARM_RECORDED"

    def _upsert_hardware_state(
        self,
        lock_id: str,
        *,
        barrier_status: str | None = None,
        car_presence: str | None = None,
        hardware_online: bool | None = None,
        latest_plate: str | None = None,
        latest_plate_at: str | None = None,
        alarm: bool = False,
        observed_at: str | None = None,
    ) -> bool:
        observed = self._event_time(observed_at)
        if observed is None:
            return False
        observed_value = observed.isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO hardware_locks(
                    lock_id, barrier_status, car_presence, hardware_online,
                    latest_plate, latest_plate_at, telemetry_json, updated_at
                ) VALUES (?, 'unknown', 'unknown', 0, NULL, NULL, '{}', ?)
                ON CONFLICT(lock_id) DO NOTHING
                """,
                (lock_id, now_iso()),
            )
            row = connection.execute(
                "SELECT * FROM hardware_locks WHERE lock_id = ?", (lock_id,)
            ).fetchone()
            assert row is not None
            values: dict[str, Any] = {}
            if barrier_status is not None:
                if row["barrier_observed_at"] and observed <= self._row_time(row["barrier_observed_at"]):
                    return False
                values.update(
                    barrier_status=barrier_status, barrier_observed_at=observed_value
                )
            if car_presence is not None:
                if row["presence_observed_at"] and observed <= self._row_time(row["presence_observed_at"]):
                    return False
                values.update(
                    car_presence=car_presence, presence_observed_at=observed_value
                )
            if latest_plate is not None:
                if row["latest_plate_at"]:
                    current_plate_time = self._row_time(row["latest_plate_at"])
                    if observed < current_plate_time:
                        return False
                    if observed == current_plate_time:
                        current_plate = canonical_plate(row["latest_plate"])
                        incoming_plate = canonical_plate(latest_plate)
                        if current_plate or not incoming_plate:
                            return False
                values.update(latest_plate=latest_plate, latest_plate_at=observed_value)
            if alarm:
                if row["alarm_observed_at"] and observed <= self._row_time(row["alarm_observed_at"]):
                    return False
                values["alarm_observed_at"] = observed_value
            if hardware_online is not None:
                values["hardware_online"] = int(hardware_online)
            if not values:
                return False
            values["updated_at"] = now_iso()
            assignments = ", ".join(f"{column} = ?" for column in values)
            connection.execute(
                f"UPDATE hardware_locks SET {assignments} WHERE lock_id = ?",
                (*values.values(), lock_id),
            )
            return True

    def _try_combined_check_in(self, lock_id: str, reservation: Any) -> bool:
        with self.database.connect() as connection:
            state = connection.execute(
                """SELECT latest_plate, latest_plate_at, car_presence, presence_observed_at
                   FROM hardware_locks WHERE lock_id = ?""",
                (lock_id,),
            ).fetchone()
        if not state or state["car_presence"] != "occupied":
            return False
        plate_time = self._event_time(state["latest_plate_at"])
        presence_time = self._event_time(state["presence_observed_at"])
        if not plate_time or not presence_time:
            return False
        if abs(plate_time - presence_time) > timedelta(minutes=5):
            return False
        if canonical_plate(state["latest_plate"]) != canonical_plate(
            reservation["license_plate"]
        ):
            return False
        return self._mark_checked_in(reservation)

    def _mark_checked_in(self, reservation: Any) -> bool:
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE app_reservations SET status = 'CHECKED_IN',
                       ecurious_status = 'checked_in', attention_message = NULL, updated_at = ?
                   WHERE id = ? AND status IN ('RESERVED','ATTENTION_REQUIRED','BLOCKED')""",
                (now_iso(), reservation["id"]),
            ).rowcount
        if changed:
            self._resolve_alerts(reservation["id"], "WRONG_PLATE")
            self._resolve_alerts(reservation["id"], "BLOCKED_BAY")
        return bool(changed) or reservation["status"] in {
            "CHECKED_IN",
            "CHARGING",
            "CHARGE_COMPLETED",
        }

    async def _process_expiry(self, event: dict[str, Any], reservation) -> str:
        if reservation is None:
            return "EXPIRED_WITHOUT_LOCAL_MATCH"
        provider = await self.adapter.get_reservation(reservation["ecurious_reservation_id"])
        self.reservations._update_from_provider(reservation["id"], provider)
        return "NO_SHOW_EXPIRED" if provider["status"] == "expired" else "EXPIRY_RECONCILED"

    def _create_alert(
        self,
        source_event_id: str,
        bay_id: str,
        reservation_id: str | None,
        alert_type: str,
        message: str,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO alerts(
                    id, bay_id, reservation_id, source_event_id, alert_type,
                    status, message, created_at
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?)
                """,
                (
                    f"ALT-{uuid.uuid4().hex[:12].upper()}",
                    bay_id,
                    reservation_id,
                    source_event_id,
                    alert_type,
                    message,
                    now_iso(),
                ),
            )

    def _resolve_alerts(self, reservation_id: str, alert_type: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE alerts SET status = 'RESOLVED', resolved_at = ?
                WHERE reservation_id = ? AND alert_type = ? AND status != 'RESOLVED'
                """,
                (now_iso(), reservation_id, alert_type),
            )
