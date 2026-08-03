from dataclasses import dataclass
from pathlib import Path


class SandboxPolicyError(ValueError):
    """Raised when a path or execution request escapes the sandbox contract."""


@dataclass(frozen=True)
class SandboxPolicy:
    """Path policy consumed by a future process-isolating Sandbox Executor.

    This object validates paths only. It is deliberately unable to authorize a
    command because it does not enforce mounts, networking, environment, or resources.
    """

    workspace_root: Path
    temporary_root: Path
    network_enabled: bool = False

    @property
    def process_isolation_enforced(self) -> bool:
        return False

    def require_process_isolation(self) -> None:
        raise SandboxPolicyError(
            "SandboxPolicy is not an execution boundary; a Sandbox Executor is required"
        )

    def __post_init__(self) -> None:
        workspace_root = self.workspace_root.resolve()
        temporary_root = self.temporary_root.resolve()
        if workspace_root == temporary_root:
            raise SandboxPolicyError("Workspace and temporary roots must differ")
        object.__setattr__(self, "workspace_root", workspace_root)
        object.__setattr__(self, "temporary_root", temporary_root)

    def resolve_workspace_path(self, path: Path) -> Path:
        candidate = (
            (self.workspace_root / path).resolve()
            if not path.is_absolute()
            else path.resolve()
        )
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise SandboxPolicyError("Path escapes the task workspace") from exc
        if candidate == self.workspace_root / ".git" or ".git" in candidate.parts:
            raise SandboxPolicyError("Git metadata is not exposed to sandboxed tools")
        return candidate
