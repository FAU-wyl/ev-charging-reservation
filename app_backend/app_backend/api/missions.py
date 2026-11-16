from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app_backend.api.dependencies import admin_user, current_user
from app_backend.schemas import ChargeWaiverRequest, UseCasePolicyUpdate
from app_backend.services.missions import MissionService


router = APIRouter(tags=["mission-areas"])


def service(request: Request) -> MissionService:
    return MissionService(
        request.app.state.database,
        request.app.state.adapter,
        request.app.state.settings,
    )


@router.get("/api/use-cases")
def list_use_cases(request: Request):
    """Public, enabled reservation scenarios such as hotel, retail and fleet."""
    return service(request).policies()


@router.get("/api/reservations/{reservation_id}/finance")
def reservation_finance(
    reservation_id: str, request: Request, user=Depends(current_user)
):
    return service(request).reservation_finance(
        reservation_id, user["id"], user["role"]
    )


@router.get("/api/admin/use-cases", dependencies=[Depends(admin_user)])
def admin_use_cases(request: Request):
    return service(request).policies(include_disabled=True)


@router.patch(
    "/api/admin/use-cases/{code}", dependencies=[Depends(admin_user)]
)
def update_use_case(code: str, payload: UseCasePolicyUpdate, request: Request):
    return service(request).update_policy(code, payload)


@router.post(
    "/api/admin/charges/{charge_id}/waive", dependencies=[Depends(admin_user)]
)
def waive_charge(charge_id: str, payload: ChargeWaiverRequest, request: Request):
    return service(request).waive_charge(charge_id, payload.reason)


@router.get("/api/admin/charges", dependencies=[Depends(admin_user)])
def list_charges(request: Request):
    return service(request).charges()


@router.post("/api/admin/lifecycle/run", dependencies=[Depends(admin_user)])
async def run_lifecycle(request: Request):
    """Operator-visible retry; the same lifecycle policy also runs in background."""
    return await service(request).process_lifecycle()


@router.post("/api/admin/privacy/purge", dependencies=[Depends(admin_user)])
def purge_privacy_data(request: Request):
    return service(request).purge_personal_data()
