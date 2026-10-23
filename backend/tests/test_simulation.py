from test_reservations import create_reservation


def test_complete_simulated_lifecycle(client, auth_headers, reservation_payload):
    reservation = create_reservation(
        client, auth_headers, reservation_payload(), "full-flow"
    ).json()
    reservation_id = reservation["reservation_id"]

    check_in = client.post(
        "/mock/events/plate",
        headers=auth_headers,
        json={
            "bay_id": "BAY-001",
            "license_plate": "M-AB1234",
            "confidence": 0.99,
        },
    )
    assert check_in.json()["outcome"] == "checked_in"

    entered = client.post(
        "/mock/bays/BAY-001/occupancy",
        headers=auth_headers,
        json={"occupancy": "occupied", "license_plate": "M-AB1234"},
    )
    assert entered.status_code == 200
    assert entered.json()["blocked"] is False

    started = client.post(
        f"/mock/reservations/{reservation_id}/charging/start", headers=auth_headers
    )
    assert started.status_code == 200
    assert started.json()["lifecycle_state"] == "CHARGING"
    assert started.json()["public_reservation"]["status"] == "checked_in"

    stopped = client.post(
        f"/mock/reservations/{reservation_id}/charging/stop", headers=auth_headers
    )
    assert stopped.status_code == 200
    assert stopped.json()["lifecycle_state"] == "CHARGE_COMPLETED"

    released = client.post(
        f"/v1/reservations/{reservation_id}/release",
        headers=auth_headers,
        json={"reason": "charging_completed"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"

    # Resource release does not pretend that an occupied bay is physically empty.
    occupied = client.get("/v1/bays/BAY-001/status", headers=auth_headers).json()
    assert occupied["occupancy"] == "occupied"
    assert occupied["protection"] == "released"

    left = client.post(
        "/mock/bays/BAY-001/occupancy",
        headers=auth_headers,
        json={"occupancy": "free"},
    )
    assert left.status_code == 200
    final_status = left.json()["bay_status"]
    assert final_status["occupancy"] == "free"
    assert final_status["protection"] == "released"
    assert final_status["active_reservation_id"] is None


def test_blocked_bay_preserves_physical_truth(
    client, auth_headers, reservation_payload
):
    reservation = create_reservation(
        client, auth_headers, reservation_payload(), "blocked"
    ).json()
    occupied = client.post(
        "/mock/bays/BAY-001/occupancy",
        headers=auth_headers,
        json={"occupancy": "occupied", "license_plate": "N-CD5678"},
    )
    assert occupied.status_code == 200
    assert occupied.json()["blocked"] is True
    assert occupied.json()["event"]["type"] == "occupancy.changed"
    assert occupied.json()["bay_status"]["occupancy"] == "occupied"
    assert occupied.json()["bay_status"]["active_reservation_id"] == reservation["reservation_id"]
    assert occupied.json()["bay_status"]["protection"] == "released"

    charging = client.post(
        f"/mock/reservations/{reservation['reservation_id']}/charging/start",
        headers=auth_headers,
    )
    assert charging.status_code == 409
    assert charging.json()["error"] == "INVALID_STATE_TRANSITION"


def test_reset_clears_dynamic_data(client, auth_headers, reservation_payload):
    reservation = create_reservation(
        client, auth_headers, reservation_payload(), "to-reset"
    ).json()
    reset = client.post("/mock/reset", headers=auth_headers)
    assert reset.status_code == 200
    assert reset.json() == {"reset": True, "seeded_bays": 3}
    missing = client.get(
        f"/v1/reservations/{reservation['reservation_id']}", headers=auth_headers
    )
    assert missing.status_code == 404
    assert client.get("/v1/events", headers=auth_headers).json()["events"] == []


def test_mock_endpoints_are_hidden_when_disabled(settings, auth_headers):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(replace(settings, mock_mode=False))
    with TestClient(app) as disabled_client:
        response = disabled_client.post("/mock/reset", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"

