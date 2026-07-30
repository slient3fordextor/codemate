from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class LanguageInfo:
    language: str
    file_path: Path | None = None
    project_type: str | None = None
    package_manager: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class ProjectInfo:
    root: Path
    language: str
    config_files: tuple[Path, ...] = ()
    source_roots: tuple[Path, ...] = ()
    test_roots: tuple[Path, ...] = ()
    package_manager: str | None = None


@dataclass(frozen=True)
class CodeContext:
    language: str
    current_file: Path | None = None
    selected_text: str | None = None
    related_files: tuple[Path, ...] = ()
    project_summary: str = ""
    system_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class LanguageContextConfig:
    enabled: bool = True
    default_level: str = "L1"
    max_files: int = 8
    max_bytes: int = 120_000
    enable_tree_sitter: bool = False
    enable_lsp: bool = False


@dataclass(frozen=True)
class LanguageDetectionRule:
    language: str
    extensions: frozenset[str] = field(default_factory=frozenset)
    project_files: frozenset[str] = field(default_factory=frozenset)
    package_manager_files: dict[str, str] = field(default_factory=dict)
    default_package_manager: str | None = None


class LanguageAdapter(Protocol):
    language: str
    supported_extensions: frozenset[str]

    def detect_project(self, workspace_root: Path, file_path: Path | None) -> ProjectInfo | None:
        ...

    def collect_context(
        self,
        project: ProjectInfo,
        current_file: Path | None,
        selected_text: str | None,
    ) -> CodeContext:
        ...

    def build_system_hints(self, project: ProjectInfo) -> tuple[str, ...]:
        ...

    def get_test_command(self, project: ProjectInfo) -> tuple[str, ...] | None:
        ...

    def get_format_command(self, project: ProjectInfo) -> tuple[str, ...] | None:
        ...

