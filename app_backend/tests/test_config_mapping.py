from pathlib import Path

from app_backend.config import Settings
from app_backend.database import Database


FIXTURE = str(Path(__file__).resolve().parents[2] / "goingelectric_response.json")


def test_live_default_uses_assigned_lock_id(monkeypatch):
    monkeypatch.setenv("ECURIOUS_API_MODE", "live")
    monkeypatch.setenv("ECURIOUS_API_KEY", "test-only-key")
    monkeypatch.delenv("ECURIOUS_LOCK_ID", raising=False)
    assert Settings.from_env().ecurious_lock_id == "lock-0001"


def test_live_mapping_has_one_physical_bay_and_mock_ids_stay_mock(tmp_path):
    live = Database(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'live-map.db'}",
            ecurious_api_mode="live",
            ecurious_api_key="test-only",
            ecurious_lock_id="lock-0001",
            station_fixture_path=FIXTURE,
        )
    )
    live.initialize()
    with live.connect() as connection:
        live_ids = [
            row["bay_id"]
            for row in connection.execute(
                "SELECT bay_id FROM bay_mappings ORDER BY bay_id"
            ).fetchall()
        ]
    assert live_ids == ["lock-0001"]

    mock = Database(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'mock-map.db'}",
            ecurious_api_mode="mock",
            station_fixture_path=FIXTURE,
        )
    )
    mock.initialize()
    with mock.connect() as connection:
        mock_ids = {
            row["bay_id"]
            for row in connection.execute("SELECT bay_id FROM bay_mappings").fetchall()
        }
    assert mock_ids == {"BAY-001", "BAY-002", "BAY-003"}
