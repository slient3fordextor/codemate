from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised for invalid repositories, lifecycle violations, or Git failures."""


@dataclass(frozen=True)
class TaskWorktree:
    task_id: str
    repository_root: Path
    path: Path
    base_commit: str
    base_branch: str | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        task_id: str,
        repository_root: Path,
        path: Path,
        base_commit: str,
        base_branch: str | None,
    ) -> "TaskWorktree":
        return cls(
            task_id=task_id,
            repository_root=repository_root,
            path=path,
            base_commit=base_commit,
            base_branch=base_branch,
            created_at=datetime.now(UTC),
        )


@dataclass(frozen=True)
class WorktreeChangeSet:
    tracked_patch: bytes
    untracked_patch: bytes
    untracked_paths: tuple[Path, ...]

    @property
    def patch(self) -> bytes:
        if not self.tracked_patch:
            return self.untracked_patch
        if not self.untracked_patch:
            return self.tracked_patch
        separator = b"" if self.tracked_patch.endswith(b"\n") else b"\n"
        return self.tracked_patch + separator + self.untracked_patch

    @property
    def dirty(self) -> bool:
        return bool(self.tracked_patch or self.untracked_paths)


@dataclass(frozen=True)
class DeliveryResult:
    task_id: str
    target_path: Path
    changed_paths: tuple[Path, ...]
    applied_at: datetime

    @classmethod
    def create(
        cls,
        task_id: str,
        target_path: Path,
        changed_paths: tuple[Path, ...],
    ) -> "DeliveryResult":
        return cls(task_id, target_path, changed_paths, datetime.now(UTC))
