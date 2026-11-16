def test_correct_plate_immediately_checks_in_and_event_is_deduplicated(
    client, reservation, admin_headers, driver_headers
):
    demo = client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "license_plate": "M-AB1234", "confidence": 0.98},
    )
    assert demo.status_code == 200
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "CHECKED_IN"

    events = client.get("/api/admin/events", headers=admin_headers).json()
    assert len(events) == 1
    assert events[0]["processing_result"] == "CHECK_IN_SUCCESSFUL"
    assert events[0]["masked_plate"] == "M-***34"

    repeated = client.post("/api/admin/sync", headers=admin_headers)
    assert repeated.status_code == 200
    assert repeated.json()["processed"] == 0
    assert len(client.get("/api/admin/events", headers=admin_headers).json()) == 1

    occupied = client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "occupancy": "occupied", "license_plate": "M-AB1234"},
    )
    assert occupied.status_code == 200
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "CHECKED_IN"


def test_wrong_plate_then_occupancy_creates_blocked_alert(
    client, reservation, admin_headers, driver_headers
):
    wrong = client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "license_plate": "N-CD5678", "confidence": 0.95},
    )
    assert wrong.status_code == 200
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "ATTENTION_REQUIRED"

    occupied = client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "occupancy": "occupied", "license_plate": "N-CD5678"},
    )
    assert occupied.status_code == 200
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "BLOCKED"
    alerts = client.get("/api/admin/alerts", headers=admin_headers).json()
    assert {alert["alert_type"] for alert in alerts} == {"WRONG_PLATE", "BLOCKED_BAY"}


def test_no_show_updates_driver_and_admin_metrics(
    client, reservation, admin_headers, driver_headers
):
    result = client.post("/api/admin/demo/no-show", headers=admin_headers, json={})
    assert result.status_code == 200
    current = client.get(
        f"/api/reservations/{reservation['id']}", headers=driver_headers
    ).json()
    assert current["status"] == "EXPIRED"
    overview = client.get("/api/admin/overview", headers=admin_headers).json()
    assert overview["no_show_count"] == 1
    analytics = client.get("/api/admin/analytics", headers=admin_headers).json()
    assert analytics["label"] == "Simulation / Estimated"
    assert analytics["no_show_rate"] == 100.0
    assert analytics["estimated_revenue_eur"] == 5.0


def test_full_charging_demo_and_admin_release(
    client, reservation, admin_headers, driver_headers
):
    client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "license_plate": "M-AB1234", "confidence": 0.99},
    )
    client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": "BAY-001", "occupancy": "occupied", "license_plate": "M-AB1234"},
    )
    started = client.post(
        f"/api/admin/demo/reservations/{reservation['id']}/charging/start",
        headers=admin_headers,
    )
    assert started.status_code == 200
    assert started.json()["reservation"]["status"] == "CHARGING"
    stopped = client.post(
        f"/api/admin/demo/reservations/{reservation['id']}/charging/stop",
        headers=admin_headers,
    )
    assert stopped.json()["reservation"]["status"] == "CHARGE_COMPLETED"
    released = client.post(
        f"/api/admin/reservations/{reservation['id']}/release", headers=admin_headers
    )
    assert released.status_code == 200
    assert released.json()["status"] == "COMPLETED"


def _check_in(client, reservation, admin_headers):
    client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": reservation["bay_id"], "license_plate": "M-AB1234", "confidence": 0.99},
    )
    client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": reservation["bay_id"], "occupancy": "occupied", "license_plate": "M-AB1234"},
    )


