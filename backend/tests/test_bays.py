def test_list_bays_and_read_distinct_status(client, auth_headers):
    response = client.get("/v1/bays", headers=auth_headers)
    assert response.status_code == 200
    bays = response.json()["bays"]
    assert [bay["bay_id"] for bay in bays] == ["BAY-001", "BAY-002", "BAY-003"]
    assert bays[1]["reservation_supported"] is False

    status = client.get("/v1/bays/BAY-001/status", headers=auth_headers)
    assert status.status_code == 200
    assert status.json() == {
        "bay_id": "BAY-001",
        "occupancy": "free",
        "protection": "released",
        "active_reservation_id": None,
        "last_seen_at": None,
    }


def test_unknown_bay_and_authentication_errors(client, auth_headers):
    missing = client.get("/v1/bays/DOES-NOT-EXIST/status", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["error"] == "NOT_FOUND"

    absent_token = client.get("/v1/bays")
    assert absent_token.status_code == 401
    assert absent_token.json()["error"] == "UNAUTHORIZED"
    assert absent_token.headers["www-authenticate"] == "Bearer"

    wrong_token = client.get("/v1/bays", headers={"Authorization": "Bearer wrong"})
    assert wrong_token.status_code == 401


def test_swagger_ui_is_available(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text.lower()

