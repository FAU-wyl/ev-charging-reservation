from fastapi import APIRouter, Depends, Request, status

from app_backend.api.dependencies import current_user
from app_backend.schemas import ChargingSessionCreate, ChargingSessionView
from app_backend.services.charging import ChargingService


router = APIRouter(prefix="/api/charging/sessions", tags=["charging"])


def service(request: Request) -> ChargingService:
    return ChargingService(
        request.app.state.database,
        request.app.state.adapter,
        request.app.state.settings,
    )


@router.post("", response_model=ChargingSessionView, status_code=status.HTTP_201_CREATED)
async def start_session(
    payload: ChargingSessionCreate, request: Request, user=Depends(current_user)
):
    return await service(request).start(payload.reservation_id, user)


@router.get("/{session_id}", response_model=ChargingSessionView)
def get_session(session_id: str, request: Request, user=Depends(current_user)):
    return service(request).get(session_id, user)


@router.post("/{session_id}/stop", response_model=ChargingSessionView)
async def stop_session(session_id: str, request: Request, user=Depends(current_user)):
    return await service(request).stop(session_id, user)
