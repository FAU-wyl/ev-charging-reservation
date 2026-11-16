from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app_backend.adapters.base import EcuriousAdapter, ProviderError


_EVENT_TYPES = {
    "status_change": "hardware.status_changed",
    "vehicle_presence": "occupancy.changed",
    "photo_captured": "plate.detected",
    "alarm": "hardware.alarm",
}


def canonical_plate(value: str | None) -> str:
    """Comparison/wire form that tolerates common European separators."""
    return "".join(character for character in (value or "").upper() if character.isalnum())


def _timestamp(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def normalize_live_event(
    payload: dict[str, Any], *, event_name: str | None = None, event_id: str | None = None
) -> dict[str, Any]:
    raw_hardware_type = str(payload.get("type") or event_name or "unknown").strip()
    hardware_type = raw_hardware_type
    for separator in (":", "/", "."):
        hardware_type = hardware_type.rsplit(separator, 1)[-1]
    hardware_type = hardware_type.lower()
    lock_id = str(payload.get("lockId") or payload.get("lock_id") or "")
    normalized_timestamp = _timestamp(payload.get("timestamp"))
    identity = event_id or payload.get("eventId") or payload.get("id")
    if not identity:
        stable = json.dumps(
            {"event": raw_hardware_type, "lock": lock_id, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        identity = "LIVE-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]
    event: dict[str, Any] = {
        "event_id": str(identity),
        "type": _EVENT_TYPES.get(hardware_type, f"hardware.{hardware_type}"),
        "bay_id": lock_id,
        "timestamp": normalized_timestamp,
        "timestamp_valid": normalized_timestamp is not None,
        "hardware_event_type": raw_hardware_type,
    }
    if hardware_type == "photo_captured":
        event["license_plate"] = str(payload.get("licensePlate") or "").strip().upper()
    elif hardware_type == "vehicle_presence":
        presence = str(payload.get("presence") or "unknown").lower()
        event["occupancy"] = {"vacant": "free", "occupied": "occupied"}.get(
            presence, "unknown"
        )
    elif hardware_type == "status_change":
        event["hardware_status"] = str(payload.get("status") or "unknown").lower()
    elif hardware_type == "alarm":
        event["error"] = str(payload.get("error") or "unknown_hardware_alarm")
    return event


async def parse_sse_lines(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    event_name: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except (json.JSONDecodeError, TypeError):
                    payload = None
                if isinstance(payload, dict):
                    yield normalize_live_event(
                        payload, event_name=event_name, event_id=event_id
                    )
            event_name = None
            event_id = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            yield normalize_live_event(payload, event_name=event_name, event_id=event_id)


class EcuriousLiveAdapter(EcuriousAdapter):
    """Official ECURIOUS REST/SSE hardware adapter.

    Reservation ownership remains in the application database because the real
    hardware API does not expose a reservation resource.
    """

    mode = "live"
    supports_sse = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        lock_id: str,
        timeout: float = 10.0,
        offline_seconds: int = 120,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or api_key == "REPLACE_WITH_YOUR_KEY":
            raise ValueError("A real ECURIOUS_API_KEY is required in live mode")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.lock_id = lock_id
        self.timeout = timeout
        self.offline_seconds = offline_seconds
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, read=timeout),
            headers={"X-API-Key": api_key, "User-Agent": "ecurious-reservation-backend/2.0"},
            transport=transport,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _provider_error(response: httpx.Response) -> ProviderError:
        status = response.status_code
        if status == 401:
            return ProviderError(502, "ECURIOUS_AUTH_FAILED", "ECURIOUS rejected the API key")
        if status == 403:
            return ProviderError(502, "ECURIOUS_ACCESS_DENIED", "ECURIOUS denied access to this lock")
        if status == 404:
            return ProviderError(404, "ECURIOUS_LOCK_NOT_FOUND", "The ECURIOUS lock was not found")
        if status == 429:
            return ProviderError(503, "ECURIOUS_RATE_LIMITED", "ECURIOUS rate limit reached; retry later")
        if status >= 500:
            return ProviderError(503, "ECURIOUS_UNAVAILABLE", "ECURIOUS hardware service is unavailable")
        return ProviderError(502, "ECURIOUS_REJECTED", "ECURIOUS rejected the hardware request")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError(504, "ECURIOUS_TIMEOUT", "ECURIOUS hardware request timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderError(503, "ECURIOUS_UNAVAILABLE", "ECURIOUS hardware service is unavailable") from exc
        if response.is_error:
            raise self._provider_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(502, "ECURIOUS_INVALID_RESPONSE", "ECURIOUS returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise ProviderError(502, "ECURIOUS_INVALID_RESPONSE", "ECURIOUS returned an invalid response")
        return body

    def _is_online(self, lock: dict[str, Any]) -> bool:
        explicit = lock.get("isOnline", lock.get("online"))
        if isinstance(explicit, bool):
            return explicit
        last_seen = lock.get("lastSeenAt")
        if not isinstance(last_seen, str):
            return False
        try:
            observed = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        except ValueError:
            return False
        if observed.tzinfo is None:
            return False
        return observed >= datetime.now(timezone.utc) - timedelta(seconds=self.offline_seconds)

    def _bay(self, raw: dict[str, Any]) -> dict[str, Any]:
        status = str(raw.get("status") or "unknown").lower()
        presence = str(raw.get("carPresence") or "unknown").lower()
        return {
            "bay_id": str(raw.get("id") or ""),
            "occupancy": {"vacant": "free", "occupied": "occupied"}.get(presence, "unknown"),
            "protection": {"raised": "protected", "lowered": "released"}.get(status, "unknown"),
            "active_reservation_id": None,
            "last_seen_at": raw.get("lastSeenAt"),
            "hardware_online": self._is_online(raw),
            "barrier_status": status,
            "battery_12v": raw.get("battery12V"),
            "battery_3_7v": raw.get("battery3_7V"),
            "signal_strength_4g": raw.get("signalStrength4G"),
            "water_ingress": bool(raw.get("waterIngress", False)),
            "active_errors": list(raw.get("activeErrors") or []),
        }

    async def list_locks(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v1/locks")
        locks = body.get("locks")
        if not isinstance(locks, list):
            raise ProviderError(502, "ECURIOUS_INVALID_RESPONSE", "ECURIOUS response has no locks array")
        return [item for item in locks if isinstance(item, dict)]

    async def list_bays(self) -> list[dict[str, Any]]:
        locks = await self.list_locks()
        if self.lock_id:
            locks = [lock for lock in locks if str(lock.get("id")) == self.lock_id]
        return [
            {
                "bay_id": str(lock.get("id") or ""),
                "reservation_supported": True,
                "charger_reference": None,
                "hardware_online": self._is_online(lock),
                "has_camera": bool(lock.get("hasCamera", False)),
            }
            for lock in locks
        ]

    async def get_bay_status(self, bay_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/api/v1/locks/{bay_id}")
        raw = body.get("lock", body)
        if not isinstance(raw, dict):
            raise ProviderError(502, "ECURIOUS_INVALID_RESPONSE", "ECURIOUS lock response is invalid")
        result = self._bay(raw)
        if not result["bay_id"]:
            result["bay_id"] = bay_id
        return result

    async def unlock(self, lock_id: str, *, cancel_auto_lift: bool = False) -> dict[str, Any]:
        body = await self._request(
            "POST", f"/api/v1/locks/{lock_id}/unlock", json={"cancelAutoLift": cancel_auto_lift}
        )
        return {**body, "command_accepted": bool(body.get("success")), "physical_state_confirmed": False}

    async def lock(self, lock_id: str, *, cancel_auto_lift: bool = False) -> dict[str, Any]:
        body = await self._request(
            "POST", f"/api/v1/locks/{lock_id}/lock", json={"cancelAutoLift": cancel_auto_lift}
        )
        return {**body, "command_accepted": bool(body.get("success")), "physical_state_confirmed": False}

    async def set_whitelist(self, lock_id: str, plates: list[str]) -> dict[str, Any]:
        normalized = list(dict.fromkeys(canonical_plate(plate) for plate in plates))
        if any(not plate or len(plate) > 20 for plate in normalized):
            raise ProviderError(422, "INVALID_LICENSE_PLATE", "Whitelist contains an invalid license plate")
        if len(normalized) > 10:
            raise ProviderError(409, "WHITELIST_CAPACITY_EXCEEDED", "Hardware whitelist supports at most 10 plates")
        body = await self._request(
            "POST", f"/api/v1/locks/{lock_id}/whitelist", json={"plates": normalized}
        )
        return {**body, "whitelist": body.get("whitelist", normalized), "wildcard": False}

    async def get_latest_plate(self, lock_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/locks/{lock_id}/plate/latest")

    async def set_keep_state(self, lock_id: str, state: str) -> dict[str, Any]:
        if state not in {"normal", "keep_open", "keep_closed"}:
            raise ProviderError(422, "INVALID_KEEP_STATE", "Invalid ECURIOUS keep state")
        return await self._request(
            "POST", f"/api/v1/locks/{lock_id}/keep-state", json={"state": state}
        )

    async def stream_events(self) -> AsyncIterator[dict[str, Any]]:
        timeout = httpx.Timeout(self.timeout, read=None)
        try:
            async with self._client.stream(
                "GET", "/events", params={"apiKey": self._api_key}, timeout=timeout
            ) as response:
                if response.is_error:
                    await response.aread()
                    raise self._provider_error(response)
                async for event in parse_sse_lines(response.aiter_lines()):
                    if event["bay_id"]:
                        yield event
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(504, "ECURIOUS_SSE_TIMEOUT", "ECURIOUS event stream timed out") from exc
        except httpx.RequestError as exc:
            raise ProviderError(503, "ECURIOUS_SSE_UNAVAILABLE", "ECURIOUS event stream disconnected") from exc

    async def create_reservation(self, **_: Any) -> dict[str, Any]:
        raise ProviderError(501, "LIVE_RESERVATIONS_ARE_LOCAL", "Live reservations are stored by the app")

    async def get_reservation(self, reservation_id: str) -> dict[str, Any]:
        raise ProviderError(501, "LIVE_RESERVATIONS_ARE_LOCAL", "Live reservations are stored by the app")

    async def release_reservation(self, reservation_id: str, reason: str) -> dict[str, Any]:
        raise ProviderError(501, "LIVE_RESERVATIONS_ARE_LOCAL", "Live reservations are stored by the app")

    async def poll_events(self, after: str | None) -> dict[str, Any]:
        return {"events": [], "next_cursor": after}
