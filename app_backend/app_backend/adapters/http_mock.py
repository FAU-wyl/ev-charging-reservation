from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx

from app_backend.adapters.base import EcuriousAdapter, ProviderError


class HttpMockEcuriousAdapter(EcuriousAdapter):
    def __init__(self, base_url: str, token: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        headers.update(kwargs.pop("headers", {}))
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout
            ) as client:
                response = await client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError(504, "ECURIOUS_TIMEOUT", "Ecurious provider timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                503, "ECURIOUS_UNAVAILABLE", "Ecurious provider is unavailable"
            ) from exc

        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("error", "ECURIOUS_ERROR")
            message = body.get("message", "Ecurious provider rejected the request")
            status = response.status_code if response.status_code in {404, 409, 422} else 502
            raise ProviderError(status, code, message)
        return response.json()

    async def list_bays(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/v1/bays"))["bays"]

    async def get_bay_status(self, bay_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/bays/{bay_id}/status")

    async def create_reservation(
        self,
        *,
        bay_id: str,
        license_plate: str,
        start_time: datetime,
        end_time: datetime,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/reservations",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "bay_id": bay_id,
                "license_plate": license_plate,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            },
        )

    async def get_reservation(self, reservation_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/reservations/{reservation_id}")

    async def release_reservation(self, reservation_id: str, reason: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/v1/reservations/{reservation_id}/release", json={"reason": reason}
        )

    async def poll_events(self, after: str | None) -> dict[str, Any]:
        params = {"after": after} if after else None
        return await self._request("GET", "/v1/events", params=params)

    async def simulate_plate(
        self, *, bay_id: str, license_plate: str, confidence: float
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/mock/events/plate",
            json={
                "bay_id": bay_id,
                "license_plate": license_plate,
                "confidence": confidence,
                "source_event_id": f"APP-DEMO-{uuid.uuid4().hex}",
            },
        )

    async def simulate_occupancy(
        self, *, bay_id: str, occupancy: str, license_plate: str | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/mock/bays/{bay_id}/occupancy",
            json={"occupancy": occupancy, "license_plate": license_plate},
        )

    async def simulate_charging(
        self, reservation_id: str, *, start: bool
    ) -> dict[str, Any]:
        action = "start" if start else "stop"
        return await self._request(
            "POST", f"/mock/reservations/{reservation_id}/charging/{action}"
        )

    async def simulate_no_show(self, now: datetime | None) -> dict[str, Any]:
        return await self._request(
            "POST", "/mock/no-show/run", json={"now": now.isoformat() if now else None}
        )

