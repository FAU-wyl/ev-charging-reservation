from fastapi import APIRouter, Depends, Request

from app_backend.api.dependencies import admin_user, current_user
from app_backend.schemas import HardwareCommandView, KeepStateRequest


router = APIRouter(prefix="/api/bays", tags=["hardware"])


@router.post("/{lock_id}/unlock", response_model=HardwareCommandView)
async def manual_unlock(
    lock_id: str, request: Request, user=Depends(current_user)
):
    """Manual LPR fallback for the valid reservation holder or an admin."""
    return await request.app.state.hardware.manual_unlock(lock_id, user)


@router.post("/{lock_id}/lock", response_model=HardwareCommandView)
async def admin_lock(lock_id: str, request: Request, user=Depends(admin_user)):
    return await request.app.state.hardware.admin_lock(lock_id, user)


@router.post("/{lock_id}/keep-state")
async def keep_state(
    lock_id: str,
    payload: KeepStateRequest,
    request: Request,
    user=Depends(admin_user),
):
    return await request.app.state.hardware.set_keep_state(lock_id, payload.state, user)
