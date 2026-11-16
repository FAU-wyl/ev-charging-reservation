from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.core.exceptions import AppError
from app.db.database import Database
from app.services.event_service import EventService
from app.services.hardware_simulator import HardwareSimulator
from app.services.policy_service import PolicyService
from app.services.reservation_service import ReservationService


bearer_scheme = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    settings: Settings = request.app.state.settings
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, settings.token)
    ):
        raise AppError(401, "UNAUTHORIZED", "A valid Bearer token is required")


def require_mock_mode(
    request: Request,
    _authenticated: Annotated[None, Depends(require_auth)],
) -> None:
    if not request.app.state.settings.mock_mode:
        raise AppError(404, "NOT_FOUND", "Mock control endpoints are disabled")


def reservation_service(request: Request) -> ReservationService:
    return ReservationService(request.app.state.database)


def event_service(request: Request) -> EventService:
    settings: Settings = request.app.state.settings
    return EventService(request.app.state.database, settings.event_page_size)


def hardware_simulator(request: Request) -> HardwareSimulator:
    return HardwareSimulator(request.app.state.database, request.app.state.settings)


def policy_service(request: Request) -> PolicyService:
    return PolicyService(request.app.state.database, request.app.state.settings)

