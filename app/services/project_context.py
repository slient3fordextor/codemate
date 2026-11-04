import re
from pathlib import Path
from typing import Any

from app.agent.tools.readonly import ReadonlyToolError, ReadonlyToolExecutor
from app.schemas.chat import ContextAttachment

SECRET = re.compile(
    r"PRIVATE KEY|\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})|"
    r"(?:api[_-]?key|password|secret|token)\s*[:=]\s*[\"']?\S{8,}",
    re.I,
)


class ProjectContext:
    def __init__(self, root: Path, max_bytes: int = 120000, max_files: int = 8) -> None:
        self.tools = ReadonlyToolExecutor(
            root,
            ignored_names=(".git", ".venv", ".codemate", "__pycache__", "node_modules", ".env"),
            max_file_bytes=max_bytes,
            max_output_bytes=max_bytes,
        )
        self.max_bytes = max_bytes
        self.max_files = max_files

    def read(self, path: str) -> dict[str, Any]:
        result = self.tools.execute("read_file", {"path": path})
        self.validate_text(result.content)
        return {"content": result.content, **result.metadata}

    def files(self, path: str = ".") -> dict[str, Any]:
        result = self.tools.execute("list_files", {"path": path})
        return {"files": result.content.splitlines(), **result.metadata}

    def diff(self) -> dict[str, Any]:
        result = self.tools.execute("git_diff", {})
        self.validate_text(result.content)
        # Reject sensitive tracked file paths as well as sensitive content.
        for line in result.content.splitlines():
            if line.startswith(('+++ "', '--- "')):
                raise ReadonlyToolError("Quoted diff paths cannot be attached safely")
            if line.startswith(("+++ b/", "--- a/")):
                self.tools._resolve(line[6:])
        return {"content": result.content, **result.metadata}

    def validate(self, context: list[ContextAttachment]) -> None:
        if (
            len(context) > self.max_files
            or sum(len(item.content.encode()) for item in context) > self.max_bytes
        ):
            raise ReadonlyToolError("Project context exceeds its budget")
        for item in context:
            if item.path:
                self.tools._resolve(item.path)
            self.validate_text(item.content)

    @staticmethod
    def validate_text(content: str) -> None:
        if SECRET.search(content):
            raise ReadonlyToolError("Sensitive material cannot be attached as model context")
