import json
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.adapters.models.base import ModelChunk, ModelRequest


class ScriptedModel:
    """Offline native-tool responses; proposal IDs come from actual tool results."""

    supports_tools = True

    def __init__(self, steps: list[tuple[str, dict[str, Any]] | str]) -> None:
        self.steps = steps
        self.requests: list[ModelRequest] = []

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        assert self.steps, "Unexpected model request"
        step = self.steps.pop(0)
        if isinstance(step, str):
            yield ModelChunk(type="message.delta", content=step)
        else:
            tool, arguments = step
            arguments = dict(arguments)
            if tool == "apply_patch" and not arguments:
                result = json.loads(request.messages[-1].content)
                arguments = {"proposal_id": result["metadata"]["proposal_id"]}
            encoded = json.dumps(arguments)
            yield ModelChunk(
                type="tool.call.start",
                tool_call_id="call-1",
                tool_name=tool,
            )
            midpoint = len(encoded) // 2
            for part in (encoded[:midpoint], encoded[midpoint:]):
                yield ModelChunk(type="tool.call.delta", tool_arguments=part)
            yield ModelChunk(type="tool.call.done")
        yield ModelChunk(type="message.done")


def require_bubblewrap(required: bool) -> None:
    if sys.platform == "linux" and shutil.which("bwrap"):
        return
    message = "Real sandbox tests require Linux and Bubblewrap"
    if required:
        pytest.fail(message)
    pytest.skip(message)


def create_repository(path: Path) -> Path:
    path.mkdir()
    (path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (path / "app.py").write_text("value = 1\n", encoding="utf-8")
    (path / "test_value.py").write_text(
        "import unittest\nimport app\n"
        "class ValueTest(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(app.value, 2)\n",
        encoding="utf-8",
    )
    for arguments in (
        ("init",),
        ("config", "user.email", "test@example.test"),
        ("config", "user.name", "Safety Test"),
        ("add", "."),
        ("commit", "-m", "baseline"),
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=path,
            check=True,
            capture_output=True,
            timeout=10,
        )
    return path


def coding_steps(*, validate: bool = True) -> list[tuple[str, dict[str, Any]] | str]:
    steps: list[tuple[str, dict[str, Any]] | str] = [
        ("read_file", {"path": "app.py"}),
        ("propose_write", {"path": "app.py", "content": "value = 2\n"}),
        ("apply_patch", {}),
    ]
    if validate:
        steps.append(("run_command", {"argv": ["python", "-m", "unittest", "-v"]}))
    steps.append("Completed with evidence")
    return steps
