from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    name: str = "CodeMate"
    version: str = "0.1.0"
    environment: Literal["local", "test", "development", "staging", "production"] = "local"
    debug: bool = False


class WorkspaceConfig(BaseModel):
    root: Path = Field(default_factory=Path.cwd)
    max_file_bytes: int = 1_000_000
    max_tree_entries: int = 5_000
    ignored_names: tuple[str, ...] = (
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".env",
    )

    @field_validator("root")
    @classmethod
    def normalize_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class StorageConfig(BaseModel):
    database_url: str = "sqlite+aiosqlite:///./codemate.db"
    enabled: bool = False


class SessionMemoryConfig(BaseModel):
    enabled: bool = True
    max_turns: int = Field(default=10, gt=0)
    max_sessions: int = Field(default=100, gt=0)


ModelProviderName = Literal[
    "mock",
    "openai_compatible",
    "anthropic",
    "claude",
    "ollama",
    "deepseek",
    "qwen",
    "zhipu",
    "moonshot",
    "baichuan",
]


class ModelProviderConfig(BaseModel):
    provider: ModelProviderName = "mock"
    base_url: str | None = None
    name: str = "mock-model"
    api_key: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "ModelProviderConfig":
        base_url_required_providers = {
            "openai_compatible",
            "ollama",
            "deepseek",
            "qwen",
            "zhipu",
            "moonshot",
            "baichuan",
        }
        if self.provider in base_url_required_providers and not self.base_url:
            msg = f"model_base_url is required when model_provider is {self.provider}"
            raise ValueError(msg)
        return self


class SecurityConfig(BaseModel):
    enable_sensitive_content_detection: bool = True
    enable_rate_limit: bool = False
    rate_limit_per_minute: int = 60


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_enabled: bool = False
    access_log_enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CodeMate"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "development", "staging", "production"] = "local"
    debug: bool = False

    host: str = "127.0.0.1"
    port: int = 8000

    log_level: str = "INFO"
    log_json_enabled: bool = False
    access_log_enabled: bool = True

    workspace_root: Path = Field(default_factory=Path.cwd)
    workspace_max_file_bytes: int = 1_000_000
    workspace_max_tree_entries: int = 5_000

    database_url: str = "sqlite+aiosqlite:///./codemate.db"
    storage_enabled: bool = False

    session_memory_enabled: bool = True
    session_memory_max_turns: int = 10
    session_memory_max_sessions: int = 100

    model_provider: ModelProviderName = "mock"
    model_base_url: str | None = None
    model_name: str = "mock-model"
    model_api_key: str | None = None
    model_timeout_seconds: float = 60.0
    model_max_retries: int = 2

    enable_sensitive_content_detection: bool = True
    enable_rate_limit: bool = False
    rate_limit_per_minute: int = 60

    @property
    def app(self) -> AppConfig:
        return AppConfig(
            name=self.app_name,
            version=self.app_version,
            environment=self.environment,
            debug=self.debug,
        )

    @property
    def workspace(self) -> WorkspaceConfig:
        return WorkspaceConfig(
            root=self.workspace_root,
            max_file_bytes=self.workspace_max_file_bytes,
            max_tree_entries=self.workspace_max_tree_entries,
        )

    @property
    def storage(self) -> StorageConfig:
        return StorageConfig(
            database_url=self.database_url,
            enabled=self.storage_enabled,
        )

    @property
    def session_memory(self) -> SessionMemoryConfig:
        return SessionMemoryConfig(
            enabled=self.session_memory_enabled,
            max_turns=self.session_memory_max_turns,
            max_sessions=self.session_memory_max_sessions,
        )

    @property
    def model(self) -> ModelProviderConfig:
        return ModelProviderConfig(
            provider=self.model_provider,
            base_url=self.model_base_url,
            name=self.model_name,
            api_key=self.model_api_key,
            timeout_seconds=self.model_timeout_seconds,
            max_retries=self.model_max_retries,
        )

    @property
    def security(self) -> SecurityConfig:
        return SecurityConfig(
            enable_sensitive_content_detection=self.enable_sensitive_content_detection,
            enable_rate_limit=self.enable_rate_limit,
            rate_limit_per_minute=self.rate_limit_per_minute,
        )

    @property
    def logging(self) -> LoggingConfig:
        return LoggingConfig(
            level=self.log_level,
            json_enabled=self.log_json_enabled,
            access_log_enabled=self.access_log_enabled,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
