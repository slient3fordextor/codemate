import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.graph.models import NodeState, NodeType
from app.agent.persistence import PersistenceError, SQLiteAgentStore, default_agent_state_path
from app.agent.task_state import TaskRecord, TaskState
from app.agent.worktree import GitAdapter, WorktreeError, WorktreeManager


def records(workspace: Path) -> list[TaskRecord]:
    path = default_agent_state_path(workspace)
    if not path.exists():
        return []
    store = SQLiteAgentStore(path)
    return [
        record
        for identity in store.task_ids()
        if (record := store.load_task(identity)).repository_root == workspace.resolve()
    ]


def size(path: Path) -> int:
    return (
        sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file() and not item.is_symlink()
        )
        if path.is_dir()
        else 0
    )


def task_payload(record: TaskRecord) -> dict[str, Any]:
    return {**asdict(record), "state": record.state.value, "bytes": size(record.worktree_path)}


def inspect_task(workspace: Path, task_id: str, worktree_root: Path) -> dict[str, Any]:
    from app.cli.main import _task_worktree

    store = SQLiteAgentStore(default_agent_state_path(workspace))
    record = store.load_task(task_id)
    if record.repository_root != workspace.resolve():
        raise WorktreeError("Task belongs to a different workspace")
    manager = WorktreeManager(workspace, worktree_root)
    diff = None
    if record.worktree_path.is_dir():
        task = manager.recover(_task_worktree(record))
        diff = manager.diff(task).decode("utf-8", errors="replace")
    graph = store.load_graph(record.graph_id) if record.graph_id else None
    with store._connect() as db:
        ledger = db.execute("SELECT * FROM task_budgets WHERE task_id=?", (task_id,)).fetchone()
        runs = db.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY started_at DESC", (task_id,)
        ).fetchall()
    budget = dict(ledger) if ledger else None
    if budget:
        budget.pop("messages")
    started: dict[str, datetime] = {}
    timings = []
    for event in graph.events if graph else ():
        if event.node_id and event.state is NodeState.RUNNING:
            started[event.node_id] = event.at
        elif event.node_id in started and event.state and event.state.terminal:
            timings.append(
                {
                    "node_id": event.node_id,
                    "state": event.state.value,
                    "duration_ms": round(
                        (event.at - started.pop(event.node_id)).total_seconds() * 1000
                    ),
                }
            )
    return {
        **task_payload(record),
        "diff": diff,
        "worktree_available": diff is not None,
        "evidence": [asdict(node) for node in graph.nodes] if graph else [],
        "model_rounds": sum(node.type is NodeType.MODEL for node in graph.nodes) if graph else 0,
        "node_timings": timings,
        "budget": budget,
        "runs": [dict(row) for row in runs],
    }


def doctor(workspace: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "git": bool(shutil.which("git")),
        "bubblewrap": False,
        "desktop": bool(
            shutil.which("xdotool")
            and os.environ.get("DISPLAY")
            and os.environ.get("XDG_SESSION_TYPE") != "wayland"
        ),
    }
    if shutil.which("bwrap"):
        try:
            probe = subprocess.run(
                ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "--", "/bin/true"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            checks["bubblewrap"] = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    checks["workspace"] = workspace.is_dir()
    checks["free_bytes"] = shutil.disk_usage(workspace).free
    checks["retained_bytes"] = sum(size(record.worktree_path) for record in records(workspace))
    checks["capacity_warning"] = checks["retained_bytes"] > checks["free_bytes"] // 5
    return checks


def cleanup_safe(record: TaskRecord, workspace: Path, worktree_root: Path) -> bool:
    from app.cli.main import _task_worktree

    if record.state is not TaskState.DELIVERED or not record.validated_patch_digest:
        return False
    manager = WorktreeManager(workspace, worktree_root)
    try:
        task = manager.recover(_task_worktree(record))
        if GitAdapter().ignored_paths(task.path):
            return False
        return manager.changes(task).patch_digest == record.validated_patch_digest
    except (OSError, ValueError, WorktreeError):
        return False


def gc_candidates(workspace: Path, days: int, worktree_root: Path) -> list[TaskRecord]:
    now = datetime.now(UTC)
    return [
        record
        for record in records(workspace)
        if (now - record.updated_at).days >= days and cleanup_safe(record, workspace, worktree_root)
    ]


def backup(workspace: Path, destination: Path) -> dict[str, str]:
    source = default_agent_state_path(workspace)
    if not source.is_file():
        raise PersistenceError("Agent database does not exist")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as current:
            with sqlite3.connect(destination) as target:
                current.backup(target)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"backup": str(destination)}


def output(payload: Any) -> None:
    print(json.dumps(payload, default=str, ensure_ascii=False))
