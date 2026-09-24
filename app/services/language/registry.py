from app.services.language.adapters import (
    JavaScriptLanguageAdapter,
    PythonLanguageAdapter,
    TypeScriptLanguageAdapter,
)
from app.services.language.interfaces import LanguageAdapter


class LanguageRegistry:
    def __init__(self, adapters: tuple[LanguageAdapter, ...]) -> None:
        self._adapters = {adapter.language: adapter for adapter in adapters}

    def get(self, language: str) -> LanguageAdapter | None:
        return self._adapters.get(language)


def build_default_language_registry() -> LanguageRegistry:
    return LanguageRegistry(
        (
            PythonLanguageAdapter(),
            JavaScriptLanguageAdapter(),
            TypeScriptLanguageAdapter(),
        )
    )
