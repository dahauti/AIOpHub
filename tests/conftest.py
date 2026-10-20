"""Shared pytest fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.config import Settings, get_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip Settings keys from the OS env, point at .env.example and reset caches."""
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", ENV_EXAMPLE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def temp_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> Iterator[Path]:
    """Point DATABASE_URL at a throwaway SQLite file and reset cached engine/sessionmaker."""
    from src.database import get_engine, get_sessionmaker

    db_path = tmp_path / "nested" / "sync.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield db_path
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
