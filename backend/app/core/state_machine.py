from __future__ import annotations

from enum import StrEnum

from app.core.exceptions import conflict


class LifecycleState(StrEnum):
    RESERVED = "RESERVED"
    CHECKED_IN = "CHECKED_IN"
    CHARGING = "CHARGING"
    CHARGE_COMPLETED = "CHARGE_COMPLETED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


TERMINAL_STATES = {
    LifecycleState.RELEASED,
    LifecycleState.CANCELLED,
    LifecycleState.EXPIRED,
    LifecycleState.FAILED,
}

PUBLIC_STATUS = {
    LifecycleState.RESERVED: "reserved",
    LifecycleState.CHECKED_IN: "checked_in",
    LifecycleState.CHARGING: "checked_in",
    LifecycleState.CHARGE_COMPLETED: "checked_in",
    LifecycleState.RELEASED: "released",
    LifecycleState.CANCELLED: "cancelled",
    LifecycleState.EXPIRED: "expired",
    LifecycleState.FAILED: "cancelled",
}

ALLOWED_TRANSITIONS = {
    LifecycleState.RESERVED: {
        LifecycleState.CHECKED_IN,
        LifecycleState.RELEASED,
        LifecycleState.CANCELLED,
        LifecycleState.EXPIRED,
        LifecycleState.FAILED,
    },
    LifecycleState.CHECKED_IN: {
        LifecycleState.CHARGING,
        LifecycleState.RELEASED,
        LifecycleState.FAILED,
    },
    LifecycleState.CHARGING: {
        LifecycleState.CHARGE_COMPLETED,
        LifecycleState.RELEASED,
        LifecycleState.FAILED,
    },
    LifecycleState.CHARGE_COMPLETED: {
        LifecycleState.RELEASED,
        LifecycleState.FAILED,
    },
}


def ensure_transition(current: str, target: LifecycleState) -> None:
    current_state = LifecycleState(current)
    if target not in ALLOWED_TRANSITIONS.get(current_state, set()):
        raise conflict(
            "INVALID_STATE_TRANSITION",
            f"Cannot transition reservation from {current_state.value} to {target.value}",
        )

