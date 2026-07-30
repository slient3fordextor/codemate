from app.services.language.adapters.base import RuleBasedLanguageAdapter


class PythonLanguageAdapter(RuleBasedLanguageAdapter):
    language = "python"
    supported_extensions = frozenset({".py", ".pyi"})
    project_markers: tuple[str, ...] = (
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "uv.lock",
    )
    default_package_manager = "python"
    test_command = ("python", "-m", "pytest")
    format_command = ("ruff", "format", ".")
    base_hints: tuple[str, ...] = (
        "Prefer pytest for tests when the project already uses it.",
        "Prefer ruff-compatible formatting and linting when available.",
    )
