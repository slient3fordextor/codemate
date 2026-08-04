import json
from enum import StrEnum
from typing import Any

from app.adapters.models.base import ModelToolDefinition
from app.agent.sandbox import BubblewrapExecutor
from app.agent.tools.editing import EditToolError, WorkspaceEditor
from app.agent.tools.readonly import (
    ReadonlyToolError,
    ReadonlyToolExecutor,
    ToolExecutionResult,
)


class PermissionMode(StrEnum):
    READONLY = "readonly"
    CONFIRM = "confirm"
    AGENT = "agent"


class ControlledToolExecutor:
    """Combines reads, reviewable edits, and sandboxed verification commands."""

    _write_definitions: tuple[dict[str, Any], ...] = (
        {
            "name": "propose_write",
            "description": "Propose replacing one text file and return an exact review diff.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "apply_patch",
            "description": "Apply one previously returned patch proposal.",
            "input_schema": {
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_command",
            "description": (
                "Run an approved test, lint, type-check, or compile command without network."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 128,
                    }
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
        },
    )

    def __init__(
        self,
        readonly: ReadonlyToolExecutor,
        editor: WorkspaceEditor,
        sandbox: BubblewrapExecutor,
        mode: PermissionMode,
        *,
        workspace_dirty: bool = False,
        require_command_approval: bool = False,
    ) -> None:
        if mode is PermissionMode.READONLY:
            raise ValueError("Use ReadonlyToolExecutor for readonly mode")
        self._readonly = readonly
        self._editor = editor
        self._sandbox = sandbox
        self.mode = mode
        self.definitions = readonly.definitions + self._write_definitions
        self._workspace_revision = 1 if workspace_dirty else 0
        self._validated_revision: int | None = None
        self._require_command_approval = require_command_approval

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
            "You are a controlled coding agent operating in an isolated Git worktree. "
            "Inspect before editing. To change a file, call propose_write, review its exact "
            "diff, then call apply_patch with that proposal_id. Use run_command only for "
            "relevant verification. Call one tool per round and report validation failures "
            "honestly. Never claim changes were delivered to the user's source checkout."
        )

    def requires_approval(self, tool: str) -> bool:
        if self.mode is PermissionMode.CONFIRM and tool in {"apply_patch", "run_command"}:
            return True
        return self._require_command_approval and tool == "run_command"

    def approval_preview(self, tool: str, arguments: dict[str, Any]) -> str:
        if tool == "apply_patch":
            proposal_id = self._required_string(arguments, "proposal_id")
            return self._editor.proposal(proposal_id).diff
        if tool == "run_command":
            argv = self._argv(arguments)
            return json.dumps({"argv": argv, "network": False}, ensure_ascii=False)
        return ""

    def cacheable(self, tool: str) -> bool:
        return tool in {definition["name"] for definition in self._readonly.definitions}

    def completion_error(self) -> str | None:
        if self._workspace_revision and self._validated_revision != self._workspace_revision:
            return "Latest task worktree edits do not have successful command evidence"
        return None

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        approval_id: str | None = None,
    ) -> ToolExecutionResult:
        if tool in {definition["name"] for definition in self._readonly.definitions}:
            return self._readonly.execute(tool, arguments)
        try:
            if tool == "propose_write":
                self._validate_arguments(arguments, {"path", "content"})
                proposal = self._editor.propose_write(
                    self._required_string(arguments, "path"),
                    self._required_string(arguments, "content", allow_empty=True),
                )
                return ToolExecutionResult(
                    tool=tool,
                    content=proposal.diff,
                    metadata={
                        "proposal_id": proposal.id,
                        "path": proposal.relative_path.as_posix(),
                        "before_digest": proposal.before_digest,
                        "after_digest": proposal.after_digest,
                    },
                )
            if tool == "apply_patch":
                self._validate_arguments(arguments, {"proposal_id"})
                effective_approval = approval_id
                if effective_approval is None and self.mode is PermissionMode.AGENT:
                    effective_approval = "policy:agent-worktree"
                applied = self._editor.apply(
                    self._required_string(arguments, "proposal_id"),
                    approval_id=effective_approval or "",
                )
                self._workspace_revision += 1
                return ToolExecutionResult(
                    tool=tool,
                    content=f"Applied {applied.relative_path.as_posix()} in the task worktree.",
                    metadata={
                        "proposal_id": applied.proposal_id,
                        "path": applied.relative_path.as_posix(),
                        "approval_id": applied.approval_id,
                        "evidence_id": applied.evidence.id,
                    },
                )
            if tool == "run_command":
                self._validate_arguments(arguments, {"argv"})
                if self.requires_approval(tool) and not approval_id:
                    raise ReadonlyToolError("Command execution requires approval")
                artifact = self._sandbox.execute(self._argv(arguments))
                if artifact.passed:
                    self._validated_revision = self._workspace_revision
                content = artifact.stdout
                if artifact.stderr:
                    content += ("\n" if content else "") + artifact.stderr
                return ToolExecutionResult(
                    tool=tool,
                    content=content,
                    metadata={
                        "artifact_id": artifact.id,
                        "argv": list(artifact.argv),
                        "exit_code": artifact.exit_code,
                        "duration_seconds": artifact.duration_seconds,
                        "timed_out": artifact.timed_out,
                        "truncated": artifact.output_truncated,
                        "evidence_valid": artifact.passed,
                    },
                )
        except EditToolError as exc:
            raise ReadonlyToolError(str(exc)) from exc
        raise ReadonlyToolError(f"Unsupported controlled tool: {tool}")

    @staticmethod
    def _validate_arguments(arguments: dict[str, Any], allowed: set[str]) -> None:
        unknown = set(arguments) - allowed
        if unknown:
            raise ReadonlyToolError(f"Unknown tool arguments: {sorted(unknown)!r}")

    @staticmethod
    def _required_string(
        arguments: dict[str, Any],
        key: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise ReadonlyToolError(f"Tool argument {key!r} must be a string")
        return value

    @staticmethod
    def _argv(arguments: dict[str, Any]) -> list[str]:
        value = arguments.get("argv")
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ReadonlyToolError("Tool argument 'argv' must be a string array")
        return value
