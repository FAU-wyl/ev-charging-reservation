from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app_backend.config import Settings
from app_backend.security import hash_password


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('DRIVER', 'ADMIN')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    license_plate TEXT NOT NULL,
    nickname TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, license_plate)
);
CREATE INDEX IF NOT EXISTS ix_vehicles_user ON vehicles(user_id);

CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_station_id TEXT NOT NULL,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    postcode TEXT NOT NULL,
    street TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    network TEXT,
    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
    fault INTEGER NOT NULL CHECK (fault IN (0, 1)),
    chargepoints_json TEXT NOT NULL,
    source_url TEXT NOT NULL,
    UNIQUE(source, external_station_id)
);

CREATE TABLE IF NOT EXISTS bay_mappings (
    bay_id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id)
);
CREATE INDEX IF NOT EXISTS ix_bay_mappings_station ON bay_mappings(station_id);

CREATE TABLE IF NOT EXISTS app_reservations (
    id TEXT PRIMARY KEY,
    ecurious_reservation_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id),
    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
    station_id TEXT NOT NULL REFERENCES stations(id),
    bay_id TEXT NOT NULL,
    status TEXT NOT NULL,
    ecurious_status TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cancellation_reason TEXT,
    attention_message TEXT,
    simulated_charging INTEGER NOT NULL DEFAULT 0 CHECK (simulated_charging IN (0, 1)),
    provider_confirmed INTEGER NOT NULL DEFAULT 1 CHECK (provider_confirmed IN (0, 1)),
    hardware_sync_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    hardware_sync_error TEXT,
    hardware_sync_at TEXT,
    release_operation_id TEXT,
    release_requested_at TEXT,
    client_request_key TEXT NOT NULL,
    UNIQUE(user_id, client_request_key)
);
CREATE INDEX IF NOT EXISTS ix_app_reservations_user ON app_reservations(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_app_reservations_bay ON app_reservations(bay_id, start_time, end_time);
CREATE INDEX IF NOT EXISTS ix_app_reservations_status ON app_reservations(status);

CREATE TABLE IF NOT EXISTS processed_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    bay_id TEXT NOT NULL,
    reservation_id TEXT,
    masked_plate TEXT,
    confidence REAL,
    processing_result TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_processed_events_time ON processed_events(timestamp DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    bay_id TEXT NOT NULL,
    reservation_id TEXT,
    source_event_id TEXT,
    alert_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    message TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(source_event_id, alert_type)
);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status, created_at DESC);

