"""Server-side settings. API keys never leave this process."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application and LLM configuration loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./backend/data/app.db"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("llm_base_url", "llm_model", mode="before")
    @classmethod
    def _strip_optional(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def llm_configured(self) -> bool:
        key = self.llm_api_key.get_secret_value().strip()
        return bool(self.llm_base_url and self.llm_model and key)

    def resolve_sqlite_path(self) -> Path | None:
        """Return filesystem path for sqlite URLs; None for non-sqlite URLs."""
        url = self.database_url
        if not url.startswith("sqlite:///"):
            return None
        raw = url.removeprefix("sqlite:///")
        path = Path(raw)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
