from collections.abc import Generator
from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "SESSION_MEMORY_DATABASE_PATH",
        str(tmp_path / ".codemate" / "memory.sqlite3"),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