CREATE TABLE IF NOT EXISTS hardware_locks (
    lock_id TEXT PRIMARY KEY,
    barrier_status TEXT NOT NULL DEFAULT 'unknown',
    car_presence TEXT NOT NULL DEFAULT 'unknown',
    hardware_online INTEGER NOT NULL DEFAULT 0 CHECK (hardware_online IN (0, 1)),
    latest_plate TEXT,
    latest_plate_at TEXT,
    barrier_observed_at TEXT,
    presence_observed_at TEXT,
    alarm_observed_at TEXT,
    telemetry_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hardware_commands (
    id TEXT PRIMARY KEY,
    lock_id TEXT NOT NULL,
    command TEXT NOT NULL,
    requested_by_user_id TEXT REFERENCES users(id),
    reservation_id TEXT REFERENCES app_reservations(id),
    result_status TEXT NOT NULL,
    result_message TEXT,
    requested_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_hardware_commands_lock
    ON hardware_commands(lock_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS hardware_whitelist_state (
    lock_id TEXT PRIMARY KEY,
    desired_hash TEXT,
    applied_hash TEXT,
    sync_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_operations (
    id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES app_reservations(id),
    lock_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_release_operations_reservation
    ON release_operations(reservation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS charging_sessions (
    session_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES app_reservations(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    station_id TEXT NOT NULL REFERENCES stations(id),
    bay_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    pricing_model TEXT NOT NULL,
    rate_eur_per_hour REAL NOT NULL,
    estimated_cost_eur REAL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'COMPLETED')),
    is_simulated INTEGER NOT NULL DEFAULT 1 CHECK (is_simulated IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_charging_sessions_active_reservation
    ON charging_sessions(reservation_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS ix_charging_sessions_reservation
    ON charging_sessions(reservation_id, started_at DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.database_path

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

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
        self._seed_stations()
        self._seed_demo_users()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Small additive migrations keep existing hackathon databases usable."""
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(app_reservations)").fetchall()
        }
        additions = {
            "hardware_sync_status": "TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
            "hardware_sync_error": "TEXT",
            "hardware_sync_at": "TEXT",
            "release_operation_id": "TEXT",
            "release_requested_at": "TEXT",
        }
        for column, declaration in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE app_reservations ADD COLUMN {column} {declaration}"
                )
        hardware_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'hardware_locks'"
        ).fetchone()
        if hardware_table:
            hardware_existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(hardware_locks)").fetchall()
            }
            for column in (
                "barrier_observed_at",
                "presence_observed_at",
                "alarm_observed_at",
            ):
                if column not in hardware_existing:
                    connection.execute(
                        f"ALTER TABLE hardware_locks ADD COLUMN {column} TEXT"
                    )

    def _seed_stations(self) -> None:
        fixture = Path(self.settings.station_fixture_path)
        if not fixture.exists():
            raise RuntimeError(f"Station fixture not found: {fixture}")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        stations = data.get("chargelocations", [])
        if not stations:
            raise RuntimeError("Station fixture does not contain chargelocations")

        with self.transaction(immediate=True) as connection:
            station_ids: list[str] = []
            for item in stations:
                external_id = str(item["ge_id"])
                station_id = f"GE-{external_id}"
                station_ids.append(station_id)
                address = item.get("address") or {}
                source_url = item.get("url") or (
                    f"https://www.goingelectric.de/stromtankstellen/{external_id}/"
                )
                if source_url.startswith("//"):
                    source_url = "https:" + source_url
                network = item.get("network")
                connection.execute(
                    """
                    INSERT INTO stations(
                        id, source, external_station_id, name, city, country,
                        postcode, street, lat, lng, network, verified, fault,
                        chargepoints_json, source_url
                    ) VALUES (?, 'GoingElectric fixture', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name, city = excluded.city, country = excluded.country,
                        postcode = excluded.postcode, street = excluded.street,
                        lat = excluded.lat, lng = excluded.lng, network = excluded.network,
                        verified = excluded.verified, fault = excluded.fault,
                        chargepoints_json = excluded.chargepoints_json,
                        source_url = excluded.source_url
                    """,
                    (
                        station_id,
                        external_id,
                        item.get("name") or f"Charging station {external_id}",
                        address.get("city") or "",
                        address.get("country") or "",
                        address.get("postcode") or "",
                        address.get("street") or "",
                        float(item["coordinates"]["lat"]),
                        float(item["coordinates"]["lng"]),
                        str(network) if network else None,
                        int(bool(item.get("verified"))),
                        int(bool(item.get("fault_report"))),
                        json.dumps(item.get("chargepoints") or [], separators=(",", ":")),
                        source_url,
                    ),
                )

            # Mock IDs never represent additional physical bays in live mode.
            if self.settings.ecurious_api_mode == "live":
                connection.execute(
                    "DELETE FROM bay_mappings WHERE bay_id IN ('BAY-001','BAY-002','BAY-003')"
                )
            else:
                for bay_id, station_id in zip(
                    ("BAY-001", "BAY-003", "BAY-002"), station_ids[:3], strict=False
                ):
                    connection.execute(
                        "INSERT OR IGNORE INTO bay_mappings(bay_id, station_id) VALUES (?, ?)",
                        (bay_id, station_id),
                    )
            if self.settings.ecurious_api_mode == "live" and self.settings.ecurious_lock_id and station_ids:
                connection.execute(
                    """INSERT INTO bay_mappings(bay_id, station_id) VALUES (?, ?)
                       ON CONFLICT(bay_id) DO UPDATE SET station_id = excluded.station_id""",
                    (self.settings.ecurious_lock_id, station_ids[0]),
                )

    def _seed_demo_users(self) -> None:
        if not self.settings.seed_demo_users:
            return
        users = (
            (
                self.settings.demo_driver_email,
                self.settings.demo_driver_password,
                "DRIVER",
            ),
            (
                self.settings.demo_admin_email,
                self.settings.demo_admin_password,
                "ADMIN",
            ),
        )
        if any(len(password) < 8 for _, password, _ in users):
            raise RuntimeError(
                "Demo user seeding requires DEMO_DRIVER_PASSWORD and "
                "DEMO_ADMIN_PASSWORD of at least 8 characters"
            )
        with self.transaction(immediate=True) as connection:
            for email, password, role in users:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO users(id, email, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        f"USR-{uuid.uuid4().hex[:12].upper()}",
                        email.lower(),
                        hash_password(password),
                        role,
                        now_iso(),
                    ),
                )
