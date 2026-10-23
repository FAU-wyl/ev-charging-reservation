from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import event_service, require_auth
from app.schemas.models import ErrorResponse, EventPage
from app.services.event_service import EventService


router = APIRouter(
    prefix="/events",
    tags=["events"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=EventPage, responses={422: {"model": ErrorResponse}})
def poll_events(
    service: Annotated[EventService, Depends(event_service)],
    after: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    return service.poll(after)

