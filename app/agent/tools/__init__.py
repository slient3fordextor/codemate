"""Built-in Agent tools with explicit side-effect boundaries."""

from app.agent.tools.coding import ControlledToolExecutor, PermissionMode
from app.agent.tools.desktop import DesktopControl
from app.agent.tools.editing import AppliedEdit, EditToolError, PatchProposal, WorkspaceEditor
from app.agent.tools.readonly import (
    ReadonlyToolError,
    ReadonlyToolExecutor,
    ToolExecutionResult,
)

__all__ = [
    "AppliedEdit",
    "ControlledToolExecutor",
    "DesktopControl",
    "EditToolError",
    "PatchProposal",
    "PermissionMode",
    "ReadonlyToolError",
    "ReadonlyToolExecutor",
    "ToolExecutionResult",
    "WorkspaceEditor",
]
