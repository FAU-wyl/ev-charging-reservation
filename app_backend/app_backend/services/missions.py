from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.errors import AppError, conflict, not_found
from app_backend.schemas import ReservationCreate, UseCasePolicyUpdate


def _utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def policy_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "code": row["code"],
        "name": row["name"],
        "description": row["description"],
        "enabled": bool(row["enabled"]),
        "max_duration_minutes": row["max_duration_minutes"],
        "advance_booking_days": row["advance_booking_days"],
        "discount_percent": row["discount_percent"],
        "requires_reference": bool(row["requires_reference"]),
    }


class MissionService:
    """Small, reusable business rules layered on the core reservation lifecycle."""

    def __init__(
        self, database: Database, adapter: EcuriousAdapter, settings: Settings
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.settings = settings

    def policies(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM use_case_policies"
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY CASE code WHEN 'STANDARD' THEN 0 ELSE 1 END, name"
        with self.database.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [policy_view(row) for row in rows]

    def update_policy(
        self, code: str, payload: UseCasePolicyUpdate
    ) -> dict[str, Any]:
        updates = payload.model_dump(exclude_none=True)
        if not updates:
            raise AppError(422, "NO_POLICY_CHANGES", "At least one setting is required")
        values: dict[str, Any] = {
            key: int(value) if key in {"enabled", "requires_reference"} else value
            for key, value in updates.items()
        }
        values["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                f"UPDATE use_case_policies SET {assignments} WHERE code = ?",
                (*values.values(), code.upper()),
            ).rowcount
            if not changed:
                raise not_found("Use-case policy was not found")
            row = connection.execute(
                "SELECT * FROM use_case_policies WHERE code = ?", (code.upper(),)
            ).fetchone()
        assert row is not None
        return policy_view(row)

    def validate_use_case(self, payload: ReservationCreate) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM use_case_policies WHERE code = ?", (payload.use_case_code,)
            ).fetchone()
        if row is None or not row["enabled"]:
            raise conflict("USE_CASE_UNAVAILABLE", "The selected reservation scenario is unavailable")
        duration = int((payload.end_time - payload.start_time).total_seconds() / 60)
        if duration > row["max_duration_minutes"]:
            raise conflict(
                "USE_CASE_DURATION_EXCEEDED",
                f"{row['name']} reservations are limited to {row['max_duration_minutes']} minutes",
            )
        if payload.start_time > datetime.now(timezone.utc) + timedelta(days=row["advance_booking_days"]):
            raise conflict(
                "USE_CASE_ADVANCE_LIMIT",
                f"{row['name']} reservations can be booked up to {row['advance_booking_days']} days ahead",
            )
        if row["requires_reference"] and not payload.membership_reference:
            raise conflict(
                "USE_CASE_REFERENCE_REQUIRED",
                f"{row['name']} requires a verified booking or membership reference",
            )
        return policy_view(row)

    def record_reservation_created(
        self, reservation_id: str, policy: dict[str, Any]
    ) -> None:
        amount = round(
            self.settings.reservation_fee_eur
            * (1 - float(policy["discount_percent"]) / 100),
            2,
        )
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO reservation_charges(
                       id, reservation_id, charge_type, amount_eur, status,
                       description, created_at, updated_at
                   ) VALUES (?, ?, 'RESERVATION_FEE', ?, 'ESTIMATED', ?, ?, ?)""",
                (
                    f"FEE-{uuid.uuid4().hex[:12].upper()}",
                    reservation_id,
                    amount,
                    f"Estimated {policy['name']} reservation fee; no payment captured",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO reservation_notifications(
                       id, reservation_id, notification_type, status, message, created_at
                   ) VALUES (?, ?, 'BOOKING_CONFIRMED', 'IN_APP', ?, ?)""",
                (
                    f"NTF-{uuid.uuid4().hex[:12].upper()}",
                    reservation_id,
                    f"Reservation confirmed under the {policy['name']} scenario.",
                    timestamp,
                ),
            )

    def reservation_finance(self, reservation_id: str, user_id: str, role: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            reservation = connection.execute(
                "SELECT user_id FROM app_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if reservation is None:
                raise not_found("Reservation was not found")
            if role != "ADMIN" and reservation["user_id"] != user_id:
                raise AppError(403, "FORBIDDEN", "Drivers can only access their own fees")
            charges = connection.execute(
                "SELECT * FROM reservation_charges WHERE reservation_id = ? ORDER BY created_at",
                (reservation_id,),
            ).fetchall()
            notices = connection.execute(
                "SELECT * FROM reservation_notifications WHERE reservation_id = ? ORDER BY created_at DESC",
                (reservation_id,),
            ).fetchall()
        return {
            "label": "Estimated / Simulation — no payment captured",
            "charges": [dict(row) for row in charges],
            "notifications": [dict(row) for row in notices],
            "estimated_total_eur": round(
                sum(row["amount_eur"] for row in charges if row["status"] == "ESTIMATED"),
                2,
            ),
        }

    def create_notification(
        self, reservation_id: str, notification_type: str, message: str
    ) -> bool:
        with self.database.transaction(immediate=True) as connection:
            return bool(
                connection.execute(
                    """INSERT OR IGNORE INTO reservation_notifications(
                           id, reservation_id, notification_type, status, message, created_at
                       ) VALUES (?, ?, ?, 'IN_APP', ?, ?)""",
                    (
                        f"NTF-{uuid.uuid4().hex[:12].upper()}",
                        reservation_id,
                        notification_type,
                        message,
                        now_iso(),
                    ),
                ).rowcount
            )

    async def process_lifecycle(self, *, at: datetime | None = None) -> dict[str, int]:
        instant = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        reminder_window = timedelta(minutes=self.settings.arrival_reminder_minutes)
        grace = timedelta(minutes=self.settings.reservation_grace_minutes)
        with self.database.connect() as connection:
            active = connection.execute(
                """SELECT id, ecurious_reservation_id, bay_id, start_time, status
                   FROM app_reservations
                   WHERE status IN ('RESERVED','ATTENTION_REQUIRED') ORDER BY start_time"""
            ).fetchall()
        reminders = expired = reviews = 0
        for row in active:
            start = _utc(row["start_time"])
            if start - reminder_window <= instant < start + grace:
                reminders += int(
                    self.create_notification(
                        row["id"],
                        "ARRIVAL_REMINDER",
                        f"Your reservation starts soon. Arrival grace is {self.settings.reservation_grace_minutes} minutes.",
                    )
                )
            if self.adapter.mode == "live" or start + grace > instant:
                continue
            try:
                physical = await self.adapter.get_bay_status(row["bay_id"])
            except ProviderError as exc:
                self._record_no_show_review(row["id"], row["bay_id"], exc.code)
                reviews += 1
                continue
            if physical.get("hardware_online") is False or physical.get("occupancy") != "free":
                self._record_no_show_review(row["id"], row["bay_id"], "HARDWARE_OR_OCCUPANCY_UNCERTAIN")
                reviews += 1
                continue
            provider = await self.adapter.release_reservation(
                row["ecurious_reservation_id"], "no_show"
            )
            with self.database.transaction(immediate=True) as connection:
                expired += connection.execute(
                    """UPDATE app_reservations SET status = 'EXPIRED',
                           ecurious_status = ?, updated_at = ?, attention_message = NULL
                       WHERE id = ? AND status IN ('RESERVED','ATTENTION_REQUIRED')""",
                    (provider.get("status", "expired"), now_iso(), row["id"]),
                ).rowcount
        recorded, review_records = self.record_no_show_outcomes()
        return {
            "reminders": reminders,
            "expired": expired,
            "no_show_records": recorded,
            "review_required": max(reviews, review_records),
        }

    def record_no_show_outcomes(self) -> tuple[int, int]:
        timestamp = now_iso()
        confirmed = reviews = 0
        with self.database.transaction(immediate=True) as connection:
            expired_rows = connection.execute(
                """SELECT r.id FROM app_reservations r
                   LEFT JOIN no_show_cases n ON n.reservation_id = r.id
                   WHERE r.status = 'EXPIRED' AND n.id IS NULL"""
            ).fetchall()
            for row in expired_rows:
                case_id = f"NS-{uuid.uuid4().hex[:12].upper()}"
                connection.execute(
                    """INSERT INTO no_show_cases(id, reservation_id, status, reason, created_at)
                       VALUES (?, ?, 'AUTO_CONFIRMED', 'Grace period elapsed with a confirmed free bay', ?)""",
                    (case_id, row["id"], timestamp),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO reservation_charges(
                           id, reservation_id, charge_type, amount_eur, status,
                           description, created_at, updated_at
                       ) VALUES (?, ?, 'NO_SHOW_FEE', ?, 'ESTIMATED', ?, ?, ?)""",
                    (
                        f"FEE-{uuid.uuid4().hex[:12].upper()}",
                        row["id"],
                        self.settings.no_show_fee_eur,
                        "Estimated no-show fee after the grace period; no payment captured",
                        timestamp,
                        timestamp,
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO reservation_notifications(
                           id, reservation_id, notification_type, status, message, created_at
                       ) VALUES (?, ?, 'NO_SHOW_CONFIRMED', 'IN_APP', ?, ?)""",
                    (
                        f"NTF-{uuid.uuid4().hex[:12].upper()}",
                        row["id"],
                        "Reservation released after the arrival grace period. An estimated no-show fee was recorded.",
                        timestamp,
                    ),
                )
                confirmed += 1
            review_rows = connection.execute(
                """SELECT DISTINCT a.reservation_id FROM alerts a
                   LEFT JOIN no_show_cases n ON n.reservation_id = a.reservation_id
                   WHERE a.alert_type = 'NO_SHOW_REVIEW' AND a.status != 'RESOLVED'
                     AND a.reservation_id IS NOT NULL AND n.id IS NULL"""
            ).fetchall()
            for row in review_rows:
                connection.execute(
                    """INSERT INTO no_show_cases(id, reservation_id, status, reason, created_at)
                       VALUES (?, ?, 'REVIEW_REQUIRED', 'Hardware or OCR evidence is inconclusive', ?)""",
                    (f"NS-{uuid.uuid4().hex[:12].upper()}", row["reservation_id"], timestamp),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO reservation_notifications(
                           id, reservation_id, notification_type, status, message, created_at
                       ) VALUES (?, ?, 'NO_SHOW_REVIEW', 'IN_APP', ?, ?)""",
                    (
                        f"NTF-{uuid.uuid4().hex[:12].upper()}",
                        row["reservation_id"],
                        "Arrival evidence requires operator review. No no-show fee has been applied.",
                        timestamp,
                    ),
                )
                reviews += 1
        return confirmed, reviews

    def _record_no_show_review(self, reservation_id: str, bay_id: str, reason: str) -> None:
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE app_reservations SET status = 'ATTENTION_REQUIRED',
                       attention_message = ?, updated_at = ?
                   WHERE id = ? AND status IN ('RESERVED','ATTENTION_REQUIRED')""",
                ("No-show review required; no fee applied", timestamp, reservation_id),
            )
            connection.execute(
                """INSERT OR IGNORE INTO alerts(
                       id, bay_id, reservation_id, source_event_id, alert_type,
                       status, message, created_at
                   ) VALUES (?, ?, ?, ?, 'NO_SHOW_REVIEW', 'OPEN', ?, ?)""",
                (
                    f"ALT-{uuid.uuid4().hex[:12].upper()}",
                    bay_id,
                    reservation_id,
                    f"NO-SHOW-REVIEW-{reservation_id}",
                    f"No-show requires manual review: {reason}",
                    timestamp,
                ),
            )

    def waive_charge(self, charge_id: str, reason: str) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE reservation_charges SET status = 'WAIVED', waiver_reason = ?,
                       updated_at = ? WHERE id = ? AND status != 'WAIVED'""",
                (reason, timestamp, charge_id),
            ).rowcount
            row = connection.execute(
                "SELECT * FROM reservation_charges WHERE id = ?", (charge_id,)
            ).fetchone()
            if row is None:
                raise not_found("Charge was not found")
            if changed and row["charge_type"] == "NO_SHOW_FEE":
                connection.execute(
                    """UPDATE no_show_cases SET status = 'WAIVED', resolved_at = ?
                       WHERE reservation_id = ?""",
                    (timestamp, row["reservation_id"]),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO reservation_notifications(
                           id, reservation_id, notification_type, status, message, created_at
                       ) VALUES (?, ?, 'NO_SHOW_FEE_WAIVED', 'IN_APP', ?, ?)""",
                    (
                        f"NTF-{uuid.uuid4().hex[:12].upper()}",
                        row["reservation_id"],
                        f"The estimated no-show fee was waived: {reason}",
                        timestamp,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM reservation_charges WHERE id = ?", (charge_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def charges(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, r.use_case_code, u.email AS user_email
                   FROM reservation_charges c
                   JOIN app_reservations r ON r.id = c.reservation_id
                   JOIN users u ON u.id = r.user_id
                   ORDER BY c.created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def apply_cancellation_policy(self, reservation_id: str, start_time: str) -> None:
        cutoff = _utc(start_time) - timedelta(minutes=self.settings.free_cancellation_minutes)
        if datetime.now(timezone.utc) < cutoff:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """UPDATE reservation_charges SET status = 'WAIVED',
                           waiver_reason = 'Free cancellation window', updated_at = ?
                       WHERE reservation_id = ? AND charge_type = 'RESERVATION_FEE'""",
                    (now_iso(), reservation_id),
                )

    def purge_personal_data(self, *, at: datetime | None = None) -> dict[str, int]:
        cutoff = (at or datetime.now(timezone.utc)) - timedelta(
            days=self.settings.privacy_retention_days
        )
        with self.database.transaction(immediate=True) as connection:
            events = connection.execute(
                "DELETE FROM processed_events WHERE timestamp < ?", (cutoff.isoformat(),)
            ).rowcount
            plates = connection.execute(
                """UPDATE hardware_locks SET latest_plate = NULL, latest_plate_at = NULL
                   WHERE latest_plate_at IS NOT NULL AND latest_plate_at < ?""",
                (cutoff.isoformat(),),
            ).rowcount
        return {
            "retention_days": self.settings.privacy_retention_days,
            "events_deleted": events,
            "latest_plates_cleared": plates,
        }
