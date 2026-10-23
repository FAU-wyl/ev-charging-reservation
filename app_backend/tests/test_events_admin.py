def test_correct_plate_checks_in_and_event_is_deduplicated(
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

