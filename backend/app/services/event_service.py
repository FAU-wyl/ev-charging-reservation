from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from app.core.exceptions import AppError
from app.db.database import Database
from app.services.common import db_datetime, public_event, utc_now


CURSOR_PATTERN = re.compile(r"^cursor-(\d+)$")


def insert_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    bay_id: str,
    timestamp: datetime | None = None,
    license_plate: str | None = None,
    confidence: float | None = None,
    source_event_id: str | None = None,
    outcome: str,
    reservation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> sqlite3.Row:
    event_id = f"EVT-{uuid.uuid4().hex[:16].upper()}"
    connection.execute(
        """
        INSERT INTO events(
            event_id, type, bay_id, license_plate, confidence, timestamp,
            source_event_id, outcome, reservation_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            bay_id,
            license_plate,
            confidence,
            db_datetime(timestamp or utc_now()),
            source_event_id,
            outcome,
            reservation_id,
            json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
        ),
    )
    row = connection.execute(
        "SELECT * FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()
    assert row is not None
    return row


class EventService:
    def __init__(self, database: Database, page_size: int) -> None:
        self.database = database
        self.page_size = page_size

    @staticmethod
    def parse_cursor(after: str | None) -> int:
        if after is None:
            return 0
        match = CURSOR_PATTERN.fullmatch(after)
        if not match:
            raise AppError(422, "INVALID_CURSOR", "after must use cursor-<number> format")
        return int(match.group(1))

    def poll(self, after: str | None) -> dict[str, Any]:
        sequence = self.parse_cursor(after)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (sequence, self.page_size),
            ).fetchall()
        next_sequence = int(rows[-1]["sequence"]) if rows else sequence
        next_cursor = f"cursor-{next_sequence:08d}" if rows or after is not None else None
        return {
            "events": [public_event(row) for row in rows],
            "next_cursor": next_cursor,
        }

