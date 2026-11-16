"""Read-only ECURIOUS connectivity check. This script never sends POST requests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_backend.adapters.live import EcuriousLiveAdapter
from app_backend.config import Settings


async def main() -> None:
    settings = Settings.from_env()
    if settings.ecurious_api_mode != "live":
        raise SystemExit("Set ECURIOUS_API_MODE=live before running this check")
    adapter = EcuriousLiveAdapter(
        settings.ecurious_api_base_url,
        settings.ecurious_api_key,
        lock_id=settings.ecurious_lock_id,
        timeout=settings.ecurious_timeout_seconds,
        offline_seconds=settings.ecurious_offline_seconds,
    )
    try:
        bays = await adapter.list_bays()
        if not bays:
            raise SystemExit("No assigned lock matched ECURIOUS_LOCK_ID")
        for bay in bays:
            status = await adapter.get_bay_status(bay["bay_id"])
            print(
                f"{bay['bay_id']}: online={status['hardware_online']} "
                f"barrier={status['barrier_status']} occupancy={status['occupancy']} "
                f"last_seen={status['last_seen_at']}"
            )
    finally:
        await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
