"""Typed application settings loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

AppEnv = Literal["dev", "prod"]
# One database per environment holds vehicle and task state, locks and the shared audit log.
ENV_DATABASE_URLS: dict[AppEnv, str] = {
    "dev": "sqlite+aiosqlite:///./data/aio_dev.db",
    "prod": "sqlite+aiosqlite:///./data/aio_prod.db",
}
FRONTU_BASE_URL = "https://{subdomain}.frontu.com/client/api/v2"
SQLITE_DRIVER = "sqlite+aiosqlite"
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration; field names map case-insensitively to `.env.example` keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment profile: selects the database and any DEV_/PROD_ overrides below
    app_env: AppEnv = "dev"
    dev_bc_company: str | None = Field(default=None, min_length=1)
    prod_bc_company: str | None = Field(default=None, min_length=1)
    dev_frontu_base_url: HttpUrl | None = None
    prod_frontu_base_url: HttpUrl | None = None
    dev_frontu_api_key: SecretStr | None = None
    prod_frontu_api_key: SecretStr | None = None

    # Database (explicit override; unset -> ENV_DATABASE_URLS[app_env])
    database_url: str | None = None
    task_safety_buffer_seconds: int = Field(default=900, ge=0)

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
    sync_initial_lookback_days: int = Field(default=0, ge=0)
    prototype_enabled: bool = False  # /prototype UX preview (fixture data only)

    # Business Central OnPrem Prod
    bc_tenant_id_onprem: str
    bc_client_id_onprem: str
    bc_client_secret_onprem: SecretStr
    bc_scope_onprem: str = "https://api.businesscentral.dynamics.com/.default"
    bc_odata_base_url_onprem: HttpUrl
    bc_company_id_onprem: str = Field(min_length=1)

    @field_validator("database_url")
    @classmethod
    def _require_aiosqlite(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            driver = make_url(value).drivername
        except ArgumentError as exc:
            raise ValueError(f"DATABASE_URL must be a {SQLITE_DRIVER} URL") from exc
        if driver != SQLITE_DRIVER:
            raise ValueError(f"DATABASE_URL must use the {SQLITE_DRIVER} driver, got {driver!r}")
        return value

    @property
    def active_database_url(self) -> str:
        return self.database_url or ENV_DATABASE_URLS[self.app_env]

    @property
    def active_bc_company(self) -> str:
        """Profile override, else the company of the active BC endpoint profile."""
        override = self.dev_bc_company if self.app_env == "dev" else self.prod_bc_company
        if override:
            return override
        if self.bc_active_profile == "saas":
            return self.bc_company_id_saas
        return self.bc_company_id_onprem

    @property
    def active_frontu_base_url(self) -> str:
        """Profile override, else the API root derived from `FRONTU_SUBDOMAIN`."""
        override = self.dev_frontu_base_url if self.app_env == "dev" else self.prod_frontu_base_url
        if override is not None:
            return str(override).rstrip("/")
        return FRONTU_BASE_URL.format(subdomain=self.frontu_subdomain)

    @property
    def active_frontu_api_key(self) -> SecretStr:
        """Profile override, else `FRONTU_API_KEY`; stays a SecretStr so it never logs."""
        override = self.dev_frontu_api_key if self.app_env == "dev" else self.prod_frontu_api_key
        return override if override is not None else self.frontu_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance, loaded once."""
    return Settings()
