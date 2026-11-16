from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import require_auth, reservation_service
from app.schemas.models import BayList, BayStatus, ErrorResponse
from app.services.reservation_service import ReservationService


router = APIRouter(
    prefix="/bays",
    tags=["bays"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=BayList)
def list_bays(
    service: Annotated[ReservationService, Depends(reservation_service)],
) -> dict:
    return service.list_bays()


@router.get(
    "/{bay_id}/status",
    response_model=BayStatus,
    responses={404: {"model": ErrorResponse}},
)
def get_bay_status(
    bay_id: str,
    service: Annotated[ReservationService, Depends(reservation_service)],
) -> dict:
    return service.get_bay_status(bay_id)

