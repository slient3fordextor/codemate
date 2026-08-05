"""Controlled Git worktree lifecycle for writable Agent tasks."""

from app.agent.worktree.manager import GitAdapter, WorktreeManager
from app.agent.worktree.models import (
    DeliveryResult,
    RollbackResult,
    TaskWorktree,
    WorktreeChangeSet,
    WorktreeError,
)

__all__ = [
    "DeliveryResult",
    "GitAdapter",
    "RollbackResult",
    "TaskWorktree",
    "WorktreeChangeSet",
    "WorktreeError",
    "WorktreeManager",
]
