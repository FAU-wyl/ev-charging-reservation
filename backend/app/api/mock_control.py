from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import hardware_simulator, policy_service, require_mock_mode
from app.schemas.models import (
    ChargingResult,
    ErrorResponse,
    NoShowRequest,
    NoShowResult,
    OccupancyRequest,
    OccupancyResult,
    PlateEventRequest,
    PlateEventResult,
    ResetResult,
)
from app.services.hardware_simulator import HardwareSimulator
from app.services.policy_service import PolicyService


router = APIRouter(
    prefix="/mock",
    tags=["mock controls (not part of the fictional public API)"],
    dependencies=[Depends(require_mock_mode)],
)


@router.post(
    "/events/plate",
    response_model=PlateEventResult,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def simulate_plate_event(
    payload: PlateEventRequest,
    simulator: Annotated[HardwareSimulator, Depends(hardware_simulator)],
) -> dict:
    return simulator.process_plate(payload)


@router.post(
    "/bays/{bay_id}/occupancy",
    response_model=OccupancyResult,
    responses={404: {"model": ErrorResponse}},
)
def simulate_occupancy(
    bay_id: str,
    payload: OccupancyRequest,
    simulator: Annotated[HardwareSimulator, Depends(hardware_simulator)],
) -> dict:
    return simulator.set_occupancy(bay_id, payload)


@router.post(
    "/reservations/{reservation_id}/charging/start",
    response_model=ChargingResult,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def start_charging(
    reservation_id: str,
    simulator: Annotated[HardwareSimulator, Depends(hardware_simulator)],
) -> dict:
    return simulator.start_charging(reservation_id)


@router.post(
    "/reservations/{reservation_id}/charging/stop",
    response_model=ChargingResult,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def stop_charging(
    reservation_id: str,
    simulator: Annotated[HardwareSimulator, Depends(hardware_simulator)],
) -> dict:
    return simulator.stop_charging(reservation_id)


@router.post("/no-show/run", response_model=NoShowResult)
def run_no_show_policy(
    payload: NoShowRequest,
    service: Annotated[PolicyService, Depends(policy_service)],
) -> dict:
    return service.expire_no_shows(payload.now)


@router.post("/reset", response_model=ResetResult)
def reset_environment(request: Request) -> dict:
    count = request.app.state.database.reset()
    return {"reset": True, "seeded_bays": count}

