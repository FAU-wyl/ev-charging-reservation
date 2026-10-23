from conftest import login


def test_register_login_me_logout(client):
    registered = client.post(
        "/api/auth/register",
        json={"email": "new.driver@example.com", "password": "SafePassword123!"},
    )
    assert registered.status_code == 201
    assert registered.json()["user"]["role"] == "DRIVER"
    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401

    headers = login(client, "new.driver@example.com", "SafePassword123!")
    assert client.get("/api/auth/me", headers=headers).json()["email"] == "new.driver@example.com"


def test_duplicate_registration_and_bad_credentials(client):
    payload = {"email": "same@example.com", "password": "SafePassword123!"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "EMAIL_ALREADY_REGISTERED"
    assert client.post(
        "/api/auth/login", json={**payload, "password": "WrongPassword"}
    ).status_code == 401


def test_driver_cannot_access_admin(client, driver_headers):
    response = client.get("/api/admin/overview", headers=driver_headers)
    assert response.status_code == 403


def test_vehicle_crud_and_default_management(client, driver_headers):
    first = client.post(
        "/api/vehicles",
        headers=driver_headers,
        json={"license_plate": "M-ONE1", "nickname": "First"},
    ).json()
    assert first["is_default"] is True
    second = client.post(
        "/api/vehicles",
        headers=driver_headers,
        json={"license_plate": "M-TWO2", "nickname": "Second", "is_default": True},
    ).json()
    vehicles = client.get("/api/vehicles", headers=driver_headers).json()
    assert vehicles[0]["id"] == second["id"]
    assert sum(vehicle["is_default"] for vehicle in vehicles) == 1

    updated = client.patch(
        f"/api/vehicles/{first['id']}",
        headers=driver_headers,
        json={"nickname": "Road trip EV", "is_default": True},
    )
    assert updated.status_code == 200
    assert updated.json()["nickname"] == "Road trip EV"
    assert client.delete(f"/api/vehicles/{second['id']}", headers=driver_headers).status_code == 200

