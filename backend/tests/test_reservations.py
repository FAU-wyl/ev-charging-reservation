from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.main import create_app


def create_reservation(client, auth_headers, payload, key="booking-1"):
    return client.post(
        "/v1/reservations",
        headers={**auth_headers, "Idempotency-Key": key},
        json=payload,
    )


def test_create_read_and_idempotent_replay(client, auth_headers, reservation_payload):
    payload = reservation_payload()
    created = create_reservation(client, auth_headers, payload)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "reserved"
    assert body["hardware_status"] == "protected"

    replay = create_reservation(client, auth_headers, payload)
    assert replay.status_code == 201
    assert replay.json() == body

    fetched = client.get(
        f"/v1/reservations/{body['reservation_id']}", headers=auth_headers
    )
    assert fetched.status_code == 200
    assert fetched.json() == body

    status = client.get("/v1/bays/BAY-001/status", headers=auth_headers).json()
    assert status["active_reservation_id"] == body["reservation_id"]
    assert status["protection"] == "protected"


def test_overlapping_reservation_and_idempotency_mismatch(
    client, auth_headers, reservation_payload
):
    first = create_reservation(client, auth_headers, reservation_payload(), "first")
    assert first.status_code == 201

    overlap = create_reservation(client, auth_headers, reservation_payload(), "second")
    assert overlap.status_code == 409
    assert overlap.json()["error"] == "BAY_ALREADY_RESERVED"

    changed = reservation_payload(bay_id="BAY-003")
    reused = create_reservation(client, auth_headers, changed, "first")
    assert reused.status_code == 409
    assert reused.json()["error"] == "IDEMPOTENCY_KEY_REUSED"


def test_concurrent_overlapping_requests_have_one_winner(
    app, auth_headers, reservation_payload
):
    payload = reservation_payload()

    async def issue_requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                client.post(
                    "/v1/reservations",
                    headers={**auth_headers, "Idempotency-Key": "concurrent-a"},
                    json=payload,
                ),
                client.post(
                    "/v1/reservations",
                    headers={**auth_headers, "Idempotency-Key": "concurrent-b"},
                    json=payload,
                ),
            )

    responses = asyncio.run(issue_requests())
    assert sorted(response.status_code for response in responses) == [201, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["error"] == "BAY_ALREADY_RESERVED"


def test_release_and_repeat_release_are_idempotent(
    client, auth_headers, reservation_payload
):
    created = create_reservation(client, auth_headers, reservation_payload()).json()
    url = f"/v1/reservations/{created['reservation_id']}/release"

    released = client.post(
        url, headers=auth_headers, json={"reason": "user_cancelled"}
    )
    assert released.status_code == 200
    assert released.json()["status"] == "cancelled"

    repeated = client.post(
        url, headers=auth_headers, json={"reason": "user_cancelled"}
    )
    assert repeated.status_code == 200
    assert repeated.json() == released.json()


def test_invalid_requests_and_missing_reservation(client, auth_headers, reservation_payload):
    no_key = client.post(
        "/v1/reservations", headers=auth_headers, json=reservation_payload()
    )
    assert no_key.status_code == 422
    assert no_key.json()["error"] == "INVALID_REQUEST"

    invalid_interval = reservation_payload()
    invalid_interval["end_time"] = invalid_interval["start_time"]
    invalid = create_reservation(client, auth_headers, invalid_interval)
    assert invalid.status_code == 422

    invalid_plate = reservation_payload(plate="@@")
    invalid = create_reservation(client, auth_headers, invalid_plate, "bad-plate")
    assert invalid.status_code == 422

    missing = client.get("/v1/reservations/RES-NOPE", headers=auth_headers)
    assert missing.status_code == 404


def test_persistence_across_app_instances(
    client, auth_headers, reservation_payload, settings
):
    created = create_reservation(client, auth_headers, reservation_payload()).json()
    second_app = create_app(settings)
    transport = httpx.ASGITransport(app=second_app)

    async def fetch():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as second:
            return await second.get(
                f"/v1/reservations/{created['reservation_id']}", headers=auth_headers
            )

    response = asyncio.run(fetch())
    assert response.status_code == 200
    assert response.json() == created

