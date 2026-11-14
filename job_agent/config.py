from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    model_provider: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None
    model_base_url: str | None = None

    model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    model_timeout_seconds: float = Field(default=30.0, gt=0.0)

    model_max_retries: int = Field(default=2, ge=0, le=10)


def get_settings() -> Settings:
    return Settings()
