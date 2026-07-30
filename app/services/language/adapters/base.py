from pathlib import Path

from app.services.language.interfaces import CodeContext, LanguageAdapter, ProjectInfo


class RuleBasedLanguageAdapter(LanguageAdapter):
    language: str
    supported_extensions: frozenset[str] = frozenset()
    project_markers: tuple[str, ...] = ()
    source_dir_names: tuple[str, ...] = ("src",)
    test_dir_names: tuple[str, ...] = ("tests", "test")
    default_package_manager: str | None = None
    test_command: tuple[str, ...] | None = None
    format_command: tuple[str, ...] | None = None
    base_hints: tuple[str, ...] = ()

    def detect_project(self, workspace_root: Path, file_path: Path | None) -> ProjectInfo | None:
        root = self._find_project_root(workspace_root, file_path) or workspace_root
        config_files = tuple(
            root / marker for marker in self.project_markers if (root / marker).is_file()
        )
        source_roots = tuple(
            root / name for name in self.source_dir_names if (root / name).is_dir()
        )
        test_roots = tuple(root / name for name in self.test_dir_names if (root / name).is_dir())

        if not config_files and file_path is None:
            return None

        return ProjectInfo(
            root=root,
            language=self.language,
            config_files=config_files,
            source_roots=source_roots,
            test_roots=test_roots,
            package_manager=self.default_package_manager,
        )

    def collect_context(
        self,
        project: ProjectInfo,
        current_file: Path | None,
        selected_text: str | None,
    ) -> CodeContext:
        return CodeContext(
            language=self.language,
            current_file=current_file,
            selected_text=selected_text,
            related_files=project.config_files,
            project_summary=self._build_project_summary(project),
            system_hints=self.build_system_hints(project),
        )

    def build_system_hints(self, project: ProjectInfo) -> tuple[str, ...]:
        hints = [
            f"Current language: {self.language}",
            f"Project root: {project.root}",
        ]
        if project.package_manager:
            hints.append(f"Package manager: {project.package_manager}")
        if project.config_files:
            names = ", ".join(path.name for path in project.config_files)
            hints.append(f"Detected config files: {names}")
        hints.extend(self.base_hints)
        return tuple(hints)

    def get_test_command(self, project: ProjectInfo) -> tuple[str, ...] | None:
        return self.test_command

    def get_format_command(self, project: ProjectInfo) -> tuple[str, ...] | None:
        return self.format_command

    def _find_project_root(self, workspace_root: Path, file_path: Path | None) -> Path | None:
        if file_path is None:
            candidates = [workspace_root]
        else:
            start = file_path if file_path.is_dir() else file_path.parent
            candidates = [start, *start.parents]

        for candidate in candidates:
            if workspace_root not in (candidate, *candidate.parents):
                continue
            if any((candidate / marker).is_file() for marker in self.project_markers):
                return candidate
        return None

    def _build_project_summary(self, project: ProjectInfo) -> str:
        parts = [f"{project.language} project"]
        if project.config_files:
            parts.append("configs: " + ", ".join(path.name for path in project.config_files))
        if project.source_roots:
            parts.append("source roots: " + ", ".join(path.name for path in project.source_roots))
        if project.test_roots:
            parts.append("test roots: " + ", ".join(path.name for path in project.test_roots))
        return "; ".join(parts)
