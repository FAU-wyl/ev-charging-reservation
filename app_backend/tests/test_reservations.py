from datetime import datetime, timedelta, timezone

from app_backend.adapters.base import ProviderError
from conftest import login


def test_fixture_stations_and_mapped_availability(client):
    stations = client.get("/api/stations").json()
    assert len(stations) == 178
    mapped = next(station for station in stations if station["id"] == "GE-26")
    assert mapped["source"] == "GoingElectric fixture"
    assert mapped["reservation_supported"] is True
    ordinary = next(station for station in stations if not station["ecurious_bay_ids"])
    assert ordinary["availability_label"] == "Availability Unknown"

    availability = client.get("/api/bays/BAY-001/availability")
    assert availability.status_code == 200
    assert availability.json()["occupancy"] == "free"
    assert "Mock" in availability.json()["availability_label"]


def test_create_read_idempotent_and_cancel(
    client, driver_headers, reservation_payload
):
    headers = {**driver_headers, "Idempotency-Key": "create-once"}
    created = client.post("/api/reservations", headers=headers, json=reservation_payload)
    assert created.status_code == 201
    assert created.json()["status"] == "RESERVED"
    replay = client.post("/api/reservations", headers=headers, json=reservation_payload)
    assert replay.status_code == 201
    assert replay.json()["id"] == created.json()["id"]

    mine = client.get("/api/reservations/my", headers=driver_headers)
    assert len(mine.json()) == 1
    cancelled = client.post(
        f"/api/reservations/{created.json()['id']}/cancel", headers=driver_headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_provider_rejects_conflicting_reservations(
    client, driver_headers, reservation_payload
):
    first = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "driver-one"},
        json=reservation_payload,
    )
    assert first.status_code == 201

    second_auth = client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "OtherDriver123!"},
    ).json()
    second_headers = {"Authorization": f"Bearer {second_auth['access_token']}"}
    second_vehicle = client.post(
        "/api/vehicles",
        headers=second_headers,
        json={"license_plate": "N-CD5678", "nickname": "Other EV"},
    ).json()
    conflict_payload = {**reservation_payload, "vehicle_id": second_vehicle["id"]}
    conflict = client.post(
        "/api/reservations",
        headers={**second_headers, "Idempotency-Key": "driver-two"},
        json=conflict_payload,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "BAY_ALREADY_RESERVED"


def test_driver_cannot_read_another_users_reservation(
    client, reservation, driver_headers
):
    other_auth = client.post(
        "/api/auth/register",
        json={"email": "private@example.com", "password": "PrivateDriver123!"},
    ).json()
    response = client.get(
        f"/api/reservations/{reservation['id']}",
        headers={"Authorization": f"Bearer {other_auth['access_token']}"},
    )
    assert response.status_code == 403


def test_provider_failure_and_timeout_are_exposed_safely(
    client, adapter, driver_headers, reservation_payload
):
    adapter.fail_create = ProviderError(503, "ECURIOUS_UNAVAILABLE", "Provider unavailable")
    unavailable = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "failure"},
        json=reservation_payload,
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "ECURIOUS_UNAVAILABLE"

    adapter.fail_create = ProviderError(504, "ECURIOUS_TIMEOUT", "Provider timed out")
    timeout = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "timeout"},
        json=reservation_payload,
    )
    assert timeout.status_code == 504

