from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app_backend.adapters.base import ProviderError
from app_backend.adapters.live import EcuriousLiveAdapter, normalize_live_event
from app_backend.config import Settings
from app_backend.services.event_sync import EventSyncService


logger = logging.getLogger("ecurious_app.live_sse")


class LiveSSEListener:
    """Reconnectable SSE consumer without assuming replay or cursor support."""

    def __init__(
        self,
        adapter: EcuriousLiveAdapter,
        event_sync: EventSyncService,
        settings: Settings,
    ) -> None:
        self.adapter = adapter
        self.event_sync = event_sync
        self.settings = settings

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self.reconcile_state()
                async for event in self.adapter.stream_events():
                    await self.event_sync.process_event(event)
                    backoff = 1.0
                raise ProviderError(
                    503, "ECURIOUS_SSE_DISCONNECTED", "ECURIOUS event stream ended"
                )
            except asyncio.CancelledError:
                raise
            except ProviderError as exc:
                logger.warning(
                    "ECURIOUS event stream unavailable (%s); reconnecting", exc.code
                )
            except Exception:
                logger.exception("Unexpected ECURIOUS event stream failure")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.settings.ecurious_sse_max_backoff_seconds)

    async def run_plate_polling(self) -> None:
        """Poll the read-only latest-plate endpoint as an SSE loss/race fallback."""
        interval = max(self.settings.ecurious_plate_poll_seconds, 1.0)
        while True:
            try:
                await self.poll_latest_plate()
            except asyncio.CancelledError:
                raise
            except ProviderError as exc:
                logger.warning("Latest plate poll failed: %s", exc.code)
            except Exception:
                logger.exception("Unexpected latest plate poll failure")
            await asyncio.sleep(interval)

    async def poll_latest_plate(self) -> bool:
        latest = await self.adapter.get_latest_plate(self.settings.ecurious_lock_id)
        license_plate = str(latest.get("licensePlate") or "").strip()
        timestamp = latest.get("timestamp")
        if not license_plate or not timestamp:
            return False
        payload = {
            "type": "photo_captured",
            "lockId": str(latest.get("lockId") or self.settings.ecurious_lock_id),
            "licensePlate": license_plate,
            "plateType": latest.get("plateType"),
            "timestamp": timestamp,
        }
        stable = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        event_id = (
            "PLATE-POLL-"
            + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]
        )
        event = normalize_live_event(
            payload, event_name="photo_captured", event_id=event_id
        )
        return await self.event_sync.process_event(event)

    async def reconcile_state(self) -> int:
        """Read current lock state after connect; never performs a hardware write."""
        locks = await self.adapter.list_locks()
        processed = 0
        for lock in locks:
            lock_id = str(lock.get("id") or "")
            if not lock_id:
                continue
            timestamp = lock.get("lastSeenAt") or datetime.now(timezone.utc).isoformat()
            snapshots: list[tuple[str, dict[str, Any]]] = [
                (
                    "status_change",
                    {
                        "type": "status_change",
                        "lockId": lock_id,
                        "status": lock.get("status", "unknown"),
                        "timestamp": timestamp,
                    },
                ),
                (
                    "vehicle_presence",
                    {
                        "type": "vehicle_presence",
                        "lockId": lock_id,
                        "presence": lock.get("carPresence", "unknown"),
                        "timestamp": timestamp,
                    },
                ),
            ]
            latest_photo = lock.get("latestPhoto")
            if isinstance(latest_photo, dict) and latest_photo.get("licensePlate"):
                snapshots.append(
                    (
                        "photo_captured",
                        {
                            "type": "photo_captured",
                            "lockId": lock_id,
                            "licensePlate": latest_photo["licensePlate"],
                            "plateType": latest_photo.get("plateType"),
                            "timestamp": latest_photo.get("timestamp"),
                        },
                    )
                )
            for event_type, payload in snapshots:
                stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                # This version intentionally gives existing latest-photo snapshots one
                # re-evaluation after camera matches became sufficient for
                # automatic check-in. The identifier stays stable afterward.
                event_id = "RECONCILE-V3-" + hashlib.sha256(
                    stable.encode("utf-8")
                ).hexdigest()[:32]
                event = normalize_live_event(
                    payload, event_name=event_type, event_id=event_id
                )
                processed += int(await self.event_sync.process_event(event))
        return processed
