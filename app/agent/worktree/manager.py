import os
import re
import subprocess
from pathlib import Path

from app.agent.worktree.models import (
    DeliveryResult,
    RollbackResult,
    TaskWorktree,
    WorktreeChangeSet,
    WorktreeError,
)

_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def _git_environment() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


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
        return self._git_bytes(
            repository_root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "HEAD",
        )

    def task_changes(self, worktree: TaskWorktree) -> WorktreeChangeSet:
        tracked_patch = self._git_bytes(
            worktree.path,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            worktree.base_commit,
            "--",
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

    def check_diff(self, path: Path, diff: bytes) -> None:
        if diff:
            self._run(path, ("apply", "--check", "--whitespace=nowarn", "-"), input_data=diff)

    def reverse_diff(self, path: Path, diff: bytes) -> None:
        if diff:
            self._run(path, ("apply", "--reverse", "--whitespace=nowarn", "-"), input_data=diff)

    def check_reverse_diff(self, path: Path, diff: bytes) -> None:
        if diff:
            self._run(
                path,
                ("apply", "--check", "--reverse", "--whitespace=nowarn", "-"),
                input_data=diff,
            )

    def changed_paths_since(self, path: Path, base_commit: str) -> tuple[Path, ...]:
        tracked = self._git_bytes(
            path,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            base_commit,
            "--",
        )
        values = self._safe_paths(tracked)
        return tuple(sorted(set(values) | set(self._untracked_paths(path)), key=self._path_key))

    def task_changed_paths(self, worktree: TaskWorktree) -> tuple[Path, ...]:
        tracked = self._git_bytes(
            worktree.path,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "-z",
            worktree.base_commit,
            "--",
        )
        values = self._safe_paths(tracked)
        return tuple(
            sorted(set(values) | set(self._untracked_paths(worktree.path)), key=self._path_key)
        )

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
                (
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    *args,
                ),
                cwd=cwd,
                input=input_data,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
                env=_git_environment(),
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
        return self._safe_paths(output)

    def _safe_paths(self, output: bytes) -> tuple[Path, ...]:
        paths: list[Path] = []
        for raw_path in output.split(b"\0"):
            if not raw_path:
                continue
            relative_path = Path(os.fsdecode(raw_path))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise WorktreeError("Git returned an unsafe untracked path")
            paths.append(relative_path)
        return tuple(sorted(paths, key=self._path_key))

    @staticmethod
    def _path_key(path: Path) -> bytes:
        return os.fsencode(path.as_posix())

    def _untracked_patch(self, worktree_path: Path, relative_path: Path) -> bytes:
        return self._run(
            worktree_path,
            (
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-index",
                "--binary",
                "--",
                "/dev/null",
                relative_path.as_posix(),
            ),
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

    def changed_paths(self, task_worktree: TaskWorktree) -> tuple[Path, ...]:
        self._assert_managed(task_worktree)
        return self._git.task_changed_paths(task_worktree)

    def recover(self, task_worktree: TaskWorktree) -> TaskWorktree:
        self._assert_managed(task_worktree)
        if not task_worktree.path.is_dir():
            raise WorktreeError("Persisted task worktree no longer exists")
        if self._git.head_commit(task_worktree.path) != task_worktree.base_commit:
            raise WorktreeError("Task worktree HEAD no longer matches its recorded baseline")
        return task_worktree

    def deliver(
        self,
        task_worktree: TaskWorktree,
        target_path: Path,
        *,
        expected_patch_digest: str,
    ) -> DeliveryResult:
        self._assert_managed(task_worktree)
        target_root = self._git.repository_root(target_path)
        if target_root != self._repository_root:
            raise WorktreeError("Task changes can only be delivered to the source repository")
        changes = self._git.task_changes(task_worktree)
        self._require_expected_patch(changes, expected_patch_digest)
        task_paths = self._git.task_changed_paths(task_worktree)
        target_paths = self._git.changed_paths_since(target_root, task_worktree.base_commit)
        conflicts = sorted(set(task_paths) & set(target_paths), key=GitAdapter._path_key)
        if conflicts:
            rendered = ", ".join(path.as_posix() for path in conflicts[:20])
            raise WorktreeError(f"Target workspace changed overlapping files: {rendered}")

        empty_paths = tuple(
            path
            for path in changes.untracked_paths
            if (task_worktree.path / path).stat().st_size == 0
        )
        for relative_path in changes.untracked_paths:
            target = (target_root / relative_path).resolve()
            try:
                target.relative_to(target_root)
            except ValueError as exc:
                raise WorktreeError("Task change path escaped the target workspace") from exc
            if target.exists():
                raise WorktreeError(f"Untracked task path already exists: {relative_path}")
            if not target.parent.is_dir():
                raise WorktreeError(f"Task change parent does not exist: {relative_path}")

        self._git.check_diff(target_root, changes.patch)
        created_empty: list[Path] = []
        patch_applied = False
        try:
            self._git.apply_diff(target_root, changes.patch)
            patch_applied = True
            for relative_path in empty_paths:
                target = target_root / relative_path
                if target.exists():
                    continue
                with target.open("xb") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                created_empty.append(target)
        except Exception:
            for target in created_empty:
                target.unlink(missing_ok=True)
            if patch_applied:
                self._git.reverse_diff(target_root, changes.patch)
            raise
        return DeliveryResult.create(task_worktree.task_id, target_root, task_paths)

    def rollback(
        self,
        task_worktree: TaskWorktree,
        target_path: Path,
        *,
        expected_patch_digest: str,
    ) -> RollbackResult:
        self._assert_managed(task_worktree)
        target_root = self._git.repository_root(target_path)
        if target_root != self._repository_root:
            raise WorktreeError("Task changes can only be rolled back in the source repository")
        changes = self._git.task_changes(task_worktree)
        self._require_expected_patch(changes, expected_patch_digest)
        task_paths = self._git.task_changed_paths(task_worktree)
        empty_paths = tuple(
            path
            for path in changes.untracked_paths
            if (task_worktree.path / path).stat().st_size == 0
        )
        for relative_path in empty_paths:
            target = target_root / relative_path
            if not target.is_file() or target.stat().st_size != 0:
                raise WorktreeError(f"Delivered empty file changed after delivery: {relative_path}")

        self._git.check_reverse_diff(target_root, changes.patch)
        removed_empty: list[Path] = []
        patch_reversed = False
        try:
            self._git.reverse_diff(target_root, changes.patch)
            patch_reversed = True
            for relative_path in empty_paths:
                target = target_root / relative_path
                if target.exists():
                    target.unlink()
                    removed_empty.append(target)
        except Exception:
            for target in removed_empty:
                target.touch(exist_ok=True)
            if patch_reversed:
                self._git.apply_diff(target_root, changes.patch)
            raise
        return RollbackResult.create(task_worktree.task_id, target_root, task_paths)

    def discard(self, task_worktree: TaskWorktree, *, force: bool = False) -> None:
        self._assert_managed(task_worktree)
        if self._git.is_dirty(task_worktree.path) and not force:
            raise WorktreeError("Task worktree has unreviewed changes; force is required")
        self._remove_path(task_worktree.path, force=force)

    @staticmethod
    def _require_expected_patch(
        changes: WorktreeChangeSet,
        expected_patch_digest: str,
    ) -> None:
        if not expected_patch_digest:
            raise WorktreeError("A validated patch digest is required")
        if changes.patch_digest != expected_patch_digest:
            raise WorktreeError("Task worktree changed after its last successful validation")

    def _run_worktree_add(self, task_worktree: TaskWorktree) -> None:
        self._worktree_root.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                (
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
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
                env=_git_environment(),
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
        args = [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "worktree",
            "remove",
        ]
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
                env=_git_environment(),
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
