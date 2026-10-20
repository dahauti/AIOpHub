"""Smoke tests: Settings loads both BC profiles and Frontu config from .env.example."""

import pytest

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
)


@pytest.mark.usefixtures("isolated_env")
def test_settings_instantiate_from_env_example() -> None:
    settings = get_settings()

    assert settings.database_url.scheme == "postgresql+asyncpg"
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


@pytest.mark.usefixtures("isolated_env")
def test_secrets_are_masked() -> None:
    settings = get_settings()

    for secret in (settings.bc_client_secret_saas, settings.bc_client_secret_onprem):
        assert str(secret) == "**********"


@pytest.mark.parametrize("field_name", SAAS_FIELDS + ONPREM_FIELDS)
def test_profile_credentials_are_required(field_name: str) -> None:
    assert Settings.model_fields[field_name].is_required()
