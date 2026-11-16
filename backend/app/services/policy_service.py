from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.config import Settings
from app.core.state_machine import LifecycleState, PUBLIC_STATUS, ensure_transition
from app.db.database import Database
from app.services.common import db_datetime, utc_now
from app.services.event_service import insert_event


class PolicyService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def expire_no_shows(self, simulated_now: datetime | None = None) -> dict[str, Any]:
        now = simulated_now or utc_now()
        deadline = db_datetime(now - timedelta(seconds=self.settings.no_show_grace_seconds))
        expired: list[str] = []
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM reservations
                WHERE lifecycle_state = 'RESERVED' AND start_time <= ?
                ORDER BY start_time
                """,
                (deadline,),
            ).fetchall()
            for row in rows:
                ensure_transition(row["lifecycle_state"], LifecycleState.EXPIRED)
                connection.execute(
                    """
                    UPDATE reservations
                    SET lifecycle_state = 'EXPIRED', public_status = ?,
                        hardware_status = 'released', released_reason = 'no_show', updated_at = ?
                    WHERE reservation_id = ?
                    """,
                    (PUBLIC_STATUS[LifecycleState.EXPIRED], db_datetime(now), row["reservation_id"]),
                )
                connection.execute(
                    """
                    UPDATE bays
                    SET active_reservation_id = NULL, protection = 'released', charging = 0,
                        last_seen_at = ?
                    WHERE bay_id = ? AND active_reservation_id = ?
                    """,
                    (db_datetime(now), row["bay_id"], row["reservation_id"]),
                )
                insert_event(
                    connection,
                    event_type="reservation.expired",
                    bay_id=row["bay_id"],
                    timestamp=now,
                    outcome="no_show",
                    reservation_id=row["reservation_id"],
                )
                expired.append(row["reservation_id"])
        return {"expired_reservation_ids": expired, "count": len(expired)}

