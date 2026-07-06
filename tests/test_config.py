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


def test_storage_config_is_disabled_by_default() -> None:
    settings = Settings()

    assert settings.storage.enabled is False
    assert settings.storage.database_url == "sqlite+aiosqlite:///./codemate.db"


def test_get_settings_can_be_reloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "CodeMate Test")
    get_settings.cache_clear()

    assert get_settings().app.name == "CodeMate Test"