def test_driver_charging_session_lifecycle_cost_permissions_and_idempotency(
    client, reservation, admin_headers, driver_headers, app
):
    from datetime import datetime, timedelta, timezone

    _check_in(client, reservation, admin_headers)
    unauthenticated = client.post(
        "/api/charging/sessions", json={"reservation_id": reservation["id"]}
    )
    assert unauthenticated.status_code == 401

    started = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    )
    assert started.status_code == 201
    session = started.json()
    assert session["status"] == "ACTIVE"
    assert session["is_simulated"] is True
    assert session["pricing_model"] == "TIME_EUR_PER_HOUR"

    duplicate = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["session_id"] == session["session_id"]

    other = client.post(
        "/api/auth/register",
        json={"email": "charging-other@example.com", "password": "OtherDriver123!"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(
        f"/api/charging/sessions/{session['session_id']}", headers=other_headers
    ).status_code == 403
    assert client.post(
        f"/api/charging/sessions/{session['session_id']}/stop", headers=other_headers
    ).status_code == 403

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE charging_sessions SET started_at = ? WHERE session_id = ?",
            (one_hour_ago.isoformat(), session["session_id"]),
        )
    stopped = client.post(
        f"/api/charging/sessions/{session['session_id']}/stop", headers=driver_headers
    )
    assert stopped.status_code == 200
    completed = stopped.json()
    assert completed["status"] == "COMPLETED"
    assert 3599 <= completed["duration_seconds"] <= 3601
    assert completed["estimated_cost_eur"] == 0.6
    assert completed["ended_at"] is not None

    stopped_again = client.post(
        f"/api/charging/sessions/{session['session_id']}/stop", headers=driver_headers
    )
    assert stopped_again.status_code == 200
    assert stopped_again.json() == completed
    analytics = client.get("/api/admin/analytics", headers=admin_headers).json()
    assert analytics["completed_charging_sessions"] == 1
    assert analytics["estimated_charging_revenue_eur"] == 0.6


def test_charging_rejects_unchecked_invalid_and_second_completed_session(
    client, reservation, admin_headers, driver_headers
):
    before_check_in = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    )
    assert before_check_in.status_code == 409
    assert before_check_in.json()["error"] == "CHARGING_NOT_ALLOWED"
    missing = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": "missing"},
    )
    assert missing.status_code == 404

    _check_in(client, reservation, admin_headers)
    session = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    ).json()
    client.post(
        f"/api/charging/sessions/{session['session_id']}/stop", headers=driver_headers
    )
    restart = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    )
    assert restart.status_code == 409
    assert restart.json()["error"] == "CHARGING_ALREADY_COMPLETED"


def test_release_while_charging_completes_session_first(
    client, reservation, admin_headers, driver_headers
):
    _check_in(client, reservation, admin_headers)
    session = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    ).json()
    client.post(
        "/api/admin/demo/occupancy",
        headers=admin_headers,
        json={"bay_id": reservation["bay_id"], "occupancy": "free"},
    )
    released = client.post(
        f"/api/admin/reservations/{reservation['id']}/release", headers=admin_headers
    )
    assert released.status_code == 200
    assert released.json()["status"] == "COMPLETED"
    saved_session = client.get(
        f"/api/charging/sessions/{session['session_id']}", headers=driver_headers
    ).json()
    assert saved_session["status"] == "COMPLETED"
    assert saved_session["ended_at"] is not None


def test_charging_session_persists_across_app_restart(settings, adapter):
    app_one = create_app(settings, adapter)
    with TestClient(app_one) as first:
        driver = login(first, "driver@demo.local", "DriverTest123!")
        admin = login(first, "admin@demo.local", "AdminTest123!")
        vehicle = first.post(
            "/api/vehicles",
            headers=driver,
            json={"license_plate": "M-AB1234", "nickname": "Persistent EV"},
        ).json()
        now = datetime.now(timezone.utc)
        reservation = first.post(
            "/api/reservations",
            headers={**driver, "Idempotency-Key": "persistent-session"},
            json={
                "station_id": "GE-26",
                "bay_id": "BAY-001",
                "vehicle_id": vehicle["id"],
                "start_time": (now - timedelta(minutes=1)).isoformat(),
                "end_time": (now + timedelta(hours=1)).isoformat(),
            },
        ).json()
        _check_in(first, reservation, admin)
        session = first.post(
            "/api/charging/sessions",
            headers=driver,
            json={"reservation_id": reservation["id"]},
        ).json()

    app_two = create_app(settings, adapter)
    with TestClient(app_two) as second:
        driver = login(second, "driver@demo.local", "DriverTest123!")
        restored = second.get(
            f"/api/charging/sessions/{session['session_id']}", headers=driver
        )
        assert restored.status_code == 200
        assert restored.json()["status"] == "ACTIVE"
        reservation_view = second.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert reservation_view["charging_session"]["session_id"] == session["session_id"]
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app_backend.main import create_app
from conftest import login
