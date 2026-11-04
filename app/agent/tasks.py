"""Shared task lifecycle for CLI and HTTP; callers hold the task lease."""

import asyncio
import hashlib
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from app.adapters.models.base import ModelAdapter
from app.agent.budget import TaskBudget
from app.agent.graph import ExecutionGraph, NodeState, NodeType
from app.agent.graph.models import NodePolicy
from app.agent.persistence import SQLiteAgentStore, default_agent_state_path
from app.agent.runner import (
    AgentRunResult,
    AgentRunStatus,
    ApprovalCallback,
    ProgressCallback,
    ReadonlyAgentRunner,
    ToolApprovalCallback,
)
from app.agent.sandbox import BubblewrapExecutor, SandboxPolicy
from app.agent.task_state import TaskRecord, TaskState
from app.agent.tools import (
    ControlledToolExecutor,
    PermissionMode,
    ReadonlyToolExecutor,
    WorkspaceEditor,
)
from app.agent.worktree import TaskWorktree, WorktreeError, WorktreeManager
from app.core.config import ModelProviderConfig, Settings
from app.services.context_budget import ContextBudgetPlanner
from app.services.token_counter import build_token_counter
from app.services.validation_templates import TEMPLATES


def worktree_root(workspace: Path) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "codemate-worktrees" / digest


def task_worktree(record: TaskRecord) -> TaskWorktree:
    return TaskWorktree(
        record.task_id,
        record.repository_root,
        record.worktree_path,
        record.base_commit,
        record.base_branch,
        record.created_at,
    )


def prepare_resumed_task(store: SQLiteAgentStore, record: TaskRecord) -> tuple[TaskRecord, bool]:
    interrupted = False
    if record.state in {TaskState.DELIVERED, TaskState.DISCARDED}:
        raise WorktreeError(f"Task cannot be resumed after {record.state.value}")
    if record.graph_id:
        graph = store.load_graph(record.graph_id)
        interrupted = any(
            node.type in {NodeType.COMMAND, NodeType.APPLY_PATCH}
            and node.state in {NodeState.RUNNING, NodeState.INTERRUPTED}
            for node in graph.nodes
        )
        graph.interrupt_running()
        store.append_graph(graph)
    if record.state is TaskState.ACTIVE:
        record = record.transition(TaskState.FAILED, error="Previous process was interrupted")
        store.save_task(record)
    if record.state is TaskState.CREATED:
        record = record.transition(TaskState.PREPARING)
    if record.state in {TaskState.COMPLETED, TaskState.CANCELLED}:
        record = record.transition(TaskState.RETAINED)
    if record.state in {
        TaskState.PREPARING,
        TaskState.WAITING_APPROVAL,
        TaskState.FAILED,
        TaskState.ROLLED_BACK,
        TaskState.RETAINED,
    }:
        record = record.transition(TaskState.ACTIVE)
    store.save_task(record)
    return record, interrupted


@dataclass(frozen=True)
class TaskOutcome:
    result: AgentRunResult
    record: TaskRecord
    diff: str
    changed_paths: tuple[Path, ...]
    budget: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.result.status.value,
            "answer": self.result.answer,
            "error": self.result.error,
            "task_id": self.record.task_id,
            "task_state": self.record.state.value,
            "worktree": str(self.record.worktree_path),
            "graph_id": self.result.graph.graph_id,
            "rounds": self.result.rounds,
            "tool_calls": self.result.tool_calls,
            "tokens_used": self.result.tokens_used,
            "events": len(self.result.graph.events),
            "diff": self.diff,
            "changed_paths": [path.as_posix() for path in self.changed_paths],
            "validated_patch_digest": self.record.validated_patch_digest,
            "budget": self.budget,
            "delivered": False,
        }


