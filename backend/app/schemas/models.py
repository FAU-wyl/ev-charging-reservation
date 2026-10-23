from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


PLATE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 -]{1,19}$")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset")
    return value


class Bay(BaseModel):
    bay_id: str
    reservation_supported: bool
    charger_reference: str | None = None


class BayList(BaseModel):
    bays: list[Bay]


class BayStatus(BaseModel):
    bay_id: str
    occupancy: Literal["free", "occupied", "unknown"]
    protection: Literal["protected", "released", "unknown"]
    active_reservation_id: str | None = None
    last_seen_at: datetime | None = None


class CreateReservation(BaseModel):
    bay_id: str = Field(min_length=1, max_length=64)
    license_plate: str = Field(min_length=2, max_length=20)
    start_time: datetime
    end_time: datetime

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str) -> str:
        normalized = " ".join(value.strip().upper().split())
        if not PLATE_PATTERN.fullmatch(normalized):
            raise ValueError("license_plate contains unsupported characters")
        return normalized

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def validate_interval(self) -> "CreateReservation":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be earlier than end_time")
        return self


class Reservation(BaseModel):
    reservation_id: str
    bay_id: str
    status: Literal["reserved", "checked_in", "released", "expired", "cancelled"]
    hardware_status: str | None = None
    created_at: datetime | None = None


class ReleaseReason(StrEnum):
    CHARGING_COMPLETED = "charging_completed"
    USER_CANCELLED = "user_cancelled"
    NO_SHOW = "no_show"
    OPERATOR_ACTION = "operator_action"


class ReleaseReservation(BaseModel):
    reason: ReleaseReason


class Event(BaseModel):
    event_id: str
    type: Literal["plate.detected", "occupancy.changed", "reservation.expired"]
    bay_id: str
    license_plate: str | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    timestamp: datetime


class EventPage(BaseModel):
    events: list[Event]
    next_cursor: str | None = None


class ErrorResponse(BaseModel):
    error: str
    message: str


class PlateEventRequest(BaseModel):
    bay_id: str = Field(min_length=1, max_length=64)
    license_plate: str = Field(min_length=2, max_length=20)
    confidence: Annotated[float, Field(ge=0, le=1)]
    source_event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("source_event_id", "event_id"),
    )
    timestamp: datetime | None = None

    @field_validator("license_plate")
    @classmethod
    def validate_plate(cls, value: str) -> str:
        normalized = " ".join(value.strip().upper().split())
        if not PLATE_PATTERN.fullmatch(normalized):
            raise ValueError("license_plate contains unsupported characters")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class PlateEventResult(BaseModel):
    event: Event
    duplicate: bool
    matched: bool
    outcome: str
    reservation_id: str | None = None


class OccupancyRequest(BaseModel):
    occupancy: Literal["free", "occupied", "unknown"]
    license_plate: str | None = Field(default=None, min_length=2, max_length=20)

    @field_validator("license_plate")
    @classmethod
    def validate_optional_plate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().upper().split())
        if not PLATE_PATTERN.fullmatch(normalized):
            raise ValueError("license_plate contains unsupported characters")
        return normalized


class OccupancyResult(BaseModel):
    bay_status: BayStatus
    blocked: bool
    event: Event


class ChargingResult(BaseModel):
    reservation_id: str
    lifecycle_state: str
    charging: bool
    public_reservation: Reservation


class NoShowRequest(BaseModel):
    now: datetime | None = None

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class NoShowResult(BaseModel):
    expired_reservation_ids: list[str]
    count: int


class ResetResult(BaseModel):
    reset: bool
    seeded_bays: int
