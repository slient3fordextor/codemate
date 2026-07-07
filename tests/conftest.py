from collections.abc import Generator

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Generator[None]:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
