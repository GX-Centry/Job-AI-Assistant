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

    # M1-c ASR(faster-whisper,服务端本机转写;档位评测与升级路线见
    # experiments/tool-research/REPORT.md —— small/int8 已中文实测通过)。
    asr_model: str = "small"
    asr_compute_type: str = "int8"
    # 权重缓存目录(HF 布局);首跑会下载锁定档位权重,生产预置或离线缓存。
    asr_model_cache_dir: str = "data/asr_models"
    # 超长音频只转写前 N 秒并显式标记(CPU 成本与上下文保护)。
    asr_max_seconds: float = Field(default=600.0, gt=0.0)


def get_settings() -> Settings:
    return Settings()
