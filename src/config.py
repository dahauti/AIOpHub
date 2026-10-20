"""Typed application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, SecretStr, UrlConstraints
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

AsyncpgDsn = Annotated[
    MultiHostUrl,
    UrlConstraints(host_required=True, allowed_schemes=["postgresql+asyncpg"]),
]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration; field names map case-insensitively to `.env.example` keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: AsyncpgDsn

    # Business Central SaaS (Entra ID)
    bc_tenant_id_saas: str = Field(min_length=1)
    bc_client_id_saas: str = Field(min_length=1)
    bc_client_secret_saas: SecretStr
    bc_environment_saas: str = Field(min_length=1)
    bc_company_id_saas: str = Field(min_length=1)

    # Frontu API Prod
    frontu_api_key: SecretStr
    frontu_subdomain: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    internal_company_client_id: str = Field(min_length=1)

    # App settings
    log_level: LogLevel = "INFO"
    app_port: int = Field(default=8000, ge=1, le=65535)
    bc_active_profile: Literal["saas", "onprem"] = "onprem"
    sync_interval_seconds: int = Field(default=300, ge=1)

    # Business Central OnPrem Prod
    bc_tenant_id_onprem: str
    bc_client_id_onprem: str
    bc_client_secret_onprem: SecretStr
    bc_scope_onprem: str = "https://api.businesscentral.dynamics.com/.default"
    bc_odata_base_url_onprem: HttpUrl
    bc_company_id_onprem: str = Field(min_length=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance, loaded once."""
    return Settings()
