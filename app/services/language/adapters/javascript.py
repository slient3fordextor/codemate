from pathlib import Path

from app.services.language.adapters.base import RuleBasedLanguageAdapter
from app.services.language.interfaces import ProjectInfo


class JavaScriptLanguageAdapter(RuleBasedLanguageAdapter):
    language = "javascript"
    supported_extensions = frozenset({".js", ".jsx", ".mjs", ".cjs"})
    project_markers: tuple[str, ...] = (
        "package.json",
        "jsconfig.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    )
    default_package_manager = "npm"
    test_command = ("npm", "test")
    format_command = ("npm", "run", "format")
    base_hints: tuple[str, ...] = (
        "Use package.json scripts when suggesting project commands.",
        "Follow the existing module style before introducing new tooling.",
    )

    def detect_project(self, workspace_root: Path, file_path: Path | None) -> ProjectInfo | None:
        project = super().detect_project(workspace_root, file_path)
        if project is None:
            return None

        package_manager = "npm"
        if (project.root / "pnpm-lock.yaml").is_file():
            package_manager = "pnpm"
        elif (project.root / "yarn.lock").is_file():
            package_manager = "yarn"

        return ProjectInfo(
            root=project.root,
            language=project.language,
            config_files=project.config_files,
            source_roots=project.source_roots,
            test_roots=project.test_roots,
            package_manager=package_manager,
        )
