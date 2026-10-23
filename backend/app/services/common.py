from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def db_datetime(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="microseconds")


def parse_db_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def public_reservation(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reservation_id": row["reservation_id"],
        "bay_id": row["bay_id"],
        "status": row["public_status"],
        "hardware_status": row["hardware_status"],
        "created_at": parse_db_datetime(row["created_at"]),
    }


def public_bay_status(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bay_id": row["bay_id"],
        "occupancy": row["occupancy"],
        "protection": row["protection"],
        "active_reservation_id": row["active_reservation_id"],
        "last_seen_at": (
            parse_db_datetime(row["last_seen_at"]) if row["last_seen_at"] else None
        ),
    }


def public_event(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "type": row["type"],
        "bay_id": row["bay_id"],
        "license_plate": row["license_plate"],
        "confidence": row["confidence"],
        "timestamp": parse_db_datetime(row["timestamp"]),
    }

