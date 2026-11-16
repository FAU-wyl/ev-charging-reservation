from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app_backend.adapters.base import EcuriousAdapter, ProviderError
from app_backend.config import Settings
from app_backend.database import Database
from app_backend.main import create_app
from conftest import login


class FakeLiveAdapter(EcuriousAdapter):
    mode = "live"

    def __init__(self) -> None:
        self.whitelists: list[list[str]] = []
        self.whitelist_attempts = 0
        self.unlocks = 0
        self.online = True
        self.occupancy = "free"
        self.protection = "protected"
        self.fail_whitelist = False
        self.whitelist_error: ProviderError | None = None
        self.whitelist_success = True
        self.whitelist_delay = 0.0
        self.latest_plate: dict[str, Any] = {}
        self.latest_plate_calls = 0
        self.locks = 0

    async def list_bays(self):
        return [
            {
                "bay_id": "bay-1",
                "reservation_supported": True,
                "hardware_online": self.online,
                "has_camera": True,
                "charger_reference": None,
            }
        ]

    async def get_bay_status(self, bay_id):
        return {
            "bay_id": bay_id,
            "occupancy": self.occupancy,
            "protection": self.protection,
            "active_reservation_id": None,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "hardware_online": self.online,
            "barrier_status": "raised" if self.protection == "protected" else "lowered",
        }

    async def set_whitelist(self, lock_id, plates):
        self.whitelist_attempts += 1
        if self.whitelist_delay:
            await asyncio.sleep(self.whitelist_delay)
        if self.whitelist_error:
            raise self.whitelist_error
        if self.fail_whitelist:
            raise ProviderError(503, "ECURIOUS_UNAVAILABLE", "Hardware unavailable")
        self.whitelists.append(list(plates))
        return {"success": self.whitelist_success, "whitelist": plates}

    async def unlock(self, lock_id, *, cancel_auto_lift=False):
        self.unlocks += 1
        return {"success": True, "command_accepted": True, "physical_state_confirmed": False}

    async def lock(self, lock_id, *, cancel_auto_lift=False):
        self.locks += 1
        return {"success": True, "command_accepted": True, "physical_state_confirmed": False}

    async def set_keep_state(self, lock_id, state):
        return {"success": True, "state": state}

    async def get_latest_plate(self, lock_id):
        self.latest_plate_calls += 1
        return {"lockId": lock_id, **self.latest_plate}

    async def create_reservation(self, **kwargs):
        raise AssertionError("Live mode must keep reservations in the app database")

    async def get_reservation(self, reservation_id):
        raise AssertionError("Live mode must not query a hardware reservation")

    async def release_reservation(self, reservation_id, reason):
        raise AssertionError("Live mode must not release a hardware reservation")

    async def poll_events(self, after):
        return {"events": [], "next_cursor": after}


def make_live_client(tmp_path: Path):
    adapter = FakeLiveAdapter()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'live.db'}",
        ecurious_api_mode="live",
        ecurious_api_key="test-only",
        ecurious_lock_id="bay-1",
        event_poll_enabled=False,
        ecurious_sse_enabled=False,
        station_fixture_path=str(Path(__file__).resolve().parents[2] / "goingelectric_response.json"),
        seed_demo_users=True,
        demo_driver_password="DriverTest123!",
        demo_admin_password="AdminTest123!",
    )
    app = create_app(settings, adapter)
    return app, adapter


def create_current_reservation(client, headers, plate="M-AB1234"):
    vehicle = client.post(
        "/api/vehicles",
        headers=headers,
        json={"license_plate": plate, "nickname": "Live EV", "is_default": True},
    ).json()
    now = datetime.now(timezone.utc)
    response = client.post(
        "/api/reservations",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "station_id": "GE-26",
            "bay_id": "bay-1",
            "vehicle_id": vehicle["id"],
            "start_time": (now - timedelta(minutes=1)).isoformat(),
            "end_time": (now + timedelta(hours=1)).isoformat(),
        },
    )
    return response


