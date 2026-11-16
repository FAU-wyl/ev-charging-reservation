from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from collections.abc import AsyncIterator
from typing import Any


class ProviderError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class EcuriousAdapter(ABC):
    """Replaceable boundary for the fictional provider and real lock service."""

    mode = "mock"
    supports_sse = False

    @abstractmethod
    async def list_bays(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_bay_status(self, bay_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def create_reservation(
        self,
        *,
        bay_id: str,
        license_plate: str,
        start_time: datetime,
        end_time: datetime,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def get_reservation(self, reservation_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def release_reservation(
        self, reservation_id: str, reason: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def poll_events(self, after: str | None) -> dict[str, Any]: ...

    async def unlock(self, lock_id: str, *, cancel_auto_lift: bool = False) -> dict[str, Any]:
        raise ProviderError(501, "HARDWARE_CONTROL_UNAVAILABLE", "Hardware control is unavailable")

    async def lock(self, lock_id: str, *, cancel_auto_lift: bool = False) -> dict[str, Any]:
        raise ProviderError(501, "HARDWARE_CONTROL_UNAVAILABLE", "Hardware control is unavailable")

    async def set_whitelist(self, lock_id: str, plates: list[str]) -> dict[str, Any]:
        raise ProviderError(501, "WHITELIST_UNAVAILABLE", "Hardware whitelist is unavailable")

    async def get_latest_plate(self, lock_id: str) -> dict[str, Any]:
        raise ProviderError(501, "LPR_UNAVAILABLE", "License plate recognition is unavailable")

    async def set_keep_state(self, lock_id: str, state: str) -> dict[str, Any]:
        raise ProviderError(501, "HARDWARE_CONTROL_UNAVAILABLE", "Hardware control is unavailable")

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {}
        raise ProviderError(501, "SSE_UNAVAILABLE", "Hardware event stream is unavailable")

    async def close(self) -> None:
        """Release adapter resources. Stateless adapters need no cleanup."""

    async def simulate_plate(
        self, *, bay_id: str, license_plate: str, confidence: float
    ) -> dict[str, Any]:
        raise ProviderError(501, "DEMO_UNAVAILABLE", "Demo controls are unavailable")

    async def simulate_occupancy(
        self, *, bay_id: str, occupancy: str, license_plate: str | None
    ) -> dict[str, Any]:
        raise ProviderError(501, "DEMO_UNAVAILABLE", "Demo controls are unavailable")

    async def simulate_charging(
        self, reservation_id: str, *, start: bool
    ) -> dict[str, Any]:
        raise ProviderError(501, "DEMO_UNAVAILABLE", "Demo controls are unavailable")

    async def simulate_no_show(self, now: datetime | None) -> dict[str, Any]:
        raise ProviderError(501, "DEMO_UNAVAILABLE", "Demo controls are unavailable")
