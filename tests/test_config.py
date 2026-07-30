from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_workspace_root_is_normalized(tmp_path: Path) -> None:
    settings = Settings(workspace_root=tmp_path / ".")

    assert settings.workspace.root == tmp_path.resolve()


def test_model_provider_defaults_to_mock() -> None:
    settings = Settings()

    assert settings.model.provider == "mock"
    assert settings.model.name == "mock-model"
    assert settings.model.timeout_seconds == 60.0
    assert settings.model.max_retries == 2


def test_openai_compatible_provider_requires_base_url() -> None:
    settings = Settings(model_provider="openai_compatible")

    with pytest.raises(ValidationError):
        _ = settings.model


def test_openai_compatible_provider_accepts_openai_or_deepseek_base_url() -> None:
    settings = Settings(
        model_provider="openai_compatible",
        model_base_url="https://api.deepseek.com",
        model_name="deepseek-chat",
        model_api_key="test-key",
    )

    assert settings.model.provider == "openai_compatible"
    assert settings.model.base_url == "https://api.deepseek.com"
    assert settings.model.name == "deepseek-chat"


def test_anthropic_provider_can_use_default_base_url() -> None:
    settings = Settings(
        model_provider="anthropic",
        model_name="claude-3-5-sonnet-latest",
        model_api_key="test-key",
    )

    assert settings.model.provider == "anthropic"
    assert settings.model.base_url is None
    assert settings.model.name == "claude-3-5-sonnet-latest"


def test_domestic_openai_compatible_provider_requires_base_url() -> None:
    settings = Settings(model_provider="qwen")

    with pytest.raises(ValidationError):
        _ = settings.model


def test_domestic_openai_compatible_provider_accepts_base_url() -> None:
    settings = Settings(
        model_provider="qwen",
        model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-plus",
        model_api_key="test-key",
    )

    assert settings.model.provider == "qwen"
    assert settings.model.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.model.name == "qwen-plus"


def test_storage_config_is_disabled_by_default() -> None:
    settings = Settings()

    assert settings.storage.enabled is False
    assert settings.storage.database_url == "sqlite+aiosqlite:///./codemate.db"


def test_session_memory_config_defaults_to_persistent_layers_enabled() -> None:
    settings = Settings()

    assert settings.session_memory.enabled is True
    assert settings.session_memory.backend == "sqlite"
    assert settings.session_memory.database_path.name == "memory.sqlite3"
    assert settings.session_memory.max_turns == 10
    assert settings.session_memory.max_sessions == 100
    assert settings.session_memory.medium_term_enabled is True
    assert settings.session_memory.medium_term_max_tokens == 1_536
    assert settings.session_memory.long_term_enabled is True
    assert settings.session_memory.long_term_max_items == 20


def test_legacy_character_budget_is_ignored_with_deprecation_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSION_MEMORY_MEDIUM_TERM_MAX_CHARS", "9000")

    with pytest.warns(DeprecationWarning, match="MAX_TOKENS"):
        settings = Settings()

    assert settings.session_memory.medium_term_max_tokens == 1_536


def test_get_settings_can_be_reloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "CodeMate Test")
    get_settings.cache_clear()

    assert get_settings().app.name == "CodeMate Test"
