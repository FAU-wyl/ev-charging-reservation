from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app_backend.main import create_app
from conftest import FakeAdapter, login


def test_no_show_reminder_record_fee_and_safe_manual_review(
    client, app, adapter, reservation, admin_headers, driver_headers
):
    reminder = client.post("/api/admin/lifecycle/run", headers=admin_headers)
    assert reminder.status_code == 200
    assert reminder.json()["reminders"] == 1

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE app_reservations SET start_time = ? WHERE id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
                reservation["id"],
            ),
        )
    expired = client.post("/api/admin/lifecycle/run", headers=admin_headers)
    assert expired.json()["expired"] == 1
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "EXPIRED"
    finance = client.get(
        f"/api/reservations/{reservation['id']}/finance", headers=driver_headers
    ).json()
    assert {item["charge_type"] for item in finance["charges"]} == {
        "RESERVATION_FEE",
        "NO_SHOW_FEE",
    }
    assert any(item["notification_type"] == "ARRIVAL_REMINDER" for item in finance["notifications"])
    with app.state.database.connect() as connection:
        case = connection.execute(
            "SELECT * FROM no_show_cases WHERE reservation_id = ?", (reservation["id"],)
        ).fetchone()
    assert case["status"] == "AUTO_CONFIRMED"

    with app.state.database.connect() as connection:
        alternative_station = connection.execute(
            "SELECT station_id FROM bay_mappings WHERE bay_id = 'BAY-003'"
        ).fetchone()["station_id"]
    now = datetime.now(timezone.utc)
    review_reservation = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "no-show-review"},
        json={
            "station_id": alternative_station,
            "bay_id": "BAY-003",
            "vehicle_id": reservation["vehicle_id"],
            "start_time": (now - timedelta(minutes=20)).isoformat(),
            "end_time": (now + timedelta(hours=1)).isoformat(),
        },
    ).json()
    adapter.bays["BAY-003"]["occupancy"] = "occupied"
    reviewed = client.post("/api/admin/lifecycle/run", headers=admin_headers)
    assert reviewed.json()["review_required"] == 1
    current_review = client.get(
        f"/api/reservations/{review_reservation['id']}", headers=driver_headers
    ).json()
    assert current_review["status"] == "ATTENTION_REQUIRED"
    review_finance = client.get(
        f"/api/reservations/{review_reservation['id']}/finance", headers=driver_headers
    ).json()
    assert all(item["charge_type"] != "NO_SHOW_FEE" for item in review_finance["charges"])


