from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app_backend.adapters.base import ProviderError
from app_backend.adapters.live import (
    EcuriousLiveAdapter,
    normalize_live_event,
    parse_sse_lines,
)
from app_backend.adapters.http_mock import HttpMockEcuriousAdapter
from app_backend.config import Settings
from app_backend.main import create_app
from app_backend.services.live_sse import LiveSSEListener


def run(coro):
    return asyncio.run(coro)


def live_adapter(handler, **kwargs):
    return EcuriousLiveAdapter(
        "https://hardware.test",
        "secret-test-key",
        lock_id="bay-1",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def current_lock(**overrides):
    lock = {
        "id": "bay-1",
        "name": "Bay 1 (Cam)",
        "hasCamera": True,
        "status": "raised",
        "carPresence": "vacant",
        "battery12V": 12.6,
        "battery3_7V": 3.9,
        "signalStrength4G": 28,
        "waterIngress": False,
        "activeErrors": [],
        "lastSeenAt": datetime.now(timezone.utc).isoformat(),
    }
    lock.update(overrides)
    return lock


def test_live_rest_parsing_auth_and_command_acknowledgement():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.headers["X-API-Key"] == "secret-test-key"
        path = request.url.path
        if path == "/api/v1/locks":
            return httpx.Response(200, json={"team": "Test", "locks": [current_lock()]})
        if path == "/api/v1/locks/bay-1" and request.method == "GET":
            return httpx.Response(200, json={"lock": current_lock()})
        if path.endswith("/plate/latest"):
            return httpx.Response(
                200,
                json={
                    "lockId": "bay-1",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "licensePlate": "MBD14E",
                    "plateType": "standard",
                },
            )
        if path.endswith("/whitelist"):
            assert request.read() == b'{"plates":["MAB1234"]}'
            return httpx.Response(200, json={"success": True, "whitelist": ["MAB1234"]})
        return httpx.Response(200, json={"success": True, "result": {"success": True}})

    adapter = live_adapter(handler)

    async def exercise():
        bays = await adapter.list_bays()
        status = await adapter.get_bay_status("bay-1")
        unlocked = await adapter.unlock("bay-1")
        await adapter.lock("bay-1")
        whitelist = await adapter.set_whitelist("bay-1", ["M-AB 1234"])
        latest = await adapter.get_latest_plate("bay-1")
        keep = await adapter.set_keep_state("bay-1", "normal")
        await adapter.close()
        return bays, status, unlocked, whitelist, latest, keep

    bays, status, unlocked, whitelist, latest, keep = run(exercise())
    assert bays[0]["bay_id"] == "bay-1"
    assert status["occupancy"] == "free"
    assert status["protection"] == "protected"
    assert status["hardware_online"] is True
    assert unlocked["command_accepted"] is True
    assert unlocked["physical_state_confirmed"] is False
    assert whitelist["wildcard"] is False
    assert latest["licensePlate"] == "MBD14E"
    assert keep["success"] is True
    assert len(requests) == 7


@pytest.mark.parametrize(
    ("status", "code", "public_status"),
    [
        (401, "ECURIOUS_AUTH_FAILED", 502),
        (403, "ECURIOUS_ACCESS_DENIED", 502),
        (404, "ECURIOUS_LOCK_NOT_FOUND", 404),
        (429, "ECURIOUS_RATE_LIMITED", 503),
        (500, "ECURIOUS_UNAVAILABLE", 503),
    ],
)
def test_live_rest_error_mapping(status, code, public_status):
    adapter = live_adapter(lambda _request: httpx.Response(status, json={"error": "private"}))

    async def exercise():
        try:
            await adapter.list_bays()
        finally:
            await adapter.close()

    with pytest.raises(ProviderError) as caught:
        run(exercise())
    assert caught.value.code == code
    assert caught.value.status_code == public_status


def test_live_timeout_offline_and_whitelist_capacity():
    def timeout(_request: httpx.Request):
        raise httpx.ReadTimeout("timed out")

    adapter = live_adapter(timeout)
    with pytest.raises(ProviderError, match="timed out") as caught:
        run(adapter.get_bay_status("bay-1"))
    assert caught.value.status_code == 504
    run(adapter.close())

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    offline = live_adapter(
        lambda _request: httpx.Response(200, json={"lock": current_lock(lastSeenAt=old)}),
        offline_seconds=60,
    )
    assert run(offline.get_bay_status("bay-1"))["hardware_online"] is False
    with pytest.raises(ProviderError) as capacity:
        run(offline.set_whitelist("bay-1", [f"MAB{number}" for number in range(11)]))
    assert capacity.value.code == "WHITELIST_CAPACITY_EXCEEDED"
    run(offline.close())


def test_sse_parser_deduplicatable_identity_and_bad_timestamp():
    async def lines():
        for line in [
            "event: photo_captured",
            'data: {"lockId":"bay-1","licensePlate":"M-AB1234","timestamp":"bad"}',
            "",
        ]:
            yield line

    async def collect():
        return [event async for event in parse_sse_lines(lines())]

    first = run(collect())[0]
    second = normalize_live_event(
        {"lockId": "bay-1", "licensePlate": "M-AB1234", "timestamp": "bad"},
        event_name="photo_captured",
    )
    assert first["type"] == "plate.detected"
    assert first["event_id"] == second["event_id"]
    assert first["timestamp"] is None
    assert first["timestamp_valid"] is False


@pytest.mark.parametrize(
    ("hardware_type", "expected_type", "expected_field", "expected_value"),
    [
        ("lock:photo_captured", "plate.detected", "license_plate", "京A00001"),
        ("lock:vehicle_presence", "occupancy.changed", "occupancy", "occupied"),
        ("lock:status_change", "hardware.status_changed", "hardware_status", "raised"),
        ("lock:alarm", "hardware.alarm", "error", "motor_blocked"),
    ],
)
def test_live_event_normalizer_accepts_lock_prefixed_types(
    hardware_type, expected_type, expected_field, expected_value
):
    event = normalize_live_event(
        {
            "type": hardware_type,
            "lockId": "lock-0001",
            "licensePlate": "京A00001",
            "presence": "occupied",
            "status": "raised",
            "error": "motor_blocked",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert event["type"] == expected_type
    assert event[expected_field] == expected_value
    assert event["hardware_event_type"] == hardware_type


def test_sse_reconciliation_includes_latest_photo_snapshot():
    timestamp = datetime.now(timezone.utc).isoformat()

    class SnapshotAdapter:
        async def list_locks(self):
            return [
                current_lock(
                    lastSeenAt=timestamp,
                    latestPhoto={
                        "timestamp": timestamp,
                        "licensePlate": "京A00001",
                        "plateType": "0",
                    },
                )
            ]

    class SyncRecorder:
        def __init__(self):
            self.events = []

        async def process_event(self, event):
            self.events.append(event)
            return True

    recorder = SyncRecorder()
    listener = LiveSSEListener(SnapshotAdapter(), recorder, Settings())
    assert run(listener.reconcile_state()) == 3
    assert [event["type"] for event in recorder.events] == [
        "hardware.status_changed",
        "occupancy.changed",
        "plate.detected",
    ]
    assert recorder.events[-1]["license_plate"] == "京A00001"


def test_live_latest_plate_poll_creates_stable_read_only_event():
    timestamp = datetime.now(timezone.utc).isoformat()

    class PlateAdapter:
        async def get_latest_plate(self, lock_id):
            return {
                "lockId": lock_id,
                "timestamp": timestamp,
                "licensePlate": "京A00001",
                "plateType": "0",
            }

    class SyncRecorder:
        def __init__(self):
            self.events = []

        async def process_event(self, event):
            self.events.append(event)
            return True

    recorder = SyncRecorder()
    settings = Settings(ecurious_lock_id="lock-0001", ecurious_plate_poll_seconds=5)
    listener = LiveSSEListener(PlateAdapter(), recorder, settings)
    assert run(listener.poll_latest_plate()) is True
    assert run(listener.poll_latest_plate()) is True
    assert recorder.events[0]["event_id"] == recorder.events[1]["event_id"]
    assert recorder.events[0]["type"] == "plate.detected"
    assert recorder.events[0]["license_plate"] == "京A00001"


def test_live_settings_read_key_from_environment(monkeypatch):
    monkeypatch.setenv("ECURIOUS_API_MODE", "live")
    monkeypatch.setenv("ECURIOUS_API_KEY", "from-environment")
    monkeypatch.setenv("ECURIOUS_API_BASE_URL", "https://hardware.example/")
    monkeypatch.setenv("ECURIOUS_PLATE_POLL_SECONDS", "5")
    settings = Settings.from_env()
    assert settings.ecurious_api_mode == "live"
    assert settings.ecurious_api_key == "from-environment"
    assert settings.ecurious_api_base_url == "https://hardware.example"
    assert settings.ecurious_plate_poll_seconds == 5

    monkeypatch.setenv("ECURIOUS_API_KEY", "REPLACE_WITH_YOUR_KEY")
    with pytest.raises(ValueError, match="ECURIOUS_API_KEY"):
        Settings.from_env()


def test_create_app_selects_mock_or_live_adapter(tmp_path):
    mock_app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'mock.db'}"),
        initialize_database=False,
    )
    assert isinstance(mock_app.state.adapter, HttpMockEcuriousAdapter)

    live_app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'live.db'}",
            ecurious_api_mode="live",
            ecurious_api_key="test-only",
        ),
        initialize_database=False,
    )
    assert isinstance(live_app.state.adapter, EcuriousLiveAdapter)
    run(live_app.state.adapter.close())


def test_sse_listener_reconnects_after_disconnect():
    delivered = asyncio.Event()

    class FlakyAdapter:
        calls = 0

        async def list_locks(self):
            return []

        async def stream_events(self):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError(503, "ECURIOUS_SSE_UNAVAILABLE", "disconnected")
            delivered.set()
            yield normalize_live_event(
                {
                    "type": "alarm",
                    "lockId": "bay-1",
                    "error": "test_alarm",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            await asyncio.Future()

    class SyncRecorder:
        def __init__(self):
            self.events = []

        async def process_event(self, event):
            self.events.append(event)
            return True

    adapter = FlakyAdapter()
    recorder = SyncRecorder()
    listener = LiveSSEListener(
        adapter, recorder, Settings(ecurious_sse_max_backoff_seconds=1)
    )

    async def exercise():
        task = asyncio.create_task(listener.run())
        await asyncio.wait_for(delivered.wait(), timeout=2)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(exercise())
    assert adapter.calls == 2
    assert recorder.events[0]["type"] == "hardware.alarm"
