from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS bays (
    bay_id TEXT PRIMARY KEY,
    reservation_supported INTEGER NOT NULL CHECK (reservation_supported IN (0, 1)),
    charger_reference TEXT,
    occupancy TEXT NOT NULL DEFAULT 'free'
        CHECK (occupancy IN ('free', 'occupied', 'unknown')),
    protection TEXT NOT NULL DEFAULT 'released'
        CHECK (protection IN ('protected', 'released', 'unknown')),
    active_reservation_id TEXT,
    last_seen_at TEXT,
    charging INTEGER NOT NULL DEFAULT 0 CHECK (charging IN (0, 1)),
    occupied_plate TEXT,
    blocked INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0, 1))
);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    bay_id TEXT NOT NULL REFERENCES bays(bay_id),
    license_plate TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    public_status TEXT NOT NULL
        CHECK (public_status IN ('reserved', 'checked_in', 'released', 'expired', 'cancelled')),
    lifecycle_state TEXT NOT NULL,
    hardware_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    idempotency_fingerprint TEXT NOT NULL,
    released_reason TEXT
);

CREATE INDEX IF NOT EXISTS ix_reservations_bay_interval
ON reservations(bay_id, start_time, end_time, lifecycle_state);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL
        CHECK (type IN ('plate.detected', 'occupancy.changed', 'reservation.expired')),
    bay_id TEXT NOT NULL REFERENCES bays(bay_id),
    license_plate TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    timestamp TEXT NOT NULL,
    source_event_id TEXT,
    outcome TEXT NOT NULL,
    reservation_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_events_source_event_id
ON events(source_event_id) WHERE source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_events_sequence ON events(sequence);
"""


SEED_BAYS = (
    ("BAY-001", 1, "GE-EXAMPLE-101"),
    ("BAY-002", 0, None),
    ("BAY-003", 1, "GE-EXAMPLE-103"),
)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.executemany(
                """
                INSERT OR IGNORE INTO bays(
                    bay_id, reservation_supported, charger_reference,
                    occupancy, protection, charging, blocked
                ) VALUES (?, ?, ?, 'free', 'released', 0, 0)
                """,
                SEED_BAYS,
            )

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reset(self) -> int:
        with self.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM reservations")
            connection.execute("DELETE FROM sqlite_sequence WHERE name = 'events'")
            connection.execute(
                """
                UPDATE bays
                SET occupancy = 'free', protection = 'released',
                    active_reservation_id = NULL, last_seen_at = NULL,
                    charging = 0, occupied_plate = NULL, blocked = 0
                """
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO bays(
                    bay_id, reservation_supported, charger_reference,
                    occupancy, protection, charging, blocked
                ) VALUES (?, ?, ?, 'free', 'released', 0, 0)
                """,
                SEED_BAYS,
            )
        return len(SEED_BAYS)