def test_live_reservation_whitelist_manual_unlock_and_cancel(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        created = create_current_reservation(client, driver)
        assert created.status_code == 201, created.text
        reservation = created.json()
        assert reservation["hardware_sync_status"] == "SYNCED"
        assert adapter.whitelists[-1] == ["MAB1234"]

        command = client.post("/api/bays/bay-1/unlock", headers=driver)
        assert command.status_code == 200
        assert command.json()["command_status"] == "ACCEPTED"
        assert command.json()["physical_state"] == "pending"
        assert adapter.protection == "protected"

        outsider = client.post(
            "/api/auth/register",
            json={"email": "outsider@example.com", "password": "Outsider123!"},
        ).json()
        denied = client.post(
            "/api/bays/bay-1/unlock",
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert denied.status_code == 403

        cancelled = client.post(
            f"/api/reservations/{reservation['id']}/cancel", headers=driver
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
        assert adapter.whitelists[-1] == []

        with app.state.database.connect() as connection:
            audit = connection.execute(
                "SELECT * FROM hardware_commands WHERE command = 'UNLOCK'"
            ).fetchone()
        assert audit["requested_by_user_id"]
        assert audit["result_status"] == "ACCEPTED"


def test_driver_confirms_departure_after_stop_and_lock_rises_once(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        plate = {
            "event_id": "DEPARTURE-CHECK-IN",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "MAB1234",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.run(app.state.event_sync.process_event(plate))
        session = client.post(
            "/api/charging/sessions",
            headers=driver,
            json={"reservation_id": reservation["id"]},
        ).json()
        stopped = client.post(
            f"/api/charging/sessions/{session['session_id']}/stop", headers=driver
        )
        assert stopped.status_code == 200
        assert client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()["status"] == "CHARGE_COMPLETED"

        departed = client.post(
            f"/api/reservations/{reservation['id']}/depart", headers=driver
        )
        assert departed.status_code == 200, departed.text
        body = departed.json()
        assert body["reservation"]["status"] == "COMPLETED"
        assert body["hardware_command"]["command_status"] == "ACCEPTED"
        assert body["hardware_command"]["physical_state"] == "pending"
        assert adapter.whitelists[-1] == []
        assert adapter.locks == 1

        duplicate = client.post(
            f"/api/reservations/{reservation['id']}/depart", headers=driver
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["hardware_command"]["command_id"] == body["hardware_command"]["command_id"]
        assert adapter.locks == 1
        with app.state.database.connect() as connection:
            audit = connection.execute(
                "SELECT * FROM hardware_commands WHERE command = 'LOCK' AND reservation_id = ?",
                (reservation["id"],),
            ).fetchall()
        assert len(audit) == 1
        assert audit[0]["requested_by_user_id"]


def test_driver_departure_rejects_occupied_bay_and_active_charging(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        plate = {
            "event_id": "DEPARTURE-SAFETY-CHECK-IN",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "MAB1234",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.run(app.state.event_sync.process_event(plate))
        session = client.post(
            "/api/charging/sessions",
            headers=driver,
            json={"reservation_id": reservation["id"]},
        ).json()

        active = client.post(
            f"/api/reservations/{reservation['id']}/depart", headers=driver
        )
        assert active.status_code == 409
        assert active.json()["error"] == "DEPARTURE_REQUIRES_STOP_CHARGING"
        assert adapter.locks == 0

        client.post(
            f"/api/charging/sessions/{session['session_id']}/stop", headers=driver
        )
        outsider = client.post(
            "/api/auth/register",
            json={"email": "departure-outsider@example.com", "password": "Other123!"},
        ).json()
        denied = client.post(
            f"/api/reservations/{reservation['id']}/depart",
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert denied.status_code == 403
        assert adapter.locks == 0

        adapter.occupancy = "occupied"
        occupied = client.post(
            f"/api/reservations/{reservation['id']}/depart", headers=driver
        )
        assert occupied.status_code == 409
        assert occupied.json()["error"] == "RELEASE_NOT_SAFE"
        assert adapter.locks == 0
        assert client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()["status"] == "CHARGE_COMPLETED"


def test_live_whitelist_failure_is_visible_and_never_faked(tmp_path):
    app, adapter = make_live_client(tmp_path)
    adapter.fail_whitelist = True
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        response = create_current_reservation(client, driver)
        assert response.status_code == 503
        assert response.json()["error"] == "ECURIOUS_UNAVAILABLE"
        saved = client.get("/api/reservations/my", headers=driver).json()
        assert saved[0]["status"] == "ATTENTION_REQUIRED"
        assert saved[0]["provider_confirmed"] is False
        assert saved[0]["hardware_sync_status"] == "FAILED"
        attempts = adapter.whitelist_attempts
        asyncio.run(app.state.hardware.reconcile_due())
        assert adapter.whitelist_attempts == attempts
        adapter.fail_whitelist = False
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation_id = saved[0]["id"]
        retried = client.post(
            f"/api/admin/reservations/{reservation_id}/retry-hardware-authorization",
            headers=admin,
        )
        assert retried.status_code == 200
        assert retried.json()["status"] == "RESERVED"
        assert retried.json()["hardware_sync_status"] == "SYNCED"
        recovered = client.get("/api/reservations/my", headers=driver).json()[0]
        assert recovered["status"] == "RESERVED"
        assert recovered["hardware_sync_status"] == "SYNCED"

        repeated = client.post(
            f"/api/admin/reservations/{reservation_id}/retry-hardware-authorization",
            headers=admin,
        )
        assert repeated.status_code == 409
        assert repeated.json()["error"] == "HARDWARE_RETRY_NOT_REQUIRED"


def test_live_plate_immediately_checks_in_and_deduplicates(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        now = datetime.now(timezone.utc).isoformat()
        plate_event = {
            "event_id": "LIVE-PLATE-1",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "MAB1234",
            "timestamp": now,
        }
        assert asyncio.run(app.state.event_sync.process_event(plate_event)) is True
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "CHECKED_IN"
        assert asyncio.run(app.state.event_sync.process_event(plate_event)) is False

        occupied = {
            "event_id": "LIVE-PRESENCE-1",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "occupied",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        assert asyncio.run(app.state.event_sync.process_event(occupied)) is True
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "CHECKED_IN"


def test_empty_sse_plate_uses_latest_plate_without_false_alert(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        timestamp = datetime.now(timezone.utc).isoformat()
        adapter.latest_plate = {
            "timestamp": timestamp,
            "licensePlate": "M-AB1234",
            "plateType": "standard",
        }
        empty_sse = {
            "event_id": "EMPTY-SSE-PLATE",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "",
            "timestamp": timestamp,
        }

        assert asyncio.run(app.state.event_sync.process_event(empty_sse)) is True
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "CHECKED_IN"
        assert adapter.latest_plate_calls == 1
        with app.state.database.connect() as connection:
            event = connection.execute(
                "SELECT masked_plate, processing_result FROM processed_events WHERE external_event_id = ?",
                (empty_sse["event_id"],),
            ).fetchone()
            alerts = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE reservation_id = ?",
                (reservation["id"],),
            ).fetchone()[0]
        assert event["masked_plate"] == "M-***34"
        assert event["processing_result"] == "CHECK_IN_SUCCESSFUL"
        assert alerts == 0


def test_empty_sse_plate_stays_reserved_when_latest_lookup_has_no_plate(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        timestamp = datetime.now(timezone.utc).isoformat()
        adapter.latest_plate = {"timestamp": timestamp, "licensePlate": ""}
        empty_sse = {
            "event_id": "EMPTY-SSE-NO-LATEST",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "",
            "timestamp": timestamp,
        }

        assert asyncio.run(app.state.event_sync.process_event(empty_sse)) is True
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "RESERVED"
        with app.state.database.connect() as connection:
            result = connection.execute(
                "SELECT processing_result FROM processed_events WHERE external_event_id = ?",
                (empty_sse["event_id"],),
            ).fetchone()["processing_result"]
            alerts = connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert result == "EMPTY_PLATE_IGNORED"
        assert alerts == 0


def test_live_latest_photo_replay_recovers_check_in_after_restart(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        timestamp = datetime.now(timezone.utc).isoformat()
        assert app.state.event_sync._upsert_hardware_state(
            "bay-1",
            latest_plate="MAB1234",
            latest_plate_at=timestamp,
            observed_at=timestamp,
        ) is True

        replay = {
            "event_id": "RECONCILE-AFTER-RESTART",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "MAB1234",
            "timestamp": timestamp,
        }
        assert asyncio.run(app.state.event_sync.process_event(replay)) is True
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "CHECKED_IN"
        with app.state.database.connect() as connection:
            result = connection.execute(
                "SELECT processing_result FROM processed_events WHERE external_event_id = ?",
                (replay["event_id"],),
            ).fetchone()["processing_result"]
        assert result == "CHECK_IN_SUCCESSFUL"


def test_manual_unlock_reports_offline_and_admin_lock_refuses_occupied(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        admin = login(client, "admin@demo.local", "AdminTest123!")
        adapter.online = False
        offline = client.post("/api/bays/bay-1/unlock", headers=admin)
        assert offline.status_code == 503
        assert offline.json()["error"] == "ECURIOUS_DEVICE_OFFLINE"

        adapter.online = True
        adapter.occupancy = "occupied"
        unsafe = client.post("/api/bays/bay-1/lock", headers=admin)
        assert unsafe.status_code == 409
        assert unsafe.json()["error"] == "LOCK_OCCUPIED"


def test_live_no_show_expires_locally_and_removes_whitelist(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET start_time = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
                    reservation["id"],
                ),
            )
        synced = client.post("/api/admin/sync", headers=admin)
        assert synced.status_code == 200
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "EXPIRED"
        assert current["hardware_sync_status"] == "REMOVED"
        assert adapter.whitelists[-1] == []


def test_live_no_show_uses_start_plus_grace_boundary_and_is_idempotent(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        start = datetime.now(timezone.utc)
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET start_time = ?, end_time = ? WHERE id = ?",
                (
                    start.isoformat(),
                    (start + timedelta(hours=1)).isoformat(),
                    reservation["id"],
                ),
            )
        before, reviews = asyncio.run(
            app.state.hardware._expire_due_safely(
                at=start + timedelta(minutes=15) - timedelta(microseconds=1)
            )
        )
        assert (before, reviews) == (0, 0)
        exact, reviews = asyncio.run(
            app.state.hardware._expire_due_safely(at=start + timedelta(minutes=15))
        )
        assert (exact, reviews) == (1, 0)
        repeated, reviews = asyncio.run(
            app.state.hardware._expire_due_safely(at=start + timedelta(hours=1))
        )
        assert (repeated, reviews) == (0, 0)
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "EXPIRED"
        assert current["end_time"] > (start + timedelta(minutes=15)).isoformat()
        assert adapter.occupancy == "free"


def test_live_no_show_timezone_and_checked_in_cancelled_exclusions(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        # 14:00+02:00 is 12:00Z; the exact no-show deadline is 12:15Z.
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET start_time = ?, status = 'RESERVED' WHERE id = ?",
                ("2026-10-24T14:00:00+02:00", reservation["id"]),
            )
        expired, _ = asyncio.run(
            app.state.hardware._expire_due_safely(
                at=datetime(2026, 10, 24, 12, 15, tzinfo=timezone.utc)
            )
        )
        assert expired == 1
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET status = 'CHECKED_IN' WHERE id = ?",
                (reservation["id"],),
            )
        expired, _ = asyncio.run(
            app.state.hardware._expire_due_safely(
                at=datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc)
            )
        )
        assert expired == 0
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET status = 'CANCELLED' WHERE id = ?",
                (reservation["id"],),
            )
        expired, _ = asyncio.run(
            app.state.hardware._expire_due_safely(
                at=datetime(2026, 10, 24, 13, 0, tzinfo=timezone.utc)
            )
        )
        assert expired == 0


@pytest.mark.parametrize(("online", "occupancy"), [(False, "free"), (True, "unknown"), (True, "occupied")])
def test_live_no_show_unknown_or_occupied_state_requires_review(
    tmp_path, online, occupancy
):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET start_time = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
                    reservation["id"],
                ),
            )
        adapter.online = online
        adapter.occupancy = occupancy
        expired, reviews = asyncio.run(app.state.hardware._expire_due_safely())
        assert (expired, reviews) == (0, 1)
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "ATTENTION_REQUIRED"
        assert current["hardware_sync_status"] == "REVIEW_REQUIRED"


def test_live_no_show_whitelist_failure_keeps_expired_pending_recovery(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE app_reservations SET start_time = ? WHERE id = ?",
                (
                    (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
                    reservation["id"],
                ),
            )
        adapter.fail_whitelist = True
        result = asyncio.run(app.state.hardware.reconcile_due(force=True))
        assert result["expired"] == 1
        assert result["failed"] == 1
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "EXPIRED"
        assert current["hardware_sync_status"] == "FAILED"


def test_manual_unlock_supports_check_in_when_lpr_fails(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        unlocked = client.post("/api/bays/bay-1/unlock", headers=driver)
        assert unlocked.status_code == 200
        occupancy = {
            "event_id": "LIVE-MANUAL-PRESENCE-1",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "occupied",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        asyncio.run(app.state.event_sync.process_event(occupancy))
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "CHECKED_IN"
        events = client.get("/api/admin/events", headers=admin).json()
        assert events[0]["processing_result"] == "MANUAL_UNLOCK_CHECK_IN"


def test_sse_state_watermarks_reject_reverse_equal_and_invalid_events(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        base = datetime.now(timezone.utc)
        newer = {
            "event_id": "PRESENCE-NEW",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "occupied",
            "timestamp": (base + timedelta(seconds=10)).isoformat(),
        }
        older = {
            "event_id": "PRESENCE-OLD",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "free",
            "timestamp": base.isoformat(),
        }
        equal = {**older, "event_id": "PRESENCE-EQUAL", "timestamp": newer["timestamp"]}
        invalid = {**older, "event_id": "PRESENCE-BAD", "timestamp": "not-a-time"}
        missing = {key: value for key, value in older.items() if key != "timestamp"}
        missing["event_id"] = "PRESENCE-MISSING"
        assert asyncio.run(app.state.event_sync.process_event(newer)) is True
        assert asyncio.run(app.state.event_sync.process_event(older)) is True
        assert asyncio.run(app.state.event_sync.process_event(equal)) is True
        assert asyncio.run(app.state.event_sync.process_event(invalid)) is True
        assert asyncio.run(app.state.event_sync.process_event(missing)) is True
        with app.state.database.connect() as connection:
            state = connection.execute(
                "SELECT * FROM hardware_locks WHERE lock_id = 'bay-1'"
            ).fetchone()
            results = {
                row["external_event_id"]: row["processing_result"]
                for row in connection.execute(
                    "SELECT external_event_id, processing_result FROM processed_events"
                ).fetchall()
            }
        assert state["car_presence"] == "occupied"
        assert results["PRESENCE-OLD"] == "STALE_OCCUPANCY_EVENT_IGNORED"
        assert results["PRESENCE-EQUAL"] == "STALE_OCCUPANCY_EVENT_IGNORED"
        assert results["PRESENCE-BAD"] == "INVALID_TIMESTAMP_IGNORED"
        assert results["PRESENCE-MISSING"] == "INVALID_TIMESTAMP_IGNORED"


def test_sse_event_types_have_independent_per_lock_watermarks(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app):
        base = datetime.now(timezone.utc)
        events = [
            {
                "event_id": "STATUS-NEW",
                "type": "hardware.status_changed",
                "bay_id": "bay-1",
                "hardware_status": "raised",
                "timestamp": (base + timedelta(seconds=5)).isoformat(),
            },
            {
                "event_id": "STATUS-OLD",
                "type": "hardware.status_changed",
                "bay_id": "bay-1",
                "hardware_status": "lowered",
                "timestamp": base.isoformat(),
            },
            {
                "event_id": "PLATE-NEW",
                "type": "plate.detected",
                "bay_id": "bay-1",
                "license_plate": "M-NEW1",
                "timestamp": (base + timedelta(seconds=6)).isoformat(),
            },
            {
                "event_id": "PLATE-OLD",
                "type": "plate.detected",
                "bay_id": "bay-1",
                "license_plate": "M-OLD1",
                "timestamp": (base + timedelta(seconds=1)).isoformat(),
            },
            {
                "event_id": "OTHER-LOCK",
                "type": "occupancy.changed",
                "bay_id": "lock-0002",
                "occupancy": "free",
                "timestamp": base.isoformat(),
            },
            {
                "event_id": "ALARM-NEW",
                "type": "hardware.alarm",
                "bay_id": "bay-1",
                "error": "water",
                "timestamp": (base + timedelta(seconds=7)).isoformat(),
            },
            {
                "event_id": "ALARM-OLD",
                "type": "hardware.alarm",
                "bay_id": "bay-1",
                "error": "old-water",
                "timestamp": (base + timedelta(seconds=2)).isoformat(),
            },
        ]
        for event in events:
            assert asyncio.run(app.state.event_sync.process_event(event)) is True
        with app.state.database.connect() as connection:
            first = connection.execute(
                "SELECT * FROM hardware_locks WHERE lock_id = 'bay-1'"
            ).fetchone()
            second = connection.execute(
                "SELECT * FROM hardware_locks WHERE lock_id = 'lock-0002'"
            ).fetchone()
            alarms = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE alert_type = 'HARDWARE_ALARM'"
            ).fetchone()[0]
        assert first["barrier_status"] == "raised"
        assert first["latest_plate"] == "M-NEW1"
        assert second["car_presence"] == "free"
        assert alarms == 1


def test_sse_duplicate_claim_is_atomic_across_workers(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app):
        event = {
            "event_id": "CONCURRENT-DUPLICATE",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "free",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        async def process_twice():
            return await asyncio.gather(
                app.state.event_sync.process_event(event),
                app.state.event_sync.process_event(event),
            )

        assert sorted(asyncio.run(process_twice())) == [False, True]
        with app.state.database.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM processed_events WHERE external_event_id = ?",
                (event["event_id"],),
            ).fetchone()[0] == 1


def test_live_occupancy_before_plate_uses_same_combined_check_in_rule(tmp_path):
    app, _adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        base = datetime.now(timezone.utc)
        occupied = {
            "event_id": "ORDER-OCCUPIED",
            "type": "occupancy.changed",
            "bay_id": "bay-1",
            "occupancy": "occupied",
            "timestamp": base.isoformat(),
        }
        plate = {
            "event_id": "ORDER-PLATE",
            "type": "plate.detected",
            "bay_id": "bay-1",
            "license_plate": "MAB1234",
            "timestamp": (base + timedelta(seconds=1)).isoformat(),
        }
        asyncio.run(app.state.event_sync.process_event(occupied))
        assert client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()["status"] == "BLOCKED"
        asyncio.run(app.state.event_sync.process_event(plate))
        assert client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()["status"] == "CHECKED_IN"


def test_existing_reservation_table_receives_additive_hardware_columns(tmp_path):
    database = Database(Settings(database_url=f"sqlite:///{tmp_path / 'migration.db'}"))
    with database.connect() as connection:
        connection.execute("CREATE TABLE app_reservations (id TEXT PRIMARY KEY)")
        database._migrate(connection)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(app_reservations)").fetchall()
        }
    assert {
        "hardware_sync_status",
        "hardware_sync_error",
        "hardware_sync_at",
        "release_operation_id",
        "release_requested_at",
    }.issubset(columns)


def test_live_release_success_duplicate_and_operation_log(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        first = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert first.status_code == 200
        assert first.json()["status"] == "COMPLETED"
        attempts = adapter.whitelist_attempts
        duplicate = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "COMPLETED"
        assert adapter.whitelist_attempts == attempts
        with app.state.database.connect() as connection:
            operations = connection.execute(
                "SELECT status FROM release_operations WHERE reservation_id = ?",
                (reservation["id"],),
            ).fetchall()
        assert [row["status"] for row in operations] == ["SUCCEEDED"]


@pytest.mark.parametrize(
    ("error", "success", "expected_status", "expected_sync"),
    [
        (
            ProviderError(503, "ECURIOUS_UNAVAILABLE", "unavailable"),
            True,
            "RELEASE_FAILED",
            "FAILED",
        ),
        (
            ProviderError(504, "ECURIOUS_TIMEOUT", "timeout"),
            True,
            "RELEASE_UNKNOWN",
            "UNKNOWN",
        ),
        (None, False, "RELEASE_UNKNOWN", "UNKNOWN"),
    ],
)
def test_live_release_failure_timeout_and_unknown_result_are_recoverable(
    tmp_path, error, success, expected_status, expected_sync
):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        adapter.whitelist_error = error
        adapter.whitelist_success = success
        failed = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert failed.status_code in {502, 503, 504}
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == expected_status
        assert current["hardware_sync_status"] == expected_sync
        assert current["status"] != "COMPLETED"

        adapter.whitelist_error = None
        adapter.whitelist_success = True
        recovered = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "COMPLETED"


def test_live_release_refuses_occupied_bay_without_whitelist_write(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        adapter.occupancy = "occupied"
        attempts = adapter.whitelist_attempts
        response = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert response.status_code == 409
        assert response.json()["error"] == "RELEASE_NOT_SAFE"
        assert adapter.whitelist_attempts == attempts
        assert client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()["status"] == "RESERVED"


def test_live_concurrent_release_sends_one_revocation(tmp_path):
    from app_backend.services.reservations import ReservationService

    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        reservation = create_current_reservation(client, driver).json()
        adapter.whitelist_delay = 0.05
        before = adapter.whitelist_attempts
        service = ReservationService(app.state.database, adapter, app.state.hardware)

        async def release_twice():
            return await asyncio.gather(
                service.admin_release(reservation["id"]),
                service.admin_release(reservation["id"]),
            )

        results = asyncio.run(release_twice())
        assert adapter.whitelist_attempts == before + 1
        assert {result["status"] for result in results} <= {
            "RELEASE_PENDING",
            "COMPLETED",
        }
        assert service.get(reservation["id"], "", "ADMIN")["status"] == "COMPLETED"


def test_live_release_preserves_other_current_authorizations(tmp_path):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        first = create_current_reservation(client, driver).json()
        second_vehicle = client.post(
            "/api/vehicles",
            headers=driver,
            json={"license_plate": "N-CD5678", "nickname": "Second EV"},
        ).json()
        now = datetime.now(timezone.utc)
        with app.state.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO app_reservations(
                       id, ecurious_reservation_id, user_id, vehicle_id, station_id, bay_id,
                       status, ecurious_status, start_time, end_time, created_at, updated_at,
                       provider_confirmed, hardware_sync_status, client_request_key
                   ) SELECT 'APP-RES-SECOND', 'LIVE-SECOND', user_id, ?, station_id, bay_id,
                       'RESERVED', 'reserved', ?, ?, ?, ?, 1, 'SYNCED', 'second-current'
                   FROM app_reservations WHERE id = ?""",
                (
                    second_vehicle["id"],
                    (now + timedelta(minutes=5)).isoformat(),
                    (now + timedelta(hours=1)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    first["id"],
                ),
            )
        released = client.post(
            f"/api/admin/reservations/{first['id']}/release", headers=admin
        )
        assert released.status_code == 200
        assert adapter.whitelists[-1] == ["NCD5678"]


def test_live_release_database_finalization_failure_stays_recoverable(tmp_path, monkeypatch):
    app, adapter = make_live_client(tmp_path)
    with TestClient(app) as client:
        driver = login(client, "driver@demo.local", "DriverTest123!")
        admin = login(client, "admin@demo.local", "AdminTest123!")
        reservation = create_current_reservation(client, driver).json()
        original = app.state.hardware.finalize_safe_releases

        def fail_commit(_lock_id, _physical):
            raise sqlite3.OperationalError("simulated final commit failure")

        monkeypatch.setattr(app.state.hardware, "finalize_safe_releases", fail_commit)
        with pytest.raises(sqlite3.OperationalError):
            client.post(
                f"/api/admin/reservations/{reservation['id']}/release", headers=admin
            )
        current = client.get(
            f"/api/reservations/{reservation['id']}", headers=driver
        ).json()
        assert current["status"] == "RELEASE_PENDING"
        assert current["hardware_sync_status"] == "REMOVED"

        monkeypatch.setattr(app.state.hardware, "finalize_safe_releases", original)
        recovered = client.post(
            f"/api/admin/reservations/{reservation['id']}/release", headers=admin
        )
        assert recovered.json()["status"] == "COMPLETED"
