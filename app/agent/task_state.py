from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class TaskStateError(ValueError):
    """Raised when a durable task lifecycle transition is invalid."""


class TaskState(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    ACTIVE = "active"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELIVERED = "delivered"
    RETAINED = "retained"
    DISCARDED = "discarded"


_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.CREATED: frozenset({TaskState.PREPARING, TaskState.CANCELLED}),
    TaskState.PREPARING: frozenset({TaskState.ACTIVE, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.ACTIVE: frozenset(
        {
            TaskState.WAITING_APPROVAL,
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.WAITING_APPROVAL: frozenset(
        {TaskState.ACTIVE, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.COMPLETED: frozenset({TaskState.DELIVERED, TaskState.RETAINED, TaskState.DISCARDED}),
    TaskState.FAILED: frozenset({TaskState.ACTIVE, TaskState.RETAINED, TaskState.DISCARDED}),
    TaskState.CANCELLED: frozenset({TaskState.RETAINED, TaskState.DISCARDED}),
    TaskState.DELIVERED: frozenset({TaskState.DISCARDED}),
    TaskState.RETAINED: frozenset({TaskState.ACTIVE, TaskState.DELIVERED, TaskState.DISCARDED}),
    TaskState.DISCARDED: frozenset(),
}


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    objective: str
    repository_root: Path
    worktree_path: Path
    base_commit: str
    base_branch: str | None
    state: TaskState = TaskState.CREATED
    graph_id: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        task_id: str,
        objective: str,
        repository_root: Path,
        worktree_path: Path,
        base_commit: str,
        base_branch: str | None,
    ) -> "TaskRecord":
        now = datetime.now(UTC)
        return cls(
            task_id=task_id,
            objective=objective,
            repository_root=repository_root.resolve(),
            worktree_path=worktree_path.resolve(),
            base_commit=base_commit,
            base_branch=base_branch,
            created_at=now,
            updated_at=now,
        )

    def transition(
        self,
        state: TaskState,
        *,
        graph_id: str | None = None,
        error: str | None = None,
    ) -> "TaskRecord":
        if state not in _TRANSITIONS[self.state]:
            raise TaskStateError(f"Invalid task transition: {self.state} -> {state}")
        return replace(
            self,
            state=state,
            graph_id=graph_id if graph_id is not None else self.graph_id,
            error=error,
            updated_at=datetime.now(UTC),
        )

    def checkpoint(self, graph_id: str) -> "TaskRecord":
        if not graph_id:
            raise TaskStateError("Task graph id is required")
        return replace(self, graph_id=graph_id, updated_at=datetime.now(UTC))
