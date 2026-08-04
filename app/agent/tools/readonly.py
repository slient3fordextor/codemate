import json
import os
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.adapters.models.base import ModelToolDefinition


class ReadonlyToolError(ValueError):
    """Raised when a read-only tool request violates its execution contract."""


@dataclass(frozen=True)
class ToolExecutionResult:
    tool: str
    content: str
    metadata: dict[str, Any]

    def as_message(self) -> str:
        return json.dumps(
            {"tool": self.tool, "content": self.content, "metadata": self.metadata},
            ensure_ascii=False,
        )


class ReadonlyToolExecutor:
    """Executes bounded, deterministic reads inside one workspace."""

    _sensitive_names = frozenset(
        {
            ".git-credentials",
            ".netrc",
            ".npmrc",
            ".pypirc",
            "credentials.json",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
            "id_rsa",
        }
    )
    _sensitive_suffixes = frozenset({".key", ".p12", ".pfx"})

    definitions: tuple[dict[str, Any], ...] = (
        {
            "name": "list_files",
            "description": "List project files below a workspace-relative directory.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "additionalProperties": False,
            },
        },
        {
            "name": "read_file",
            "description": "Read one UTF-8 compatible text file from the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_text",
            "description": "Search project text files for a literal string.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "git_status",
            "description": "Return Git porcelain status for the workspace repository.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "git_diff",
            "description": "Return the current tracked Git diff without changing the repository.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    )

    @property
    def model_definitions(self) -> tuple[ModelToolDefinition, ...]:
        return tuple(
            ModelToolDefinition(
                name=str(definition["name"]),
                description=str(definition["description"]),
                input_schema=dict(definition["input_schema"]),
            )
            for definition in self.definitions
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are a read-only coding agent. Inspect the workspace with the supplied "
            "tools, one tool call per round. Give a concise final answer supported by "
            "collected evidence. Never claim to edit files or run commands."
        )

    @staticmethod
    def requires_approval(tool: str) -> bool:
        del tool
        return False

    @staticmethod
    def approval_preview(tool: str, arguments: dict[str, Any]) -> str:
        del tool, arguments
        return ""

    @staticmethod
    def cacheable(tool: str) -> bool:
        del tool
        return True

    @staticmethod
    def completion_error() -> str | None:
        return None

    def __init__(
        self,
        workspace_root: Path,
        *,
        ignored_names: tuple[str, ...] = (
            ".git",
            ".venv",
            "__pycache__",
            "node_modules",
            ".env",
        ),
        max_file_bytes: int = 1_000_000,
        max_tree_entries: int = 5_000,
        max_output_bytes: int = 200_000,
        git_timeout_seconds: float = 10.0,
    ) -> None:
        root = workspace_root.expanduser().resolve()
        if not root.is_dir():
            raise ReadonlyToolError(f"Workspace does not exist: {root}")
        if min(max_file_bytes, max_tree_entries, max_output_bytes) <= 0:
            raise ValueError("Read-only tool limits must be greater than zero")
        if git_timeout_seconds <= 0:
            raise ValueError("Git timeout must be greater than zero")
        self.workspace_root = root
        self._ignored_names = frozenset(ignored_names)
        self._max_file_bytes = max_file_bytes
        self._max_tree_entries = max_tree_entries
        self._max_output_bytes = max_output_bytes
        self._git_timeout_seconds = git_timeout_seconds

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        approval_id: str | None = None,
    ) -> ToolExecutionResult:
        if approval_id is not None:
            raise ReadonlyToolError("Read-only tools do not accept write approval")
        if tool == "list_files":
            self._validate_arguments(arguments, {"path"})
            return self._list_files(self._string_argument(arguments, "path", default="."))
        if tool == "read_file":
            self._validate_arguments(arguments, {"path"})
            return self._read_file(self._string_argument(arguments, "path"))
        if tool == "search_text":
            self._validate_arguments(arguments, {"query", "path"})
            return self._search_text(
                self._string_argument(arguments, "query"),
                self._string_argument(arguments, "path", default="."),
            )
        if tool == "git_status":
            self._require_no_arguments(arguments)
            return self._git_result(tool, "status", "--short", "--untracked-files=all")
        if tool == "git_diff":
            self._require_no_arguments(arguments)
            return self._git_result(tool, "diff", "--no-ext-diff", "--no-textconv", "--")
        raise ReadonlyToolError(f"Unsupported read-only tool: {tool}")

    def _list_files(self, relative_path: str) -> ToolExecutionResult:
        directory = self._resolve(relative_path)
        if not directory.is_dir():
            raise ReadonlyToolError(f"Directory does not exist: {relative_path}")
        files: list[str] = []
        truncated = False
        for path in self._iter_files(directory):
            files.append(path.relative_to(self.workspace_root).as_posix())
            if len(files) >= self._max_tree_entries:
                truncated = True
                break
        return ToolExecutionResult(
            tool="list_files",
            content="\n".join(files),
            metadata={"entries": len(files), "truncated": truncated},
        )

    def _read_file(self, relative_path: str) -> ToolExecutionResult:
        path = self._resolve(relative_path)
        if not path.is_file():
            raise ReadonlyToolError(f"File does not exist: {relative_path}")
        size = path.stat().st_size
        if size > self._max_file_bytes:
            raise ReadonlyToolError(
                f"File exceeds the {self._max_file_bytes}-byte read limit: {relative_path}"
            )
        content = path.read_bytes()
        if b"\x00" in content:
            raise ReadonlyToolError(
                f"Binary files are not readable as model context: {relative_path}"
            )
        if b"PRIVATE KEY" in content:
            raise ReadonlyToolError("Private key material is blocked by the workspace policy")
        rendered = content.decode("utf-8", errors="replace")
        return ToolExecutionResult(
            tool="read_file",
            content=self._bounded(rendered),
            metadata={
                "path": path.relative_to(self.workspace_root).as_posix(),
                "bytes": size,
                "sha256": sha256(content).hexdigest(),
            },
        )

    def _search_text(self, query: str, relative_path: str) -> ToolExecutionResult:
        if not query:
            raise ReadonlyToolError("Search query cannot be empty")
        target = self._resolve(relative_path)
        candidates = [target] if target.is_file() else self._iter_files(target)
        matches: list[str] = []
        scanned = 0
        for path in candidates:
            if self._is_ignored(path) or not path.is_file():
                continue
            try:
                if path.stat().st_size > self._max_file_bytes:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            if b"PRIVATE KEY" in raw:
                continue
            scanned += 1
            for line_number, line in enumerate(
                raw.decode("utf-8", errors="replace").splitlines(), start=1
            ):
                if query not in line:
                    continue
                relative = path.relative_to(self.workspace_root).as_posix()
                matches.append(f"{relative}:{line_number}:{line[:300]}")
                if len(matches) >= 100:
                    return ToolExecutionResult(
                        tool="search_text",
                        content=self._bounded("\n".join(matches)),
                        metadata={"matches": len(matches), "scanned": scanned, "truncated": True},
                    )
        return ToolExecutionResult(
            tool="search_text",
            content=self._bounded("\n".join(matches)),
            metadata={"matches": len(matches), "scanned": scanned, "truncated": False},
        )

    def _git_result(self, tool: str, *args: str) -> ToolExecutionResult:
        try:
            completed = subprocess.run(
                (
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    *args,
                ),
                cwd=self.workspace_root,
                capture_output=True,
                check=False,
                env={
                    **os.environ,
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                },
                timeout=self._git_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReadonlyToolError("Git read timed out") from exc
        except OSError as exc:
            raise ReadonlyToolError("Git is unavailable") from exc
        if completed.returncode:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ReadonlyToolError(message or "Git read failed")
        output = completed.stdout.decode("utf-8", errors="replace")
        return ToolExecutionResult(
            tool=tool,
            content=self._bounded(output),
            metadata={
                "bytes": len(completed.stdout),
                "truncated": len(completed.stdout) > self._max_output_bytes,
            },
        )

    def _resolve(self, raw_path: str) -> Path:
        path = Path(raw_path)
        candidate = path.resolve() if path.is_absolute() else (self.workspace_root / path).resolve()
        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ReadonlyToolError("Path escapes the workspace") from exc
        if any(self._is_blocked_name(part) for part in relative.parts):
            raise ReadonlyToolError("Path is blocked by the workspace policy")
        return candidate

    def _is_ignored(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.workspace_root)
        except (OSError, ValueError):
            return True
        return any(self._is_blocked_name(part) for part in relative.parts)

    def _is_blocked_name(self, name: str) -> bool:
        lowered = name.casefold()
        return (
            name in self._ignored_names
            or lowered == ".env"
            or lowered.startswith(".env.")
            or lowered in self._sensitive_names
            or Path(lowered).suffix in self._sensitive_suffixes
        )

    def _iter_files(self, directory: Path) -> list[Path]:
        files: list[Path] = []
        for root, directories, names in os.walk(directory, followlinks=False):
            directories[:] = sorted(name for name in directories if not self._is_blocked_name(name))
            for name in sorted(names):
                path = Path(root) / name
                if not self._is_ignored(path):
                    files.append(path)
                if len(files) >= self._max_tree_entries:
                    return files
        return files

    def _bounded(self, value: str) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= self._max_output_bytes:
            return value
        return encoded[: self._max_output_bytes].decode("utf-8", errors="ignore") + "\n[truncated]"

    def _string_argument(
        self,
        arguments: dict[str, Any],
        key: str,
        *,
        default: str | None = None,
    ) -> str:
        value = arguments.get(key, default)
        if not isinstance(value, str) or (default is None and not value):
            raise ReadonlyToolError(f"Tool argument {key!r} must be a non-empty string")
        return value

    def _validate_arguments(self, arguments: dict[str, Any], allowed: set[str]) -> None:
        unknown = set(arguments) - allowed
        if unknown:
            raise ReadonlyToolError(f"Unknown tool arguments: {sorted(unknown)!r}")

    def _require_no_arguments(self, arguments: dict[str, Any]) -> None:
        if arguments:
            raise ReadonlyToolError("This tool accepts no arguments")
