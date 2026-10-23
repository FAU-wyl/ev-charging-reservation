from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    mock_mode: bool = True
    token: str = "local-dev-token"
    database_url: str = "sqlite:///./ecurious_mock.db"
    no_show_grace_seconds: int = 900
    early_arrival_seconds: int = 900
    min_plate_confidence: float = 0.7
    event_page_size: int = 100
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mock_mode=_as_bool(os.getenv("ECURIOUS_MOCK_MODE", "true")),
            token=os.getenv("ECURIOUS_MOCK_TOKEN", "local-dev-token"),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./ecurious_mock.db"),
            no_show_grace_seconds=int(os.getenv("NO_SHOW_GRACE_SECONDS", "900")),
            early_arrival_seconds=int(os.getenv("EARLY_ARRIVAL_SECONDS", "900")),
            min_plate_confidence=float(os.getenv("MIN_PLATE_CONFIDENCE", "0.7")),
            event_page_size=int(os.getenv("EVENT_PAGE_SIZE", "100")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def database_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported")
        raw_path = self.database_url[len(prefix) :]
        if raw_path == ":memory:":
            return raw_path
        return str(Path(raw_path).expanduser())
