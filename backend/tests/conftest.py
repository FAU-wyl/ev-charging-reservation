from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


TEST_TOKEN = "test-only-token"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_mock.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    return Settings(
        mock_mode=True,
        token=TEST_TOKEN,
        database_url=f"sqlite:///{db_path}",
        no_show_grace_seconds=60,
        early_arrival_seconds=900,
        min_plate_confidence=0.7,
        event_page_size=100,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def reservation_payload() -> Callable[..., dict[str, str]]:
    def factory(
        *,
        bay_id: str = "BAY-001",
        plate: str = "M-AB1234",
        start_delta: timedelta = timedelta(minutes=-1),
        end_delta: timedelta = timedelta(hours=1),
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        return {
            "bay_id": bay_id,
            "license_plate": plate,
            "start_time": (now + start_delta).isoformat(),
            "end_time": (now + end_delta).isoformat(),
        }

    return factory

