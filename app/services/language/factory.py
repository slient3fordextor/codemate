from pathlib import Path

from app.services.language.context import LanguageContextService
from app.services.language.detector import LanguageDetector
from app.services.language.interfaces import LanguageContextConfig, LanguageDetectionRule
from app.services.language.registry import build_default_language_registry


def build_language_context_service(
    workspace_root: Path,
    config: LanguageContextConfig | None = None,
) -> LanguageContextService:
    rules = (
        LanguageDetectionRule(
            language="python",
            extensions=frozenset({".py", ".pyi"}),
            project_files=frozenset(
                {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "uv.lock"}
            ),
            package_manager_files={"uv.lock": "uv", "pyproject.toml": "python"},
            default_package_manager="python",
        ),
        LanguageDetectionRule(
            language="typescript",
            extensions=frozenset({".ts", ".tsx", ".mts", ".cts"}),
            project_files=frozenset({"tsconfig.json"}),
            package_manager_files={
                "pnpm-lock.yaml": "pnpm",
                "yarn.lock": "yarn",
                "package-lock.json": "npm",
                "package.json": "npm",
            },
            default_package_manager="npm",
        ),
        LanguageDetectionRule(
            language="javascript",
            extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
            project_files=frozenset({"package.json", "jsconfig.json"}),
            package_manager_files={
                "pnpm-lock.yaml": "pnpm",
                "yarn.lock": "yarn",
                "package-lock.json": "npm",
                "package.json": "npm",
            },
            default_package_manager="npm",
        ),
    )
    detector = LanguageDetector(workspace_root, rules)
    return LanguageContextService(
        workspace_root=workspace_root,
        detector=detector,
        registry=build_default_language_registry(),
        config=config,
    )
