from typing import Annotated

from fastapi import APIRouter, Depends, Header, status

from app.api.dependencies import require_auth, reservation_service
from app.schemas.models import (
    CreateReservation,
    ErrorResponse,
    ReleaseReservation,
    Reservation,
)
from app.services.reservation_service import ReservationService


router = APIRouter(
    prefix="/reservations",
    tags=["reservations"],
    dependencies=[Depends(require_auth)],
)


@router.post(
    "",
    response_model=Reservation,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def create_reservation(
    payload: CreateReservation,
    service: Annotated[ReservationService, Depends(reservation_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict:
    return service.create(payload, idempotency_key)


@router.get(
    "/{reservation_id}",
    response_model=Reservation,
    responses={404: {"model": ErrorResponse}},
)
def get_reservation(
    reservation_id: str,
    service: Annotated[ReservationService, Depends(reservation_service)],
) -> dict:
    return service.get(reservation_id)


@router.post(
    "/{reservation_id}/release",
    response_model=Reservation,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def release_reservation(
    reservation_id: str,
    payload: ReleaseReservation,
    service: Annotated[ReservationService, Depends(reservation_service)],
) -> dict:
    return service.release(reservation_id, payload.reason)

