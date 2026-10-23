from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class ProviderError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class EcuriousAdapter(ABC):
    """Replaceable boundary between the app and the fictional provider API."""

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


class RealEcuriousAdapter(EcuriousAdapter):
    """Deliberate placeholder: implement only after receiving the official contract."""

    async def _unavailable(self):
        raise ProviderError(
            501,
            "REAL_ADAPTER_NOT_CONFIGURED",
            "Official Ecurious API contract and credentials are not configured",
        )

    async def list_bays(self): return await self._unavailable()
    async def get_bay_status(self, bay_id): return await self._unavailable()
    async def create_reservation(self, **kwargs): return await self._unavailable()
    async def get_reservation(self, reservation_id): return await self._unavailable()
    async def release_reservation(self, reservation_id, reason): return await self._unavailable()
    async def poll_events(self, after): return await self._unavailable()

