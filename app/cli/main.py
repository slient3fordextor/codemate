import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.adapters.models import build_model_adapter
from app.adapters.models.base import ModelProviderError
from app.agent import (
    AgentProgressEvent,
    AgentRunResult,
    AgentRunStatus,
    PersistenceError,
    ReadonlyAgentRunner,
    SQLiteAgentStore,
    TaskRecord,
    TaskState,
    ToolApprovalRequest,
    default_agent_state_path,
)
from app.agent.graph import NodeState, NodeType
from app.agent.sandbox import SandboxPolicyError
from app.agent.tasks import AgentTaskService
from app.agent.tools import (
    PermissionMode,
    ReadonlyToolExecutor,
)
from app.agent.worktree import TaskWorktree, WorktreeError, WorktreeManager
from app.core.config import get_settings
from app.core.errors import AppError
from app.services.context_budget import ContextBudgetPlanner
from app.services.model_profiles import ModelProfiles
from app.services.token_counter import build_token_counter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codemate", description="Local coding agent CLI")
    parser.add_argument("--version", action="store_true", help="show the CodeMate version")
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in PermissionMode),
        default="readonly",
        help="interactive execution policy",
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run one non-interactive Agent task")
    run_parser.add_argument("objective", help="task to investigate")
    run_parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in PermissionMode),
        default="readonly",
        help="execution policy",
    )
    run_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    run_parser.add_argument("--json", action="store_true", dest="json_output")
    run_parser.add_argument("--task", dest="task_id", help="stable writable task")
    run_parser.add_argument(
        "--task-id",
        dest="task_id",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    run_parser.add_argument(
        "--deliver",
        action="store_true",
        help="deliver the successfully validated task diff to the source workspace",
    )
    run_parser.add_argument(
        "--resume",
        dest="approve_interrupted_command",
        action="store_true",
        help="allow retry after an interrupted command",
    )
    run_parser.add_argument(
        "--approve-interrupted-command",
        dest="approve_interrupted_command",
        action="store_true",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    deliver_parser = subparsers.add_parser("deliver", help="apply a reviewed task diff")
    deliver_parser.add_argument("task_id")
    deliver_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    deliver_parser.add_argument("--json", action="store_true", dest="json_output")

    rollback_parser = subparsers.add_parser(
        "rollback",
        help="reverse a previously delivered task diff",
    )
    rollback_parser.add_argument("task_id")
    rollback_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    rollback_parser.add_argument("--json", action="store_true", dest="json_output")

    discard_parser = subparsers.add_parser("discard", help="discard a retained task worktree")
    discard_parser.add_argument("task_id")
    discard_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    discard_parser.add_argument("--force", action="store_true")
    task_parser = subparsers.add_parser("tasks", help="inspect retained tasks without a model")
    task_commands = task_parser.add_subparsers(dest="task_command", required=True)
    for command in ("list", "show", "inspect", "gc", "backup"):
        command_parser = task_commands.add_parser(command)
        command_parser.add_argument("--workspace", type=Path, default=Path.cwd())
        if command in {"show", "inspect"}:
            command_parser.add_argument("task_id")
        if command == "gc":
            command_parser.add_argument("--days", type=int, default=30)
            command_parser.add_argument("--apply", action="store_true")
        if command == "backup":
            command_parser.add_argument("destination", type=Path)
    doctor_parser = subparsers.add_parser("doctor", help="check local dependencies without secrets")
    doctor_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.version:
        print(settings.app.version)
        return 0
    if args.command == "run":
        return asyncio.run(
            _run_once(
                args.objective,
                args.workspace,
                mode=PermissionMode(args.mode),
                json_output=args.json_output,
                interactive=False,
                task_id=args.task_id,
                approve_interrupted_command=args.approve_interrupted_command,
                deliver_requested=args.deliver,
            )
        )
    if args.command == "deliver":
        return _deliver_task(args.task_id, args.workspace, json_output=args.json_output)
    if args.command == "rollback":
        return _rollback_task(args.task_id, args.workspace, json_output=args.json_output)
    if args.command == "discard":
        return _discard_task(args.task_id, args.workspace, force=args.force)
    if args.command in {"tasks", "doctor"}:
        from app.cli import diagnostics

        workspace = args.workspace.expanduser().resolve()
        payload: Any
        try:
            if args.command == "doctor":
                payload = diagnostics.doctor(workspace)
            elif args.task_command == "list":
                payload = [
                    diagnostics.task_payload(record) for record in diagnostics.records(workspace)
                ]
            elif args.task_command in {"show", "inspect"}:
                if args.task_command == "inspect":
                    payload = diagnostics.inspect_task(
                        workspace, args.task_id, _worktree_root(workspace)
                    )
                else:
                    record = SQLiteAgentStore(default_agent_state_path(workspace)).load_task(
                        args.task_id
                    )
                    if record.repository_root != workspace:
                        raise WorktreeError("Task belongs to a different workspace")
                    payload = diagnostics.task_payload(record)
            elif args.task_command == "backup":
                payload = diagnostics.backup(workspace, args.destination.expanduser().resolve())
            else:
                if args.days < 0:
                    raise ValueError("Retention days cannot be negative")
                candidates = diagnostics.gc_candidates(
                    workspace, args.days, _worktree_root(workspace)
                )
                payload = {
                    "preview": not args.apply,
                    "candidates": [record.task_id for record in candidates],
                }
                if args.apply:
                    for record in candidates:

                        def discard_delivered(record: TaskRecord = record) -> int:
                            fresh = SQLiteAgentStore(default_agent_state_path(workspace)).load_task(
                                record.task_id
                            )
                            if not diagnostics.cleanup_safe(
                                fresh, workspace, _worktree_root(workspace)
                            ):
                                raise WorktreeError(
                                    "Task changed or is not delivered; cleanup refused"
                                )
                            return _discard_task_locked(record.task_id, workspace, force=True)

                        if _with_task_lock(record.task_id, workspace, "gc", discard_delivered) != 0:
                            return 1
            diagnostics.output(payload)
            return 0
        except (OSError, ValueError, PersistenceError, WorktreeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.command is not None:
        parser.error(f"Unsupported command: {args.command}")
    return asyncio.run(_interactive(args.workspace, PermissionMode(args.mode)))


async def _interactive(workspace: Path, mode: PermissionMode) -> int:
    print(f"CodeMate {mode.value} agent | workspace: {workspace.resolve()}")
    while True:
        try:
            objective = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        objective = objective.strip()
        if not objective:
            continue
        if objective in {"/exit", "/quit"}:
            return 0
        await _run_once(
            objective,
            workspace,
            mode=mode,
            json_output=False,
            interactive=True,
            deliver_requested=False,
        )


async def _run_once(
    objective: str,
    workspace: Path,
    *,
    mode: PermissionMode,
    json_output: bool,
    interactive: bool,
    task_id: str | None = None,
    approve_interrupted_command: bool = False,
    deliver_requested: bool = False,
) -> int:
    async def execute(effective_id: str | None, temporary_directory: Path | None = None) -> int:
        return await _run_once_locked(
            objective,
            workspace,
            mode=mode,
            json_output=json_output,
            interactive=interactive,
            task_id=effective_id,
            temporary_directory=temporary_directory,
            approve_interrupted_command=approve_interrupted_command,
            deliver_requested=deliver_requested,
        )

    if mode is PermissionMode.READONLY:
        return await execute(task_id)
    effective_task_id = task_id or uuid4().hex
    try:
        resolved = workspace.expanduser().resolve()
        manager = WorktreeManager(resolved, _worktree_root(resolved))
        with manager.task_lock(effective_task_id, action="run"):
            with tempfile.TemporaryDirectory(prefix="codemate-sandbox-") as temporary:
                return await execute(effective_task_id, Path(temporary))
    except (ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


async def _run_once_locked(
    objective: str,
    workspace: Path,
    *,
    mode: PermissionMode,
    json_output: bool,
    interactive: bool,
    task_id: str | None = None,
    approve_interrupted_command: bool = False,
    deliver_requested: bool = False,
    temporary_directory: Path | None = None,
) -> int:
    settings = get_settings()
    resolved = workspace.expanduser().resolve()
    if deliver_requested and mode is PermissionMode.READONLY:
        print("--deliver requires confirm or agent mode", file=sys.stderr)
        return 1

    def progress(event: AgentProgressEvent) -> None:
        if json_output:
            return
        if event.type == "model.started":
            print(f"[model] round {event.data.get('round')}", file=sys.stderr)
        elif event.type == "tool.completed":
            print(f"[tool] {event.data.get('tool')}", file=sys.stderr)

    async def approve(next_round: int) -> bool:
        answer = await asyncio.to_thread(input, f"Continue round {next_round} once? [y/N] ")
        return answer.strip().casefold() in {"y", "yes"}

    async def approve_tool(request: ToolApprovalRequest) -> bool:
        print(request.preview, file=sys.stderr)
        answer = await asyncio.to_thread(input, "Approve this action once? [y/N] ")
        return answer.strip().casefold() in {"y", "yes"}

    try:
        if mode is PermissionMode.READONLY:
            model = build_model_adapter(settings.model)
            tools = ReadonlyToolExecutor(
                resolved,
                ignored_names=settings.workspace.ignored_names,
                max_file_bytes=settings.workspace.max_file_bytes,
                max_tree_entries=settings.workspace.max_tree_entries,
                max_output_bytes=max(4000, min(100000, settings.model.context_window * 2)),
            )
            runner = ReadonlyAgentRunner(
                model,
                settings.model.name,
                tools,
                context_budget_planner=ContextBudgetPlanner(
                    build_token_counter(settings.model.provider, settings.model.name),
                    settings.model.context_window,
                ),
            )
            result = await runner.run(
                objective, on_progress=progress, approve_round=approve if interactive else None
            )
            payload = _result_payload(result)
        else:
            if temporary_directory is None or task_id is None:
                raise ValueError("Writable task requires a managed temporary directory and ID")
            service = AgentTaskService(resolved, settings, root=_worktree_root(resolved))
            config = settings.model
            with service.store._connect() as db:
                spec = db.execute(
                    "SELECT profile_id FROM task_specs WHERE task_id=?", (task_id,)
                ).fetchone()
            if spec and spec[0]:
                config = ModelProfiles(service.store).get(spec[0])[0]
            model = build_model_adapter(config)
            outcome = await service.run_locked(
                task_id,
                objective,
                mode,
                temporary_directory,
                model,
                config,
                progress=progress,
                approve_tool=approve_tool if interactive else None,
                approve_round=approve if interactive else None,
                approve_interrupted_command=approve_interrupted_command,
                max_rounds=settings.agent_max_rounds,
                max_tokens=settings.agent_max_tokens,
                max_replans=settings.agent_max_replans,
            )
            result = outcome.result
            payload = outcome.payload()
            should_deliver = deliver_requested
            if (
                result.status is AgentRunStatus.COMPLETED
                and interactive
                and outcome.diff
                and not should_deliver
            ):
                print(outcome.diff, file=sys.stderr)
                answer = await asyncio.to_thread(input, "Deliver these validated changes? [y/N] ")
                should_deliver = answer.strip().casefold() in {"y", "yes"}
            if result.status is AgentRunStatus.COMPLETED and should_deliver:
                delivery = service.deliver_locked(task_id, outcome.record.validated_patch_digest)
                payload.update({"delivered": True, "task_state": delivery["state"]})
    except (
        ValueError,
        WorktreeError,
        SandboxPolicyError,
        PersistenceError,
        ModelProviderError,
        AppError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        if json_output:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "task_id": task_id,
                        "error": {
                            "code": getattr(exc, "code", "TASK_REJECTED"),
                            "message": str(exc),
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return 1
    if json_output:
        print(json.dumps(payload, ensure_ascii=False))
    elif result.status is AgentRunStatus.COMPLETED:
        print(result.answer)
        if mode is not PermissionMode.READONLY:
            print(f"Task {task_id}: {payload['task_state']}")
    else:
        print(result.error or result.status.value, file=sys.stderr)
    return 0 if result.status is AgentRunStatus.COMPLETED else 2


def _deliver_task(task_id: str, workspace: Path, *, json_output: bool) -> int:
    return _with_task_lock(
        task_id,
        workspace,
        "deliver",
        lambda: _deliver_task_locked(task_id, workspace, json_output=json_output),
    )


def _deliver_task_locked(task_id: str, workspace: Path, *, json_output: bool) -> int:
    try:
        resolved = workspace.expanduser().resolve()
        payload = AgentTaskService(
            resolved, get_settings(), root=_worktree_root(resolved)
        ).deliver_locked(task_id)
    except (PersistenceError, ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False) if json_output else f"Delivered task {task_id}")
    return 0


def _rollback_task(task_id: str, workspace: Path, *, json_output: bool) -> int:
    return _with_task_lock(
        task_id,
        workspace,
        "rollback",
        lambda: _rollback_task_locked(task_id, workspace, json_output=json_output),
    )


def _rollback_task_locked(task_id: str, workspace: Path, *, json_output: bool) -> int:
    try:
        resolved = workspace.expanduser().resolve()
        payload = AgentTaskService(
            resolved, get_settings(), root=_worktree_root(resolved)
        ).rollback_locked(task_id)
    except (PersistenceError, ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False) if json_output else f"Rolled back task {task_id}")
    return 0


def _discard_task(task_id: str, workspace: Path, *, force: bool) -> int:
    return _with_task_lock(
        task_id,
        workspace,
        "discard",
        lambda: _discard_task_locked(task_id, workspace, force=force),
    )


def _discard_task_locked(task_id: str, workspace: Path, *, force: bool) -> int:
    resolved_workspace = workspace.expanduser().resolve()
    try:
        store = SQLiteAgentStore(default_agent_state_path(resolved_workspace))
        record = store.load_task(task_id)
        if record.state in {
            TaskState.CREATED,
            TaskState.PREPARING,
            TaskState.ACTIVE,
        }:
            raise WorktreeError(f"Task is still active: {record.state.value}")
        if record.state is TaskState.WAITING_APPROVAL:
            record = record.transition(TaskState.CANCELLED)
            store.save_task(record)
        manager = WorktreeManager(resolved_workspace, _worktree_root(resolved_workspace))
        manager.discard(manager.recover(_task_worktree(record)), force=force)
        record = record.transition(TaskState.DISCARDED)
        store.save_task(record)
    except (PersistenceError, ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Discarded task {task_id}")
    return 0


def _with_task_lock(
    task_id: str, workspace: Path, action: str, operation: Callable[[], int]
) -> int:
    try:
        resolved = workspace.expanduser().resolve()
        manager = WorktreeManager(resolved, _worktree_root(resolved))
        with manager.task_lock(task_id, action=action):
            return operation()
    except (ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _worktree_root(workspace: Path) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "codemate-worktrees" / digest


def _prepare_resumed_task(
    store: SQLiteAgentStore,
    record: TaskRecord,
) -> tuple[TaskRecord, bool]:
    interrupted_command = False
    if record.state in {TaskState.DELIVERED, TaskState.DISCARDED}:
        raise WorktreeError(f"Task cannot be resumed after {record.state.value}")
    if record.state is TaskState.ACTIVE:
        if record.graph_id is not None:
            graph = store.load_graph(record.graph_id)
            interrupted_command = any(
                node.type is NodeType.COMMAND
                and node.state in {NodeState.RUNNING, NodeState.INTERRUPTED}
                for node in graph.nodes
            )
            graph.interrupt_running()
            store.append_graph(graph)
        record = record.transition(TaskState.FAILED, error="Previous process was interrupted")
        store.save_task(record)
    if record.state is TaskState.CREATED:
        record = record.transition(TaskState.PREPARING)
        store.save_task(record)
    if record.state is TaskState.COMPLETED:
        record = record.transition(TaskState.RETAINED)
        store.save_task(record)
    if record.state is TaskState.CANCELLED:
        record = record.transition(TaskState.RETAINED)
        store.save_task(record)
    if record.state in {
        TaskState.PREPARING,
        TaskState.WAITING_APPROVAL,
        TaskState.FAILED,
        TaskState.ROLLED_BACK,
        TaskState.RETAINED,
    }:
        record = record.transition(TaskState.ACTIVE)
        store.save_task(record)
    if record.state is not TaskState.ACTIVE:
        raise WorktreeError(f"Task cannot be resumed from {record.state.value}")
    return record, interrupted_command


def _task_worktree(record: TaskRecord) -> TaskWorktree:
    return TaskWorktree(
        task_id=record.task_id,
        repository_root=record.repository_root,
        path=record.worktree_path,
        base_commit=record.base_commit,
        base_branch=record.base_branch,
        created_at=record.created_at,
    )


def _result_payload(result: AgentRunResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "answer": result.answer,
        "error": result.error,
        "graph_id": result.graph.graph_id,
        "rounds": result.rounds,
        "tool_calls": result.tool_calls,
        "tokens_used": result.tokens_used,
        "events": len(result.graph.events),
    }


if __name__ == "__main__":
    raise SystemExit(main())