def test_blocked_bay_notifies_escalates_and_reallocates(
    client, reservation, admin_headers, driver_headers
):
    client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "license_plate": "N-CD5678", "confidence": 0.96},
    )
    client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "occupancy": "occupied"},
    )
    alternatives = client.get(
        f"/api/admin/reservations/{reservation['id']}/alternatives",
        headers=admin_headers,
    )
    assert alternatives.status_code == 200
    assert any(item["bay_id"] == "BAY-003" for item in alternatives.json())

    blocked_alert = next(
        item
        for item in client.get("/api/admin/alerts", headers=admin_headers).json()
        if item["alert_type"] == "BLOCKED_BAY"
    )
    escalated = client.post(
        f"/api/admin/alerts/{blocked_alert['id']}/escalate",
        headers=admin_headers,
        json={"level": "ON_SITE", "note": "Send an on-site operator"},
    )
    assert escalated.json()["escalation_level"] == "ON_SITE"

    moved = client.post(
        f"/api/admin/reservations/{reservation['id']}/reallocate",
        headers=admin_headers,
        json={"target_bay_id": "BAY-003"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["bay_id"] == "BAY-003"
    assert moved.json()["status"] == "RESERVED"
    finance = client.get(
        f"/api/reservations/{reservation['id']}/finance", headers=driver_headers
    ).json()
    assert any(item["notification_type"] == "BAY_REALLOCATED" for item in finance["notifications"])


def test_lpr_vehicle_change_checks_in_and_masks_logs(
    client, app, reservation, admin_headers, driver_headers
):
    replacement = client.post(
        "/api/vehicles",
        headers=driver_headers,
        json={"license_plate": "京A12345", "nickname": "Visitor EV"},
    ).json()
    changed = client.post(
        f"/api/reservations/{reservation['id']}/vehicle",
        headers=driver_headers,
        json={"vehicle_id": replacement["id"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["license_plate"] == "京A12345"

    client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "license_plate": "京A12345", "confidence": 0.91},
    )
    assert client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()["status"] == "CHECKED_IN"
    event = next(
        item
        for item in client.get("/api/admin/events", headers=admin_headers).json()
        if item["event_type"] == "plate.detected"
    )
    assert event["masked_plate"] != "京A12345"
    purged = app.state.missions.purge_personal_data(
        at=datetime.now(timezone.utc) + timedelta(days=31)
    )
    assert purged["events_deleted"] >= 1
    with app.state.database.connect() as connection:
        latest = connection.execute(
            "SELECT latest_plate FROM hardware_locks WHERE lock_id = 'BAY-001'"
        ).fetchone()
    assert latest["latest_plate"] is None


def test_partner_rest_api_key_scopes_and_unified_reservation_id(settings):
    partner_settings = replace(settings, partner_api_key="partner-test-secret")
    app = create_app(partner_settings, FakeAdapter())
    with TestClient(app) as client:
        assert client.get("/api/partner/v1/bays").status_code == 401
        headers = {"X-API-Key": "partner-test-secret"}
        bays = client.get("/api/partner/v1/bays", headers=headers)
        assert bays.status_code == 200
        now = datetime.now(timezone.utc)
        created = client.post(
            "/api/partner/v1/reservations",
            headers={**headers, "Idempotency-Key": "partner-booking-1"},
            json={
                "station_id": "GE-26",
                "bay_id": "BAY-001",
                "license_plate": "M-PA1234",
                "vehicle_nickname": "Partner EV",
                "start_time": now.isoformat(),
                "end_time": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert created.status_code == 201, created.text
        reservation_id = created.json()["id"]
        assert reservation_id.startswith("APP-RES-")
        assert client.get(
            f"/api/partner/v1/reservations/{reservation_id}", headers=headers
        ).status_code == 200
        assert client.post(
            f"/api/partner/v1/reservations/{reservation_id}/cancel", headers=headers
        ).json()["status"] == "CANCELLED"
        assert client.get(
            f"/api/partner/v1/reservations/{reservation_id}",
            headers={"X-API-Key": "wrong"},
        ).status_code == 401
        description = client.get("/openapi.json").json()["info"]["description"]
        assert "does not claim conformance" in description


def test_configurable_hotel_use_case_is_fully_operable(
    client, reservation_payload, driver_headers, admin_headers
):
    no_reference = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "hotel-no-reference"},
        json={**reservation_payload, "use_case_code": "HOTEL"},
    )
    assert no_reference.status_code == 409
    assert no_reference.json()["error"] == "USE_CASE_REFERENCE_REQUIRED"

    created = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "hotel-booking"},
        json={
            **reservation_payload,
            "use_case_code": "HOTEL",
            "membership_reference": "HOTEL-ROOM-204",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["use_case_code"] == "HOTEL"
    finance = client.get(
        f"/api/reservations/{created.json()['id']}/finance", headers=driver_headers
    ).json()
    assert finance["charges"][0]["amount_eur"] == 2.0

    disabled = client.patch(
        "/api/admin/use-cases/HOTEL",
        headers=admin_headers,
        json={"enabled": False},
    )
    assert disabled.json()["enabled"] is False


def test_utilisation_analytics_has_range_sensor_rate_and_idle_time(
    client, app, admin_headers
):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            """INSERT INTO processed_events(
                   external_event_id, event_type, bay_id, processing_result,
                   timestamp, processed_at
               ) VALUES ('UTIL-OCC', 'occupancy.changed', 'BAY-001',
                         'OCCUPANCY_OCCUPIED_NO_RESERVATION', ?, ?)""",
            ((start + timedelta(minutes=10)).isoformat(), end.isoformat()),
        )
        connection.execute(
            """INSERT INTO processed_events(
                   external_event_id, event_type, bay_id, processing_result,
                   timestamp, processed_at
               ) VALUES ('UTIL-FREE', 'occupancy.changed', 'BAY-001',
                         'OCCUPANCY_FREE_NO_RESERVATION', ?, ?)""",
            ((start + timedelta(minutes=40)).isoformat(), end.isoformat()),
        )
    response = client.get(
        "/api/admin/analytics",
        headers=admin_headers,
        params={"from_time": start.isoformat(), "to_time": end.isoformat()},
    )
    assert response.status_code == 200
    analytics = response.json()
    assert analytics["occupancy_rate"] == 16.7
    assert analytics["idle_hours"] == 2.5
    assert analytics["data_sources"]["occupancy"] == "Mock occupancy events (simulated)"
    assert analytics["range"]["from"]


def test_business_case_driver_breakdown_cancellation_and_no_show_waiver(
    client, reservation, admin_headers, driver_headers
):
    client.post("/api/admin/demo/no-show", headers=admin_headers, json={})
    finance = client.get(
        f"/api/reservations/{reservation['id']}/finance", headers=driver_headers
    ).json()
    assert finance["label"].startswith("Estimated / Simulation")
    no_show = next(
        charge for charge in finance["charges"] if charge["charge_type"] == "NO_SHOW_FEE"
    )
    waived = client.post(
        f"/api/admin/charges/{no_show['id']}/waive",
        headers=admin_headers,
        json={"reason": "OCR evidence was inconclusive"},
    )
    assert waived.status_code == 200
    assert waived.json()["status"] == "WAIVED"
    analytics = client.get("/api/admin/analytics", headers=admin_headers).json()
    assert analytics["waived_fees_eur"] == 5.0
    assert analytics["assumptions"]["cpo_monthly_subscription_eur"] == 49.0
    assert analytics["estimated_platform_revenue_eur"] > 0

    now = datetime.now(timezone.utc)
    future = client.post(
        "/api/reservations",
        headers={**driver_headers, "Idempotency-Key": "free-cancellation"},
        json={
            "station_id": "GE-26",
            "bay_id": "BAY-001",
            "vehicle_id": reservation["vehicle_id"],
            "start_time": (now + timedelta(hours=2)).isoformat(),
            "end_time": (now + timedelta(hours=3)).isoformat(),
        },
    ).json()
    cancelled = client.post(
        f"/api/reservations/{future['id']}/cancel", headers=driver_headers
    )
    assert cancelled.json()["status"] == "CANCELLED"
    future_finance = client.get(
        f"/api/reservations/{future['id']}/finance", headers=driver_headers
    ).json()
    assert future_finance["charges"][0]["status"] == "WAIVED"
    assert future_finance["charges"][0]["waiver_reason"] == "Free cancellation window"
