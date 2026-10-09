"""Application settings loaded from environment / backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    agent_model: str = "gpt-4o-mini"
    patient_model: str = "gpt-4o-mini"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    def has_openai_api_key(self) -> bool:
        key = (self.openai_api_key or "").strip()
        return bool(key) and not key.startswith("sk-your-key")


@lru_cache
def get_settings() -> Settings:
    return Settings()
