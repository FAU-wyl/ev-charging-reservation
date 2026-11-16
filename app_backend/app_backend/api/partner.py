from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.security import APIKeyHeader

from app_backend.errors import AppError, forbidden, not_found
from app_backend.database import now_iso
from app_backend.schemas import PartnerReservationCreate, ReservationCreate, ReservationView
from app_backend.security import token_hash
from app_backend.services.reservations import ReservationService


router = APIRouter(prefix="/api/partner/v1", tags=["partner-api"])
partner_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def partner_client(
    request: Request,
    api_key: Annotated[str | None, Depends(partner_key_header)],
):
    if not api_key:
        raise AppError(401, "INVALID_API_KEY", "A valid partner API key is required")
    with request.app.state.database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM api_clients WHERE key_hash = ? AND active = 1",
            (token_hash(api_key),),
        ).fetchone()
    if row is None:
        raise AppError(401, "INVALID_API_KEY", "A valid partner API key is required")
    return row


def require_scope(client: Any, scope: str) -> None:
    if scope not in json.loads(client["scopes_json"]):
        raise forbidden(f"Partner scope {scope} is required")


def reservation_service(request: Request) -> ReservationService:
    return ReservationService(
        request.app.state.database,
        request.app.state.adapter,
        request.app.state.hardware,
    )


@router.get("/bays")
async def list_bays(request: Request, client=Depends(partner_client)):
    """List reservable Ecurious bays with a current physical-state snapshot."""
    require_scope(client, "bays:read")
    provider_bays = await request.app.state.adapter.list_bays()
    output = []
    with request.app.state.database.connect() as connection:
        mappings = {
            row["bay_id"]: dict(row)
            for row in connection.execute(
                """SELECT bay_mappings.bay_id, stations.id AS station_id,
                          stations.name AS station_name
                   FROM bay_mappings JOIN stations ON stations.id = bay_mappings.station_id"""
            ).fetchall()
        }
    for bay in provider_bays:
        if not bay.get("reservation_supported") or bay["bay_id"] not in mappings:
            continue
        state = await request.app.state.adapter.get_bay_status(bay["bay_id"])
        output.append({**mappings[bay["bay_id"]], **bay, **state})
    return output


@router.post("/reservations", response_model=ReservationView, status_code=status.HTTP_201_CREATED)
async def create_partner_reservation(
    payload: PartnerReservationCreate,
    request: Request,
    client=Depends(partner_client),
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
):
    require_scope(client, "reservations:write")
    if not idempotency_key.strip():
        raise AppError(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required")
    database = request.app.state.database
    with database.transaction(immediate=True) as connection:
        vehicle = connection.execute(
            "SELECT id FROM vehicles WHERE user_id = ? AND license_plate = ?",
            (client["service_user_id"], payload.license_plate),
        ).fetchone()
        if vehicle is None:
            vehicle_id = f"VEH-{uuid.uuid4().hex[:12].upper()}"
            timestamp = now_iso()
            connection.execute(
                """INSERT INTO vehicles(
                       id, user_id, license_plate, nickname, is_default, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (
                    vehicle_id,
                    client["service_user_id"],
                    payload.license_plate,
                    payload.vehicle_nickname,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            vehicle_id = vehicle["id"]
    created = await reservation_service(request).create(
        client["service_user_id"],
        ReservationCreate(
            station_id=payload.station_id,
            bay_id=payload.bay_id,
            vehicle_id=vehicle_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            use_case_code=payload.use_case_code,
            membership_reference=payload.membership_reference,
        ),
        f"{client['id']}:{idempotency_key.strip()}",
    )
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE app_reservations SET api_client_id = ? WHERE id = ?",
            (client["id"], created["id"]),
        )
    return reservation_service(request).get(
        created["id"], client["service_user_id"], "DRIVER"
    )


def _owned_reservation(request: Request, reservation_id: str, client: Any):
    with request.app.state.database.connect() as connection:
        row = connection.execute(
            "SELECT id FROM app_reservations WHERE id = ? AND api_client_id = ?",
            (reservation_id, client["id"]),
        ).fetchone()
    if row is None:
        raise not_found("Partner reservation was not found")


@router.get("/reservations/{reservation_id}", response_model=ReservationView)
def get_partner_reservation(
    reservation_id: str, request: Request, client=Depends(partner_client)
):
    require_scope(client, "reservations:read")
    _owned_reservation(request, reservation_id, client)
    return reservation_service(request).get(
        reservation_id, client["service_user_id"], "DRIVER"
    )


@router.post("/reservations/{reservation_id}/cancel", response_model=ReservationView)
async def cancel_partner_reservation(
    reservation_id: str, request: Request, client=Depends(partner_client)
):
    require_scope(client, "reservations:write")
    _owned_reservation(request, reservation_id, client)
    return await reservation_service(request).cancel(
        reservation_id, client["service_user_id"]
    )
