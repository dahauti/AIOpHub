"""Typed application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./data/sync.db"
SQLITE_DRIVER = "sqlite+aiosqlite"
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration; field names map case-insensitively to `.env.example` keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = DEFAULT_DATABASE_URL

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
    sync_enabled: bool = False
    sync_dry_run: bool = True

    # Business Central OnPrem Prod
    bc_tenant_id_onprem: str
    bc_client_id_onprem: str
    bc_client_secret_onprem: SecretStr
    bc_scope_onprem: str = "https://api.businesscentral.dynamics.com/.default"
    bc_odata_base_url_onprem: HttpUrl
    bc_company_id_onprem: str = Field(min_length=1)

    @field_validator("database_url")
    @classmethod
    def _require_aiosqlite(cls, value: str) -> str:
        try:
            driver = make_url(value).drivername
        except ArgumentError as exc:
            raise ValueError(f"DATABASE_URL must be a {SQLITE_DRIVER} URL") from exc
        if driver != SQLITE_DRIVER:
            raise ValueError(f"DATABASE_URL must use the {SQLITE_DRIVER} driver, got {driver!r}")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance, loaded once."""
    return Settings()
