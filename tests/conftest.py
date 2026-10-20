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
