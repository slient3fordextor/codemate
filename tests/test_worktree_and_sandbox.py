import subprocess
from pathlib import Path

import pytest

from app.agent.sandbox import SandboxPolicy, SandboxPolicyError
from app.agent.worktree import GitAdapter, WorktreeError, WorktreeManager


def _git(path: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=path, check=True, stdout=subprocess.PIPE)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "codemate@example.test")
    _git(repository, "config", "user.name", "CodeMate Test")
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "initial")
    return repository


def test_task_worktree_isolated_and_delivers_a_diff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")

    task = manager.create("task-1")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")

    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert b"-value = 1" in manager.diff(task)
    with pytest.raises(WorktreeError):
        manager.discard(task)
    manager.discard(task, force=True)
    assert not task.path.exists()


def test_task_change_set_includes_untracked_text_binary_and_empty_files(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("new-files")
    (task.path / "new.py").write_text("answer = 42\n", encoding="utf-8")
    (task.path / "binary.dat").write_bytes(b"\x00\x01\x02\xff")
    (task.path / "empty.txt").touch()

    changes = manager.changes(task)

    assert changes.untracked_paths == (
        Path("binary.dat"),
        Path("empty.txt"),
        Path("new.py"),
    )
    assert b"new.py" in changes.patch
    assert b"binary.dat" in changes.patch
    assert changes.dirty
    manager.discard(task, force=True)


def test_task_delivery_applies_reviewed_changes_and_empty_files(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("delivery")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")
    (task.path / "empty.txt").touch()
    patch_digest = manager.changes(task).patch_digest

    result = manager.deliver(
        task,
        repository,
        expected_patch_digest=patch_digest,
    )

    assert result.changed_paths == (Path("app.py"), Path("empty.txt"))
    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (repository / "empty.txt").read_bytes() == b""
    manager.discard(task, force=True)


def test_task_delivery_rejects_overlapping_source_changes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("conflict")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")
    (repository / "app.py").write_text("value = 3\n", encoding="utf-8")
    patch_digest = manager.changes(task).patch_digest

    with pytest.raises(WorktreeError, match="overlapping"):
        manager.deliver(
            task,
            repository,
            expected_patch_digest=patch_digest,
        )

    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 3\n"
    manager.discard(task, force=True)


def test_task_delivery_rejects_changes_after_validation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("stale-validation")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")
    validated_digest = manager.changes(task).patch_digest
    (task.path / "app.py").write_text("value = 3\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="last successful validation"):
        manager.deliver(
            task,
            repository,
            expected_patch_digest=validated_digest,
        )

    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    manager.discard(task, force=True)


def test_delivered_task_can_be_rolled_back(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("rollback")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")
    (task.path / "empty.txt").touch()
    patch_digest = manager.changes(task).patch_digest
    manager.deliver(task, repository, expected_patch_digest=patch_digest)

    result = manager.rollback(
        task,
        repository,
        expected_patch_digest=patch_digest,
    )

    assert result.changed_paths == (Path("app.py"), Path("empty.txt"))
    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (repository / "empty.txt").exists()
    manager.discard(task, force=True)


def test_task_rollback_rejects_overlapping_changes_after_delivery(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(repository, tmp_path / "tasks")
    task = manager.create("rollback-conflict")
    (task.path / "app.py").write_text("value = 2\n", encoding="utf-8")
    patch_digest = manager.changes(task).patch_digest
    manager.deliver(task, repository, expected_patch_digest=patch_digest)
    (repository / "app.py").write_text("value = 3\n", encoding="utf-8")

    with pytest.raises(WorktreeError):
        manager.rollback(
            task,
            repository,
            expected_patch_digest=patch_digest,
        )

    assert (repository / "app.py").read_text(encoding="utf-8") == "value = 3\n"
    manager.discard(task, force=True)


def test_tracked_diff_is_only_included_when_requested(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "app.py").write_text("value = 3\n", encoding="utf-8")
    manager = WorktreeManager(repository, tmp_path / "tasks")

    clean_task = manager.create("clean")
    changed_task = manager.create("changed", include_tracked_diff=True)

    assert (clean_task.path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (changed_task.path / "app.py").read_text(encoding="utf-8") == "value = 3\n"


def test_sandbox_policy_rejects_worktree_escapes_and_git_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = SandboxPolicy(workspace, tmp_path / "temporary")

    assert policy.resolve_workspace_path(Path("src/main.py")) == workspace / "src/main.py"
    with pytest.raises(SandboxPolicyError):
        policy.resolve_workspace_path(Path("../secret"))
    with pytest.raises(SandboxPolicyError):
        policy.resolve_workspace_path(Path(".git/config"))
    assert not policy.process_isolation_enforced
    with pytest.raises(SandboxPolicyError, match="Sandbox Executor"):
        policy.require_process_isolation()


def test_worktree_root_cannot_be_inside_source_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(WorktreeError, match="outside"):
        WorktreeManager(repository, repository / ".codemate-tasks")


def test_git_adapter_reports_timeout_as_controlled_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    adapter = GitAdapter(timeout_seconds=0.01)

    with pytest.raises(WorktreeError, match="timed out"):
        adapter.repository_root(tmp_path)