class AgentTaskService:
    def __init__(self, workspace: Path, settings: Settings, *, root: Path | None = None) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.settings = settings
        self.store = SQLiteAgentStore(default_agent_state_path(self.workspace))
        self.manager = WorktreeManager(self.workspace, root or worktree_root(self.workspace))
        self.current_budget: TaskBudget | None = None

    def get(self, identity: str) -> TaskRecord:
        record = self.store.load_task(identity)
        if record.repository_root != self.workspace:
            raise WorktreeError("Task belongs to another workspace")
        return record

    def create(self, identity: str, objective: str) -> TaskRecord:
        if identity in self.store.task_ids():
            raise WorktreeError("Task already exists")
        worktree = self.manager.create(identity)
        record = TaskRecord.create(
            identity,
            objective,
            self.workspace,
            worktree.path,
            worktree.base_commit,
            worktree.base_branch,
        )
        self.store.save_task(record)
        return record

    async def run_locked(
        self,
        identity: str,
        objective: str,
        mode: PermissionMode,
        temporary: Path,
        model: ModelAdapter,
        config: ModelProviderConfig,
        *,
        progress: ProgressCallback | None = None,
        approve_tool: ToolApprovalCallback | None = None,
        approve_round: ApprovalCallback | None = None,
        approve_interrupted_command: bool = False,
        request_id: str | None = None,
        max_rounds: int = 30,
        max_tokens: int = 120000,
        max_replans: int = 3,
    ) -> TaskOutcome:
        record = (
            self.get(identity)
            if identity in self.store.task_ids()
            else self.create(identity, objective)
        )
        with self.store._transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO task_specs VALUES(?,?,NULL,NULL)", (identity, mode.value)
            )
            spec = db.execute("SELECT mode FROM task_specs WHERE task_id=?", (identity,)).fetchone()
            if spec[0] != mode.value:
                raise WorktreeError("Task permission mode cannot be changed on resume")
        budget = TaskBudget(
            self.store,
            record,
            max_rounds=max_rounds,
            max_tokens=max_tokens,
            max_replans=max_replans,
        )
        self.current_budget = budget
        tools: ReadonlyToolExecutor | ControlledToolExecutor | None = None
        try:
            record, interrupted = prepare_resumed_task(self.store, record)
            worktree = self.manager.recover(task_worktree(record))
            readonly = ReadonlyToolExecutor(
                worktree.path,
                ignored_names=self.settings.workspace.ignored_names,
                max_file_bytes=self.settings.workspace.max_file_bytes,
                max_tree_entries=self.settings.workspace.max_tree_entries,
                max_output_bytes=max(4000, min(100000, config.context_window * 2)),
            )
            if mode is PermissionMode.READONLY:
                tools = readonly
            else:
                policy = SandboxPolicy(worktree.path, temporary)
                tools = ControlledToolExecutor(
                    readonly,
                    WorkspaceEditor(policy, max_file_bytes=self.settings.workspace.max_file_bytes),
                    BubblewrapExecutor(policy),
                    mode,
                    workspace_digest=lambda: self.manager.changes(worktree).patch_digest,
                    workspace_dirty=self.manager.changes(worktree).dirty,
                    require_command_approval=interrupted and not approve_interrupted_command,
                )
            initial = budget.start(mode.value, config.model_dump(exclude={"api_key"}), request_id)
            runner = ReadonlyAgentRunner(
                model,
                config.name,
                tools,
                task_budget=budget,
                context_budget_planner=ContextBudgetPlanner(
                    build_token_counter(config.provider, config.name), config.context_window
                ),
            )
            result = await runner.run(
                objective,
                on_progress=progress,
                approve_tool=approve_tool,
                approve_round=approve_round,
                initial_messages=initial,
            )
            record = self.get(identity)
            if record.state is TaskState.WAITING_APPROVAL:
                record = record.transition(TaskState.ACTIVE)
            changes = self.manager.changes(worktree)
            if result.status is AgentRunStatus.COMPLETED:
                if mode is not PermissionMode.READONLY:
                    validated = tools.validated_patch_digest
                    if validated != changes.patch_digest:
                        result = replace(
                            result,
                            status=AgentRunStatus.NEEDS_EVIDENCE,
                            error="Task worktree changed after validation",
                        )
                    else:
                        record = record.record_validation(validated)
                target = (
                    TaskState.COMPLETED
                    if result.status is AgentRunStatus.COMPLETED
                    else TaskState.FAILED
                )
            else:
                target = (
                    TaskState.WAITING_APPROVAL
                    if result.status is AgentRunStatus.WAITING_APPROVAL
                    else TaskState.FAILED
                )
            record = record.transition(target, graph_id=result.graph.graph_id, error=result.error)
            self.store.save_task(record)
            budget.finish(result.status.value)
            return TaskOutcome(
                result,
                record,
                changes.patch.decode("utf-8", errors="replace"),
                self.manager.changed_paths(worktree),
                budget.snapshot(),
            )
        except BaseException as exc:
            if tools is not None and isinstance(tools, ControlledToolExecutor):
                await asyncio.to_thread(tools.cancel)
            if budget._graph is not None:
                budget._graph.interrupt_running()
                budget.checkpoint(budget._graph, budget._messages)
            record = self.get(identity)
            if record.state in {TaskState.ACTIVE, TaskState.PREPARING, TaskState.WAITING_APPROVAL}:
                target = (
                    TaskState.CANCELLED
                    if isinstance(exc, asyncio.CancelledError)
                    else TaskState.FAILED
                )
                self.store.save_task(record.transition(target, error=type(exc).__name__))
            budget.finish("cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
            raise

    def deliver_locked(self, identity: str, expected_digest: str | None = None) -> dict[str, Any]:
        record = self.get(identity)
        if record.state not in {TaskState.COMPLETED, TaskState.RETAINED, TaskState.ROLLED_BACK}:
            raise WorktreeError(f"Task is not ready for delivery: {record.state.value}")
        if not record.validated_patch_digest:
            raise WorktreeError("Task has no successful validation evidence")
        if expected_digest is not None and expected_digest != record.validated_patch_digest:
            raise WorktreeError("Reviewed patch digest is stale")
        delivery = self.manager.deliver(
            self.manager.recover(task_worktree(record)),
            self.workspace,
            expected_patch_digest=record.validated_patch_digest,
        )
        record = record.transition(TaskState.DELIVERED)
        self.store.save_task(record)
        return {
            "task_id": identity,
            "state": record.state.value,
            "changed_paths": [path.as_posix() for path in delivery.changed_paths],
        }

    def rollback_locked(self, identity: str, expected_digest: str | None = None) -> dict[str, Any]:
        record = self.get(identity)
        if record.state is not TaskState.DELIVERED or not record.validated_patch_digest:
            raise WorktreeError("Task is not delivered or has no validation evidence")
        if expected_digest is not None and expected_digest != record.validated_patch_digest:
            raise WorktreeError("Reviewed patch digest is stale")
        delivery = self.manager.rollback(
            self.manager.recover(task_worktree(record)),
            self.workspace,
            expected_patch_digest=record.validated_patch_digest,
        )
        record = record.transition(TaskState.ROLLED_BACK)
        self.store.save_task(record)
        return {
            "task_id": identity,
            "state": record.state.value,
            "changed_paths": [path.as_posix() for path in delivery.changed_paths],
        }

    async def validate_locked(
        self, identity: str, template_id: str, expected_digest: str, temporary: Path
    ) -> dict[str, Any]:
        if template_id not in TEMPLATES:
            raise WorktreeError("Unknown verification template")
        record = self.get(identity)
        with self.store._connect() as db:
            spec = db.execute("SELECT mode FROM task_specs WHERE task_id=?", (identity,)).fetchone()
        if spec and spec[0] == PermissionMode.READONLY.value:
            raise WorktreeError("Readonly tasks cannot execute commands")
        worktree = self.manager.recover(task_worktree(record))
        if self.manager.changes(worktree).patch_digest != expected_digest:
            raise WorktreeError("Reviewed patch digest is stale")
        sandbox = BubblewrapExecutor(SandboxPolicy(worktree.path, temporary))
        record, _ = prepare_resumed_task(self.store, record)
        graph = self.store.load_graph(record.graph_id) if record.graph_id else ExecutionGraph()
        node_id = f"validation.{len(graph.nodes) + 1}"
        argv = TEMPLATES[template_id]["argv"]
        graph.add_node(
            node_id,
            f"Verify {template_id}",
            NodeType.COMMAND,
            input={"argv": argv, "patch_digest": expected_digest, "approved": True},
            policy=NodePolicy(requires_approval=True),
        )
        graph.start(node_id)
        self.store.append_graph(graph)
        self.store.save_task(replace(record, graph_id=graph.graph_id))
        pending = asyncio.create_task(asyncio.to_thread(sandbox.execute, argv))
        try:
            artifact = await asyncio.shield(pending)
            current_digest = self.manager.changes(worktree).patch_digest
            passed = artifact.passed and current_digest == expected_digest
            output = {"metadata": asdict(artifact), "patch_digest": expected_digest}
            if passed:
                graph.succeed(node_id, output)
            else:
                graph.fail(node_id, "Verification failed or modified the reviewed patch")
                # Preserve failed command output as append-only evidence.
                graph.add_node(
                    node_id + ".output",
                    "Verification output",
                    NodeType.SUMMARY,
                    input=output,
                )
                graph.start(node_id + ".output")
                graph.succeed(node_id + ".output", output)
            self.store.append_graph(graph)
            record = self.get(identity)
            record = replace(record, validated_patch_digest=expected_digest if passed else None)
            record = record.transition(
                TaskState.COMPLETED if passed else TaskState.FAILED,
                error=None if passed else "Verification failed",
            )
            self.store.save_task(record)
            return {
                "task_id": identity,
                "passed": passed,
                "artifact": asdict(artifact),
                "patch_digest": current_digest,
                "state": record.state.value,
            }
        except BaseException as exc:
            await asyncio.to_thread(sandbox.cancel)
            with suppress(Exception):
                await pending
            graph.interrupt_running()
            self.store.append_graph(graph)
            record = self.get(identity)
            if record.state is TaskState.ACTIVE:
                self.store.save_task(
                    record.transition(
                        TaskState.CANCELLED
                        if isinstance(exc, asyncio.CancelledError)
                        else TaskState.FAILED,
                        error=type(exc).__name__,
                    )
                )
            raise
