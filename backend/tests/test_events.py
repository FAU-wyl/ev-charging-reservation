from datetime import datetime, timedelta, timezone

from test_reservations import create_reservation


def test_correct_plate_checks_in_and_duplicate_event_has_no_side_effect(
    client, auth_headers, reservation_payload
):
    reservation = create_reservation(
        client, auth_headers, reservation_payload(), "plate-ok"
    ).json()
    event_payload = {
        "bay_id": "BAY-001",
        "license_plate": "M-AB1234",
        "confidence": 0.97,
        "source_event_id": "camera-event-1",
    }
    first = client.post("/mock/events/plate", headers=auth_headers, json=event_payload)
    assert first.status_code == 200
    assert first.json()["matched"] is True
    assert first.json()["outcome"] == "checked_in"
    assert first.json()["duplicate"] is False

    duplicate = client.post("/mock/events/plate", headers=auth_headers, json=event_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["event"] == first.json()["event"]

    current = client.get(
        f"/v1/reservations/{reservation['reservation_id']}", headers=auth_headers
    ).json()
    assert current["status"] == "checked_in"
    assert current["hardware_status"] == "released"

    events = client.get("/v1/events", headers=auth_headers).json()["events"]
    assert len(events) == 1
    assert events[0]["type"] == "plate.detected"


def test_wrong_plate_does_not_authorize_or_release_protection(
    client, auth_headers, reservation_payload
):
    reservation = create_reservation(
        client, auth_headers, reservation_payload(), "plate-wrong"
    ).json()
    result = client.post(
        "/mock/events/plate",
        headers=auth_headers,
        json={
            "bay_id": "BAY-001",
            "license_plate": "N-CD5678",
            "confidence": 0.98,
        },
    )
    assert result.status_code == 200
    assert result.json()["matched"] is False
    assert result.json()["outcome"] == "plate_mismatch"

    current = client.get(
        f"/v1/reservations/{reservation['reservation_id']}", headers=auth_headers
    ).json()
    status = client.get("/v1/bays/BAY-001/status", headers=auth_headers).json()
    assert current["status"] == "reserved"
    assert status["protection"] == "protected"


def test_event_polling_cursor_is_stable(client, auth_headers, reservation_payload):
    create_reservation(client, auth_headers, reservation_payload(), "events")
    client.post(
        "/mock/events/plate",
        headers=auth_headers,
        json={
            "bay_id": "BAY-001",
            "license_plate": "N-CD5678",
            "confidence": 0.9,
        },
    )
    first = client.get("/v1/events", headers=auth_headers)
    assert first.status_code == 200
    assert len(first.json()["events"]) == 1
    cursor = first.json()["next_cursor"]

    repeated = client.get("/v1/events", headers=auth_headers)
    assert repeated.json()["events"] == first.json()["events"]

    after = client.get("/v1/events", headers=auth_headers, params={"after": cursor})
    assert after.status_code == 200
    assert after.json() == {"events": [], "next_cursor": cursor}

    invalid = client.get(
        "/v1/events", headers=auth_headers, params={"after": "bad-cursor"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "INVALID_CURSOR"


def test_no_show_expires_reservation_and_emits_event(
    client, auth_headers, reservation_payload
):
    reservation = create_reservation(
        client,
        auth_headers,
        reservation_payload(
            start_delta=timedelta(minutes=-5), end_delta=timedelta(hours=1)
        ),
        "no-show",
    ).json()
    run = client.post("/mock/no-show/run", headers=auth_headers, json={})
    assert run.status_code == 200
    assert run.json()["expired_reservation_ids"] == [reservation["reservation_id"]]

    current = client.get(
        f"/v1/reservations/{reservation['reservation_id']}", headers=auth_headers
    ).json()
    assert current["status"] == "expired"
    event_types = [
        event["type"]
        for event in client.get("/v1/events", headers=auth_headers).json()["events"]
    ]
    assert "reservation.expired" in event_types


def test_plate_validation_and_source_id_reuse(client, auth_headers, reservation_payload):
    create_reservation(client, auth_headers, reservation_payload(), "source-reuse")
    first = {
        "bay_id": "BAY-001",
        "license_plate": "M-AB1234",
        "confidence": 0.9,
        "event_id": "external-1",
    }
    assert client.post("/mock/events/plate", headers=auth_headers, json=first).status_code == 200
    changed = {**first, "license_plate": "N-CD5678"}
    reused = client.post("/mock/events/plate", headers=auth_headers, json=changed)
    assert reused.status_code == 409
    assert reused.json()["error"] == "SOURCE_EVENT_ID_REUSED"

    malformed = client.post(
        "/mock/events/plate",
        headers=auth_headers,
        json={"bay_id": "BAY-001", "license_plate": "!", "confidence": 2},
    )
    assert malformed.status_code == 422

