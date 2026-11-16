from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.adapters.http_mock import HttpMockEcuriousAdapter
from app_backend.adapters.live import EcuriousLiveAdapter
from app_backend.api import admin, auth, charging, hardware, reservations, stations, vehicles
from app_backend.config import Settings
from app_backend.database import Database
from app_backend.errors import AppError
from app_backend.services.event_sync import EventSyncService
from app_backend.services.hardware import HardwareService
from app_backend.services.live_sse import LiveSSEListener


logger = logging.getLogger("ecurious_app")


def create_app(
    settings: Settings | None = None,
    adapter: EcuriousAdapter | None = None,
    *,
    initialize_database: bool = True,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    logging.getLogger("ecurious_app").setLevel(
        getattr(logging, resolved.log_level, logging.INFO)
    )
    # The live SSE endpoint may require apiKey in its query string. Never allow
    # HTTP client request logging to expose that URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    database = Database(resolved)
    if adapter is not None:
        provider = adapter
    elif resolved.ecurious_api_mode == "live":
        provider = EcuriousLiveAdapter(
            resolved.ecurious_api_base_url,
            resolved.ecurious_api_key,
            lock_id=resolved.ecurious_lock_id,
            timeout=resolved.ecurious_timeout_seconds,
            offline_seconds=resolved.ecurious_offline_seconds,
        )
    else:
        provider = HttpMockEcuriousAdapter(
            resolved.ecurious_mock_base_url,
            resolved.ecurious_mock_token,
            timeout=resolved.ecurious_timeout_seconds,
        )
    hardware_service = HardwareService(database, provider, resolved)
    event_sync = EventSyncService(database, provider, resolved)
    live_sse = (
        LiveSSEListener(provider, event_sync, resolved)
        if provider.mode == "live" and provider.supports_sse
        else None
    )

    async def polling_loop() -> None:
        while True:
            try:
                await event_sync.sync_once()
            except ProviderError as exc:
                logger.warning("Event sync provider error: %s", exc.code)
            except Exception:
                logger.exception("Unexpected event sync failure")
            await asyncio.sleep(resolved.event_poll_seconds)

    async def hardware_maintenance_loop() -> None:
        while True:
            # Never write merely because the app started; first wait for a due
            # reservation transition, then reconcile the persisted desired set.
            await asyncio.sleep(max(resolved.event_poll_seconds, 1.0))
            try:
                await hardware_service.reconcile_transitions()
            except Exception:
                logger.exception("Unexpected hardware maintenance failure")

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        database.initialize()
        if provider.mode == "live":
            hardware_service.prime_background_monitor()
        tasks: list[asyncio.Task] = []
        if resolved.event_poll_enabled and provider.mode == "mock":
            tasks.append(asyncio.create_task(polling_loop(), name="ecurious-event-poll"))
        if live_sse and resolved.ecurious_sse_enabled:
            tasks.append(asyncio.create_task(live_sse.run(), name="ecurious-live-sse"))
        if live_sse and resolved.ecurious_plate_poll_seconds > 0:
            tasks.append(
                asyncio.create_task(
                    live_sse.run_plate_polling(), name="ecurious-live-plate-poll"
                )
            )
        if provider.mode == "live":
            tasks.append(
                asyncio.create_task(
                    hardware_maintenance_loop(), name="ecurious-hardware-maintenance"
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await provider.close()

    application = FastAPI(
        title="Ecurious EV Reservation App Backend",
        version="2.0.0",
        description=(
            "User-facing reservation API with explicit mock and official ECURIOUS "
            "parking-lock hardware modes."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.database = database
    application.state.adapter = provider
    application.state.event_sync = event_sync
    application.state.hardware = hardware_service
    application.state.live_sse = live_sse
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "message": exc.message},
            headers=headers,
        )

    @application.exception_handler(ProviderError)
    async def provider_error_handler(_request: Request, exc: ProviderError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", []))
        message = first.get("msg", "Invalid request")
        return JSONResponse(
            status_code=422,
            content={
                "error": "INVALID_REQUEST",
                "message": f"{location}: {message}" if location else message,
            },
        )

    @application.get("/api/health", tags=["system"])
    def health():
        return {
            "status": "ok",
            "provider": "ecurious-live-hardware" if provider.mode == "live" else "fictional-ecurious-mock",
            "mode": provider.mode,
        }

    application.include_router(auth.router)
    application.include_router(vehicles.router)
    application.include_router(stations.router)
    application.include_router(reservations.router)
    application.include_router(charging.router)
    application.include_router(hardware.router)
    application.include_router(admin.router)
    if initialize_database:
        database.initialize()
    return application


app = create_app(initialize_database=False)
