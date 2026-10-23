from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from app.config import Settings
from app.core.exceptions import conflict, not_found
from app.core.state_machine import LifecycleState, PUBLIC_STATUS, ensure_transition
from app.db.database import Database
from app.schemas.models import OccupancyRequest, PlateEventRequest
from app.services.common import (
    db_datetime,
    public_bay_status,
    public_event,
    public_reservation,
    utc_now,
)
from app.services.event_service import insert_event


class HardwareSimulator:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def process_plate(self, payload: PlateEventRequest) -> dict[str, Any]:
        detected_at = payload.timestamp or utc_now()
        signature = {
            "bay_id": payload.bay_id,
            "license_plate": payload.license_plate,
            "confidence": payload.confidence,
            "timestamp": db_datetime(detected_at) if payload.timestamp else None,
        }

        with self.database.transaction(immediate=True) as connection:
            bay = connection.execute(
                "SELECT * FROM bays WHERE bay_id = ?", (payload.bay_id,)
            ).fetchone()
            if bay is None:
                raise not_found(f"Bay {payload.bay_id}")

            if payload.source_event_id:
                duplicate = connection.execute(
                    "SELECT * FROM events WHERE source_event_id = ?",
                    (payload.source_event_id,),
                ).fetchone()
                if duplicate is not None:
                    metadata = json.loads(duplicate["metadata_json"])
                    if metadata.get("input") != signature:
                        raise conflict(
                            "SOURCE_EVENT_ID_REUSED",
                            "source_event_id was already used with different event data",
                        )
                    return {
                        "event": public_event(duplicate),
                        "duplicate": True,
                        "matched": bool(metadata.get("matched", False)),
                        "outcome": duplicate["outcome"],
                        "reservation_id": duplicate["reservation_id"],
                    }

            earliest_start = db_datetime(
                detected_at + timedelta(seconds=self.settings.early_arrival_seconds)
            )
            latest_end = db_datetime(
                detected_at - timedelta(seconds=self.settings.no_show_grace_seconds)
            )
            reservation = connection.execute(
                """
                SELECT * FROM reservations
                WHERE bay_id = ?
                  AND lifecycle_state IN ('RESERVED', 'CHECKED_IN', 'CHARGING', 'CHARGE_COMPLETED')
                  AND start_time <= ?
                  AND end_time >= ?
                ORDER BY start_time, created_at
                LIMIT 1
                """,
                (payload.bay_id, earliest_start, latest_end),
            ).fetchone()

            matched = False
            reservation_id: str | None = None
            if payload.confidence < self.settings.min_plate_confidence:
                outcome = "low_confidence"
            elif reservation is None:
                outcome = "no_active_reservation"
            elif reservation["license_plate"] != payload.license_plate:
                outcome = "plate_mismatch"
                reservation_id = reservation["reservation_id"]
            else:
                matched = True
                reservation_id = reservation["reservation_id"]
                if reservation["lifecycle_state"] == LifecycleState.RESERVED.value:
                    ensure_transition(
                        reservation["lifecycle_state"], LifecycleState.CHECKED_IN
                    )
                    connection.execute(
                        """
                        UPDATE reservations
                        SET lifecycle_state = 'CHECKED_IN', public_status = 'checked_in',
                            hardware_status = 'released', updated_at = ?
                        WHERE reservation_id = ?
                        """,
                        (db_datetime(detected_at), reservation_id),
                    )
                    connection.execute(
                        """
                        UPDATE bays
                        SET protection = 'released', active_reservation_id = ?, last_seen_at = ?
                        WHERE bay_id = ?
                        """,
                        (reservation_id, db_datetime(detected_at), payload.bay_id),
                    )
                    outcome = "checked_in"
                else:
                    outcome = "already_checked_in"

            event = insert_event(
                connection,
                event_type="plate.detected",
                bay_id=payload.bay_id,
                timestamp=detected_at,
                license_plate=payload.license_plate,
                confidence=payload.confidence,
                source_event_id=payload.source_event_id,
                outcome=outcome,
                reservation_id=reservation_id,
                metadata={"input": signature, "matched": matched},
            )
            return {
                "event": public_event(event),
                "duplicate": False,
                "matched": matched,
                "outcome": outcome,
                "reservation_id": reservation_id,
            }

    def set_occupancy(self, bay_id: str, payload: OccupancyRequest) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            bay = connection.execute("SELECT * FROM bays WHERE bay_id = ?", (bay_id,)).fetchone()
            if bay is None:
                raise not_found(f"Bay {bay_id}")

            reservation = None
            if bay["active_reservation_id"]:
                reservation = connection.execute(
                    "SELECT * FROM reservations WHERE reservation_id = ?",
                    (bay["active_reservation_id"],),
                ).fetchone()

            blocked = False
            if payload.occupancy == "occupied" and reservation is not None:
                checked_in = reservation["lifecycle_state"] in {
                    LifecycleState.CHECKED_IN.value,
                    LifecycleState.CHARGING.value,
                    LifecycleState.CHARGE_COMPLETED.value,
                }
                plate_matches = (
                    payload.license_plate is None
                    or payload.license_plate == reservation["license_plate"]
                )
                blocked = not (checked_in and plate_matches)

            if payload.occupancy == "occupied":
                # Safety interlock: never leave the simulated barrier protected under a car.
                protection = "released"
            elif payload.occupancy == "unknown":
                protection = "unknown"
            elif reservation is not None and reservation["lifecycle_state"] == "RESERVED":
                protection = "protected"
            else:
                protection = "released"

            connection.execute(
                """
                UPDATE bays
                SET occupancy = ?, protection = ?, occupied_plate = ?, blocked = ?,
                    charging = CASE WHEN ? = 'free' THEN 0 ELSE charging END,
                    last_seen_at = ?
                WHERE bay_id = ?
                """,
                (
                    payload.occupancy,
                    protection,
                    payload.license_plate if payload.occupancy == "occupied" else None,
                    int(blocked),
                    payload.occupancy,
                    db_datetime(now),
                    bay_id,
                ),
            )
            outcome = "blocked_bay" if blocked else f"occupancy_{payload.occupancy}"
            event = insert_event(
                connection,
                event_type="occupancy.changed",
                bay_id=bay_id,
                timestamp=now,
                outcome=outcome,
                reservation_id=(reservation["reservation_id"] if reservation else None),
                metadata={
                    "occupancy": payload.occupancy,
                    "blocked": blocked,
                    "observed_plate": payload.license_plate,
                },
            )
            updated_bay = connection.execute(
                "SELECT * FROM bays WHERE bay_id = ?", (bay_id,)
            ).fetchone()
            assert updated_bay is not None
            return {
                "bay_status": public_bay_status(updated_bay),
                "blocked": blocked,
                "event": public_event(event),
            }

    def start_charging(self, reservation_id: str) -> dict[str, Any]:
        return self._change_charging(reservation_id, start=True)

    def stop_charging(self, reservation_id: str) -> dict[str, Any]:
        return self._change_charging(reservation_id, start=False)

    def _change_charging(self, reservation_id: str, *, start: bool) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            reservation = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            if reservation is None:
                raise not_found(f"Reservation {reservation_id}")
            bay = connection.execute(
                "SELECT * FROM bays WHERE bay_id = ?", (reservation["bay_id"],)
            ).fetchone()
            assert bay is not None

            current = LifecycleState(reservation["lifecycle_state"])
            if start:
                if current is LifecycleState.CHARGING:
                    target = current
                else:
                    if bay["occupancy"] != "occupied":
                        raise conflict(
                            "CHARGING_NOT_ALLOWED", "Vehicle must physically occupy the bay"
                        )
                    ensure_transition(current.value, LifecycleState.CHARGING)
                    target = LifecycleState.CHARGING
            else:
                if current is LifecycleState.CHARGE_COMPLETED:
                    target = current
                else:
                    ensure_transition(current.value, LifecycleState.CHARGE_COMPLETED)
                    target = LifecycleState.CHARGE_COMPLETED

            connection.execute(
                """
                UPDATE reservations
                SET lifecycle_state = ?, public_status = ?, updated_at = ?
                WHERE reservation_id = ?
                """,
                (target.value, PUBLIC_STATUS[target], db_datetime(now), reservation_id),
            )
            connection.execute(
                "UPDATE bays SET charging = ?, last_seen_at = ? WHERE bay_id = ?",
                (int(start and target is LifecycleState.CHARGING), db_datetime(now), bay["bay_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            assert updated is not None
            return {
                "reservation_id": reservation_id,
                "lifecycle_state": target.value,
                "charging": start and target is LifecycleState.CHARGING,
                "public_reservation": public_reservation(updated),
            }

