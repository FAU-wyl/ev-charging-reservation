from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import bays, events, mock_control, reservations
from app.config import Settings
from app.core.exceptions import AppError
from app.db.database import Database


def create_app(
    settings: Settings | None = None, *, initialize_database: bool = True
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    database = Database(resolved_settings.database_path)
    logger = logging.getLogger("ecurious_mock")
    logger.setLevel(getattr(logging, resolved_settings.log_level, logging.INFO))

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        database.initialize()
        logger.info("Fictional Ecurious mock server started")
        yield

    application = FastAPI(
        title="Ecurious Reservation API - FICTIONAL MOCK",
        version="1.0.0-mock",
        description=(
            "Hackathon-only fictional simulator. This is not an official Ecurious API "
            "or a controller for real charging hardware."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = database

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "message": exc.message},
            headers=headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", []))
        message = first.get("msg", "Invalid request")
        if location:
            message = f"{location}: {message}"
        return JSONResponse(
            status_code=422,
            content={"error": "INVALID_REQUEST", "message": message},
        )

    application.include_router(bays.router, prefix="/v1")
    application.include_router(reservations.router, prefix="/v1")
    application.include_router(events.router, prefix="/v1")
    application.include_router(mock_control.router)
    if initialize_database:
        # Explicit app factories (notably tests) can use the app without an ASGI
        # lifespan manager. The global Uvicorn app defers this write to startup.
        database.initialize()
    return application


app = create_app(initialize_database=False)
