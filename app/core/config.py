from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CodeMate"
    app_version: str = "0.1.0"
    environment: str = "local"
    log_level: str = "INFO"
    workspace_root: Path = Field(default_factory=Path.cwd)
    database_url: str = "sqlite+aiosqlite:///./codemate.db"
    model_provider: str = "mock"
    model_base_url: str | None = None
    model_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
