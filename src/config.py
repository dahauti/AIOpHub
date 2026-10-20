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
    bc_tenant_id: str = Field(min_length=1)
    bc_client_id: str = Field(min_length=1)
    bc_client_secret: SecretStr
    bc_environment: str = Field(min_length=1)
    bc_company_id: str = Field(min_length=1)

    # Frontu API
    frontu_api_key: SecretStr
    frontu_base_url: HttpUrl

    # App settings
    log_level: LogLevel = "INFO"
    app_port: int = Field(default=8000, ge=1, le=65535)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance, loaded once."""
    return Settings()
