import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.adapters.models import build_model_adapter
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
from app.agent.sandbox import BubblewrapExecutor, SandboxPolicy, SandboxPolicyError
from app.agent.tools import (
    ControlledToolExecutor,
    PermissionMode,
    ReadonlyToolExecutor,
    WorkspaceEditor,
)
from app.agent.worktree import TaskWorktree, WorktreeError, WorktreeManager
from app.core.config import get_settings
from app.services.context_budget import ContextBudgetPlanner
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
    run_parser.add_argument("--task-id", help="stable task id for writable runs")
    run_parser.add_argument(
        "--approve-interrupted-command",
        action="store_true",
        help="allow commands after an interrupted command whose outcome is unknown",
    )

    deliver_parser = subparsers.add_parser("deliver", help="apply a reviewed task diff")
    deliver_parser.add_argument("task_id")
    deliver_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    deliver_parser.add_argument("--json", action="store_true", dest="json_output")

    discard_parser = subparsers.add_parser("discard", help="discard a retained task worktree")
    discard_parser.add_argument("task_id")
    discard_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    discard_parser.add_argument("--force", action="store_true")
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
            )
        )
    if args.command == "deliver":
        return _deliver_task(args.task_id, args.workspace, json_output=args.json_output)
    if args.command == "discard":
        return _discard_task(args.task_id, args.workspace, force=args.force)
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
) -> int:
    settings = get_settings()
    resolved_workspace = workspace.expanduser().resolve()
    task_store: SQLiteAgentStore | None = None
    task_record: TaskRecord | None = None
    task_worktree: TaskWorktree | None = None
    worktree_manager: WorktreeManager | None = None
    interrupted_command = False
    try:
        tool_workspace = resolved_workspace
        if mode is not PermissionMode.READONLY:
            effective_task_id = task_id or f"task-{uuid4().hex[:16]}"
            task_store = SQLiteAgentStore(default_agent_state_path(resolved_workspace))
            worktree_manager = WorktreeManager(
                resolved_workspace,
                _worktree_root(resolved_workspace),
            )
            try:
                task_record = task_store.load_task(effective_task_id)
            except PersistenceError:
                task_worktree = worktree_manager.create(effective_task_id)
                task_record = TaskRecord.create(
                    effective_task_id,
                    objective,
                    task_worktree.repository_root,
                    task_worktree.path,
                    task_worktree.base_commit,
                    task_worktree.base_branch,
                )
                task_store.save_task(task_record)
                task_record = task_record.transition(TaskState.PREPARING)
                task_store.save_task(task_record)
            else:
                task_record, interrupted_command = _prepare_resumed_task(task_store, task_record)
                task_worktree = worktree_manager.recover(_task_worktree(task_record))
            tool_workspace = task_worktree.path

        readonly_tools = ReadonlyToolExecutor(
            tool_workspace,
            ignored_names=settings.workspace.ignored_names,
            max_file_bytes=settings.workspace.max_file_bytes,
            max_tree_entries=settings.workspace.max_tree_entries,
            max_output_bytes=max(
                4_000,
                min(100_000, settings.model.context_window * 2),
            ),
        )
        if mode is PermissionMode.READONLY:
            tools: ReadonlyToolExecutor | ControlledToolExecutor = readonly_tools
        else:
            if task_worktree is None:
                raise RuntimeError("Writable task worktree was not initialized")
            temporary_root = Path(
                tempfile.mkdtemp(prefix=f"codemate-{task_worktree.task_id}-")
            ).resolve()
            policy = SandboxPolicy(tool_workspace, temporary_root)
            tools = ControlledToolExecutor(
                readonly_tools,
                WorkspaceEditor(policy, max_file_bytes=settings.workspace.max_file_bytes),
                BubblewrapExecutor(policy),
                mode,
                workspace_dirty=(
                    worktree_manager.changes(task_worktree).dirty
                    if worktree_manager is not None
                    else False
                ),
                require_command_approval=(interrupted_command and not approve_interrupted_command),
            )
            if task_record is None or task_store is None:
                raise RuntimeError("Writable task state was not initialized")
            if task_record.state is TaskState.PREPARING:
                task_record = task_record.transition(TaskState.ACTIVE)
                task_store.save_task(task_record)
    except (ValueError, WorktreeError, SandboxPolicyError) as exc:
        if task_record is not None and task_store is not None:
            task_record = task_record.transition(TaskState.FAILED, error=str(exc))
            task_store.save_task(task_record)
        print(str(exc), file=sys.stderr)
        return 1
    runner = ReadonlyAgentRunner(
        build_model_adapter(settings.model),
        settings.model.name,
        tools,
        context_budget_planner=ContextBudgetPlanner(
            build_token_counter(settings.model.provider, settings.model.name),
            settings.model.context_window,
        ),
    )

    def progress(event: AgentProgressEvent) -> None:
        if json_output:
            return
        if event.type == "model.started":
            print(f"[model] round {event.data['round']}", file=sys.stderr)
        elif event.type == "tool.completed":
            cached = " (cached)" if event.data.get("cached") else ""
            print(f"[tool] {event.data['tool']}{cached}", file=sys.stderr)
        elif event.type == "model.invalid_decision":
            print(f"[model] invalid decision: {event.data['error']}", file=sys.stderr)

    async def approve(next_round: int) -> bool:
        if not interactive:
            return False
        answer = await asyncio.to_thread(
            input,
            f"ReAct round {next_round} exceeds the automatic limit. Continue once? [y/N] ",
        )
        return answer.strip().casefold() in {"y", "yes"}

    async def approve_tool(request: ToolApprovalRequest) -> bool:
        if not interactive:
            return False
        print(f"\n[approval] {request.tool}", file=sys.stderr)
        print(request.preview, file=sys.stderr)
        answer = await asyncio.to_thread(input, "Approve this action once? [y/N] ")
        return answer.strip().casefold() in {"y", "yes"}

    def checkpoint_graph(graph: Any) -> None:
        nonlocal task_record
        if task_store is None or task_record is None:
            return
        task_store.append_graph(graph)
        if task_record.graph_id != graph.graph_id:
            task_record = task_record.checkpoint(graph.graph_id)
            task_store.save_task(task_record)

    result = await runner.run(
        objective,
        on_progress=progress,
        approve_round=approve if interactive else None,
        approve_tool=approve_tool if interactive and mode is not PermissionMode.READONLY else None,
        checkpoint=checkpoint_graph if task_store is not None else None,
    )
    extra: dict[str, Any] = {}
    if task_record is not None and task_store is not None and task_worktree is not None:
        task_store.append_graph(result.graph)
        if result.status is AgentRunStatus.COMPLETED:
            task_record = task_record.transition(
                TaskState.COMPLETED,
                graph_id=result.graph.graph_id,
            )
        elif result.status is AgentRunStatus.WAITING_APPROVAL:
            task_record = task_record.transition(
                TaskState.WAITING_APPROVAL,
                graph_id=result.graph.graph_id,
            )
        else:
            task_record = task_record.transition(
                TaskState.FAILED,
                graph_id=result.graph.graph_id,
                error=result.error,
            )
        task_store.save_task(task_record)
        changes = worktree_manager.changes(task_worktree) if worktree_manager else None
        changed_paths = worktree_manager.changed_paths(task_worktree) if worktree_manager else ()
        extra = {
            "task_id": task_record.task_id,
            "task_state": task_record.state.value,
            "worktree": str(task_worktree.path),
            "changed_paths": [path.as_posix() for path in changed_paths],
            "diff": changes.patch.decode("utf-8", errors="replace") if changes else "",
        }
    if json_output:
        print(json.dumps({**_result_payload(result), **extra}, ensure_ascii=False))
    elif result.status is AgentRunStatus.COMPLETED:
        print(result.answer)
        if task_record is not None:
            print(
                f"Task {task_record.task_id} retained for review. "
                f"Run: codemate deliver {task_record.task_id} --workspace {resolved_workspace}"
            )
    else:
        print(result.error or result.status.value, file=sys.stderr)
    return 0 if result.status is AgentRunStatus.COMPLETED else 2


def _deliver_task(task_id: str, workspace: Path, *, json_output: bool) -> int:
    resolved_workspace = workspace.expanduser().resolve()
    try:
        store = SQLiteAgentStore(default_agent_state_path(resolved_workspace))
        record = store.load_task(task_id)
        if record.state not in {TaskState.COMPLETED, TaskState.RETAINED}:
            raise WorktreeError(f"Task is not ready for delivery: {record.state.value}")
        manager = WorktreeManager(resolved_workspace, _worktree_root(resolved_workspace))
        task_worktree = manager.recover(_task_worktree(record))
        delivery = manager.deliver(task_worktree, resolved_workspace)
        record = record.transition(TaskState.DELIVERED)
        store.save_task(record)
    except (PersistenceError, ValueError, WorktreeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = {
        "task_id": delivery.task_id,
        "state": record.state.value,
        "changed_paths": [path.as_posix() for path in delivery.changed_paths],
    }
    print(json.dumps(payload, ensure_ascii=False) if json_output else f"Delivered task {task_id}")
    return 0


def _discard_task(task_id: str, workspace: Path, *, force: bool) -> int:
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
