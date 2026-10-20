"""Smoke tests: Settings loads both BC profiles and Frontu config from .env.example."""

import pytest
from pydantic import ValidationError

from src.config import Settings, get_settings

SAAS_FIELDS = (
    "bc_tenant_id_saas",
    "bc_client_id_saas",
    "bc_client_secret_saas",
    "bc_environment_saas",
    "bc_company_id_saas",
)
ONPREM_FIELDS = (
    "bc_tenant_id_onprem",
    "bc_client_id_onprem",
    "bc_client_secret_onprem",
    "bc_odata_base_url_onprem",
    "bc_company_id_onprem",
)
FRONTU_FIELDS = ("frontu_api_key", "frontu_subdomain", "internal_company_client_id")


@pytest.mark.usefixtures("isolated_env")
def test_settings_instantiate_from_env_example() -> None:
    settings = get_settings()

    assert settings.database_url == "sqlite+aiosqlite:///./data/sync.db"
    assert settings.frontu_api_key.get_secret_value() == "your-frontu-api-token"
    assert settings.log_level == "INFO"
    assert settings.app_port == 8000


@pytest.mark.usefixtures("isolated_env")
def test_saas_profile_loads() -> None:
    settings = get_settings()

    assert settings.bc_tenant_id_saas == "your-tenant-id-guid"
    assert settings.bc_client_id_saas == "your-app-registration-client-id"
    assert settings.bc_client_secret_saas.get_secret_value() == "your-client-secret-value"
    assert settings.bc_environment_saas == "Production"
    assert settings.bc_company_id_saas == "your-company-guid-or-name"


@pytest.mark.usefixtures("isolated_env")
def test_onprem_profile_loads() -> None:
    settings = get_settings()

    assert settings.bc_tenant_id_onprem == "your-tenant-id-guid"
    assert settings.bc_client_id_onprem == "your-client-id-guid"
    assert settings.bc_client_secret_onprem.get_secret_value() == "your-client-secret-value"
    assert settings.bc_scope_onprem == "https://api.businesscentral.dynamics.com/.default"
    assert settings.bc_odata_base_url_onprem.host == "bc.company.local"
    assert settings.bc_odata_base_url_onprem.port == 7048
    assert settings.bc_odata_base_url_onprem.path == "/BC/ODataV4"
    assert settings.bc_company_id_onprem == "your-company-name"


@pytest.mark.usefixtures("isolated_env")
def test_frontu_settings_load() -> None:
    settings = get_settings()

    assert settings.frontu_subdomain == "your-subdomain"
    assert settings.internal_company_client_id == "your-internal-company-client-id"


@pytest.mark.usefixtures("isolated_env")
def test_secrets_are_masked() -> None:
    settings = get_settings()

    for secret in (settings.bc_client_secret_saas, settings.bc_client_secret_onprem):
        assert str(secret) == "**********"


@pytest.mark.parametrize("field_name", SAAS_FIELDS + ONPREM_FIELDS + FRONTU_FIELDS)
def test_profile_credentials_are_required(field_name: str) -> None:
    assert Settings.model_fields[field_name].is_required()


@pytest.mark.parametrize("subdomain", ["acme.evil.com", "acme/x", ""])
def test_frontu_subdomain_rejects_non_label_values(
    subdomain: str, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("FRONTU_SUBDOMAIN", subdomain)

    with pytest.raises(ValidationError):
        get_settings()


@pytest.mark.usefixtures("isolated_env")
def test_runtime_defaults_select_onprem_and_five_minute_interval() -> None:
    settings = get_settings()

    assert settings.bc_active_profile == "onprem"
    assert settings.sync_interval_seconds == 300


@pytest.mark.parametrize(
    ("env_key", "value"),
    [("BC_ACTIVE_PROFILE", "cloud"), ("SYNC_INTERVAL_SECONDS", "0")],
)
def test_runtime_settings_reject_invalid_values(
    env_key: str, value: str, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv(env_key, value)

    with pytest.raises(ValidationError):
        get_settings()


def test_safety_flag_code_defaults_are_fail_safe() -> None:
    assert Settings.model_fields["sync_enabled"].default is False
    assert Settings.model_fields["sync_dry_run"].default is True


@pytest.mark.usefixtures("isolated_env")
def test_env_example_ships_with_daemon_disabled_and_dry_run_on() -> None:
    settings = get_settings()

    assert settings.sync_enabled is False
    assert settings.sync_dry_run is True


@pytest.mark.parametrize(("enabled", "dry_run"), [("true", "false"), ("false", "true")])
def test_safety_flags_parse_from_env(
    enabled: str, dry_run: str, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("SYNC_ENABLED", enabled)
    monkeypatch.setenv("SYNC_DRY_RUN", dry_run)

    settings = get_settings()

    assert settings.sync_enabled is (enabled == "true")
    assert settings.sync_dry_run is (dry_run == "true")


def test_database_url_defaults_to_embedded_sqlite() -> None:
    assert Settings.model_fields["database_url"].default == "sqlite+aiosqlite:///./data/sync.db"


@pytest.mark.parametrize(
    "url",
    ["postgresql+asyncpg://user:pw@localhost:5432/aiophub", "sqlite:///./data/sync.db", "nonsense"],
)
def test_database_url_requires_aiosqlite_driver(
    url: str, monkeypatch: pytest.MonkeyPatch, isolated_env: None
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        get_settings()
