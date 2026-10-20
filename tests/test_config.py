"""Smoke test: Settings loads from .env.example."""

import pytest

from src.config import get_settings


@pytest.mark.usefixtures("isolated_env")
def test_settings_instantiate_from_env_example() -> None:
    settings = get_settings()

    assert settings.database_url.scheme == "postgresql+asyncpg"
    assert settings.bc_client_secret.get_secret_value() == "your-client-secret-value"
    assert settings.log_level == "INFO"
    assert settings.app_port == 8000
