import os
import re
import subprocess
from pathlib import Path

from app.agent.worktree.models import TaskWorktree, WorktreeChangeSet, WorktreeError

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class GitAdapter:
    """Small fixed-command Git interface; it never accepts model-provided argv."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Git timeout must be greater than zero")
        self._timeout_seconds = timeout_seconds

    def repository_root(self, path: Path) -> Path:
        return Path(self._git(path, "rev-parse", "--show-toplevel").strip()).resolve()

    def head_commit(self, repository_root: Path) -> str:
        return self._git(repository_root, "rev-parse", "HEAD").strip()

    def current_branch(self, repository_root: Path) -> str | None:
        branch = self._git(repository_root, "branch", "--show-current").strip()
        return branch or None

    def tracked_diff(self, repository_root: Path) -> bytes:
        return self._git_bytes(repository_root, "diff", "--binary", "HEAD")

    def task_changes(self, worktree: TaskWorktree) -> WorktreeChangeSet:
        tracked_patch = self._git_bytes(
            worktree.path, "diff", "--binary", worktree.base_commit, "--"
        )
        untracked_paths = self._untracked_paths(worktree.path)
        untracked_patches = [self._untracked_patch(worktree.path, path) for path in untracked_paths]
        return WorktreeChangeSet(
            tracked_patch=tracked_patch,
            untracked_patch=b"".join(untracked_patches),
            untracked_paths=untracked_paths,
        )

    def task_diff(self, worktree: TaskWorktree) -> bytes:
        return self.task_changes(worktree).patch

    def is_dirty(self, path: Path) -> bool:
        return bool(self._git(path, "status", "--porcelain").strip())

    def apply_diff(self, path: Path, diff: bytes) -> None:
        if not diff:
            return
        self._run(path, ("apply", "--whitespace=nowarn", "-"), input_data=diff)

    def _git(self, cwd: Path, *args: str) -> str:
        return self._run(cwd, args).decode("utf-8", errors="replace")

    def _git_bytes(self, cwd: Path, *args: str) -> bytes:
        return self._run(cwd, args)

    def _run(
        self,
        cwd: Path,
        args: tuple[str, ...],
        *,
        input_data: bytes | None = None,
        accepted_returncodes: tuple[int, ...] = (0,),
    ) -> bytes:
        try:
            completed = subprocess.run(
                ("git", *args),
                cwd=cwd,
                input=input_data,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(f"Git command timed out after {self._timeout_seconds:g}s") from exc
        except OSError as exc:
            raise WorktreeError("Git is unavailable") from exc
        if completed.returncode not in accepted_returncodes:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(message or "Git command failed")
        return completed.stdout

    def _untracked_paths(self, path: Path) -> tuple[Path, ...]:
        output = self._git_bytes(path, "ls-files", "--others", "--exclude-standard", "-z")
        paths: list[Path] = []
        for raw_path in output.split(b"\0"):
            if not raw_path:
                continue
            relative_path = Path(os.fsdecode(raw_path))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise WorktreeError("Git returned an unsafe untracked path")
            paths.append(relative_path)
        return tuple(sorted(paths, key=lambda item: os.fsencode(item.as_posix())))

    def _untracked_patch(self, worktree_path: Path, relative_path: Path) -> bytes:
        return self._run(
            worktree_path,
            ("diff", "--no-index", "--binary", "--", "/dev/null", relative_path.as_posix()),
            accepted_returncodes=(0, 1),
        )


class WorktreeManager:
    """Creates per-task detached worktrees outside the user's foreground checkout."""

    def __init__(
        self,
        repository_root: Path,
        worktree_root: Path,
        *,
        git_timeout_seconds: float = 30.0,
    ) -> None:
        self._git = GitAdapter(git_timeout_seconds)
        self._repository_root = self._git.repository_root(repository_root)
        self._worktree_root = worktree_root.resolve()
        try:
            self._worktree_root.relative_to(self._repository_root)
        except ValueError:
            pass
        else:
            raise WorktreeError("Task worktree root must be outside the source repository")
        self._git_timeout_seconds = git_timeout_seconds

    def create(self, task_id: str, *, include_tracked_diff: bool = False) -> TaskWorktree:
        self._validate_task_id(task_id)
        path = (self._worktree_root / task_id).resolve()
        if path.parent != self._worktree_root:
            raise WorktreeError("Task worktree path escaped its managed root")
        if path.exists():
            raise WorktreeError(f"Task worktree already exists: {task_id}")

        base_commit = self._git.head_commit(self._repository_root)
        task_worktree = TaskWorktree.create(
            task_id,
            self._repository_root,
            path,
            base_commit,
            self._git.current_branch(self._repository_root),
        )
        self._run_worktree_add(task_worktree)
        try:
            if include_tracked_diff:
                self._git.apply_diff(path, self._git.tracked_diff(self._repository_root))
        except Exception:
            self._remove_path(path, force=True)
            raise
        return task_worktree

    def diff(self, task_worktree: TaskWorktree) -> bytes:
        return self.changes(task_worktree).patch

    def changes(self, task_worktree: TaskWorktree) -> WorktreeChangeSet:
        self._assert_managed(task_worktree)
        return self._git.task_changes(task_worktree)

    def discard(self, task_worktree: TaskWorktree, *, force: bool = False) -> None:
        self._assert_managed(task_worktree)
        if self._git.is_dirty(task_worktree.path) and not force:
            raise WorktreeError("Task worktree has unreviewed changes; force is required")
        self._remove_path(task_worktree.path, force=force)

    def _run_worktree_add(self, task_worktree: TaskWorktree) -> None:
        self._worktree_root.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                (
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(task_worktree.path),
                    task_worktree.base_commit,
                ),
                cwd=self._repository_root,
                capture_output=True,
                check=False,
                timeout=self._git_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                f"Git worktree creation timed out after {self._git_timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise WorktreeError("Git is unavailable") from exc
        if completed.returncode:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(message or "Unable to create task worktree")

    def _remove_path(self, path: Path, *, force: bool) -> None:
        args = ["git", "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        try:
            completed = subprocess.run(
                args,
                cwd=self._repository_root,
                capture_output=True,
                check=False,
                timeout=self._git_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorktreeError(
                f"Git worktree removal timed out after {self._git_timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise WorktreeError("Git is unavailable") from exc
        if completed.returncode:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(message or "Unable to remove task worktree")

    def _assert_managed(self, task_worktree: TaskWorktree) -> None:
        if task_worktree.repository_root != self._repository_root:
            raise WorktreeError("Task worktree belongs to another repository")
        if task_worktree.path.resolve().parent != self._worktree_root:
            raise WorktreeError("Task worktree is outside the managed root")

    def _validate_task_id(self, task_id: str) -> None:
        if not _TASK_ID.fullmatch(task_id):
            raise WorktreeError("Task id must be a short safe identifier")
