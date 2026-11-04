"""HTTP coding tasks; approvals and budgets survive worker/process boundaries."""

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import ExitStack, suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.adapters.models import build_model_adapter
from app.agent.budget import TaskBudget
from app.agent.locking import process_lock
from app.agent.persistence import PersistenceError
from app.agent.runner import AgentProgressEvent, ToolApprovalRequest
from app.agent.task_state import TaskState
from app.agent.tasks import AgentTaskService, task_worktree
from app.agent.tools import PermissionMode
from app.agent.tools.readonly import ReadonlyToolError
from app.agent.worktree import GitAdapter, WorktreeError
from app.api.v1.chat import LeasedStreamingResponse
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.agent import ApprovalDecision, TaskAction, TaskCreate, TaskRun, TaskValidation
from app.services.model_profiles import ModelProfiles
from app.services.validation_templates import TEMPLATES, review_diff

router = APIRouter(prefix="/agent")


async def finish_thread(callback: Callable[..., Any], *args: Any) -> Any:
    pending = asyncio.create_task(asyncio.to_thread(callback, *args))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True), suppress(Exception):
            await pending
        raise


def service_for() -> AgentTaskService:
    settings = get_settings()
    try:
        return AgentTaskService(settings.workspace.root, settings)
    except WorktreeError as exc:
        raise AppError(
            "WORKSPACE_NOT_READY", "Coding tasks require a Git repository with HEAD", 409
        ) from exc


def require_task(service: AgentTaskService, identity: str) -> None:
    try:
        service.get(identity)
    except (PersistenceError, WorktreeError) as exc:
        raise AppError("TASK_NOT_FOUND", "Task not found in this workspace", 404) from exc


def task_lease(service: AgentTaskService, identity: str, *, capacity: bool = False) -> ExitStack:
    stack = ExitStack()
    try:
        stack.enter_context(service.manager.task_lock(identity, action="web-task"))
    except WorktreeError as exc:
        stack.close()
        raise AppError("TASK_BUSY", "Task is already running or locked", 409) from exc
    if not capacity:
        return stack
    for index in range(service.settings.agent_max_concurrency):
        try:
            stack.enter_context(
                process_lock(
                    service.store.path.parent / "agent-locks" / f"slot-{index}.lock",
                    action="agent-run",
                )
            )
            return stack
        except WorktreeError:
            continue
    stack.close()
    raise AppError("AGENT_OVERLOADED", "Concurrent Agent limit reached", 429)


def detail(service: AgentTaskService, identity: str) -> dict[str, Any]:
    record = service.get(identity)
    worktree = service.manager.recover(task_worktree(record))
    changes = service.manager.changes(worktree)
    with service.store._connect() as db:
        budget = db.execute("SELECT * FROM task_budgets WHERE task_id=?", (identity,)).fetchone()
        spec = db.execute("SELECT * FROM task_specs WHERE task_id=?", (identity,)).fetchone()
        runs = db.execute(
            "SELECT task_runs.*,task_run_graphs.graph_id FROM task_runs "
            "LEFT JOIN task_run_graphs ON task_runs.id=task_run_graphs.run_id "
            "WHERE task_id=? ORDER BY started_at DESC",
            (identity,),
        ).fetchall()
        approvals = db.execute(
            "SELECT * FROM task_approvals WHERE task_id=? ORDER BY created_at DESC", (identity,)
        ).fetchall()
    nodes = (
        [asdict(node) for node in service.store.load_graph(record.graph_id).nodes]
        if record.graph_id
        else []
    )
    public_budget = dict(budget) if budget else None
    if public_budget:
        public_budget.pop("messages")
    result = jsonable_encoder(
        {
            **asdict(record),
            "mode": spec["mode"] if spec else "confirm",
            "profile_id": spec["profile_id"] if spec else None,
            "budget": public_budget,
            "runs": [dict(row) for row in runs],
            "approvals": [dict(row) for row in approvals],
            "nodes": nodes,
            "diff": changes.patch.decode("utf-8", errors="replace"),
            "patch_digest": changes.patch_digest,
            "changed_paths": [path.as_posix() for path in service.manager.changed_paths(worktree)],
            "validation_current": bool(record.validated_patch_digest)
            and record.validated_patch_digest == changes.patch_digest,
        }
    )
    return cast(dict[str, Any], result)


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    settings = get_settings()
    try:
        await asyncio.to_thread(GitAdapter().head_commit, settings.workspace.root)
        git_ready = True
    except WorktreeError:
        git_ready = False
    return {
        "git_ready": git_ready,
        "sandbox_installed": bool(shutil.which("bwrap")),
        "workspace": str(settings.workspace.root),
        "max_concurrency": settings.agent_max_concurrency,
        "max_rounds": settings.agent_max_rounds,
        "max_tokens": settings.agent_max_tokens,
        "max_replans": settings.agent_max_replans,
        "templates": [{"id": key, **value} for key, value in TEMPLATES.items()],
    }


@router.post("/tasks")
async def create_task(body: TaskCreate) -> dict[str, Any]:
    service = service_for()
    if not body.objective.strip():
        raise AppError("TASK_INVALID", "Task objective cannot be empty", 422)
    if body.profile_id:
        ModelProfiles(service.store).get(body.profile_id)
    identity = uuid4().hex
    try:
        with task_lease(service, identity):
            record = await finish_thread(service.create, identity, body.objective.strip())
            with service.store._transaction() as db:
                db.execute(
                    "INSERT INTO task_specs VALUES(?,?,?,NULL)",
                    (identity, body.mode.value, body.profile_id),
                )
            TaskBudget(
                service.store,
                record,
                max_rounds=min(body.max_rounds, service.settings.agent_max_rounds),
                max_tokens=min(body.max_tokens, service.settings.agent_max_tokens),
                max_replans=min(body.max_replans, service.settings.agent_max_replans),
            )
            return await asyncio.to_thread(detail, service, identity)
    except WorktreeError as exc:
        raise AppError("TASK_CREATE_FAILED", str(exc), 409) from exc


@router.get("/tasks")
async def list_tasks(
    offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)
) -> dict[str, Any]:
    service = service_for()
    with service.store._connect() as db:
        total = db.execute(
            "SELECT count(*) FROM tasks WHERE repository_root=?", (str(service.workspace),)
        ).fetchone()[0]
        rows = db.execute(
            "SELECT task_id,objective,state,updated_at FROM tasks WHERE repository_root=? "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (str(service.workspace), limit, offset),
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@router.get("/tasks/{identity}")
async def get_task(identity: str) -> dict[str, Any]:
    service = service_for()
    require_task(service, identity)
    try:
        return await asyncio.to_thread(detail, service, identity)
    except WorktreeError as exc:
        raise AppError("TASK_WORKTREE_MISSING", str(exc), 409) from exc


def encode(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"


async def approval(
    service: AgentTaskService,
    identity: str,
    request: ToolApprovalRequest,
    publish: Callable[[str, dict[str, Any]], None],
) -> bool:
    budget = service.current_budget
    if budget is None:
        raise RuntimeError("Task run has not started")
    record = service.get(identity)
    digest = service.manager.changes(task_worktree(record)).patch_digest
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=service.settings.agent_approval_timeout_seconds)
    with service.store._transaction() as db:
        db.execute(
            "INSERT INTO task_approvals VALUES(?,?,?,?,?,?,?,?,?)",
            (
                request.id,
                identity,
                budget.run_id,
                request.tool,
                request.preview,
                digest,
                "pending",
                expires.isoformat(),
                now.isoformat(),
            ),
        )
    service.store.save_task(record.transition(TaskState.WAITING_APPROVAL))
    publish(
        "approval.required",
        {
            "id": request.id,
            "run_id": budget.run_id,
            "tool": request.tool,
            "preview": request.preview,
            "patch_digest": digest,
            "expires_at": expires.isoformat(),
        },
    )
    try:
        while datetime.now(UTC) < expires:
            with service.store._connect() as db:
                row = db.execute(
                    "SELECT status FROM task_approvals WHERE id=?", (request.id,)
                ).fetchone()
            if row[0] != "pending":
                if row[0] != "approved":
                    return False
                if (
                    service.manager.changes(task_worktree(service.get(identity))).patch_digest
                    != digest
                ):
                    raise ReadonlyToolError("Worktree changed while approval was pending")
                with service.store._transaction() as db:
                    db.execute(
                        "UPDATE task_approvals SET status='consumed' "
                        "WHERE id=? AND status='approved'",
                        (request.id,),
                    )
                return True
            await asyncio.sleep(0.1)
        return False
    finally:
        with service.store._transaction() as db:
            db.execute(
                "UPDATE task_approvals SET status='expired' WHERE id=? AND status='pending'",
                (request.id,),
            )
        record = service.get(identity)
        if record.state is TaskState.WAITING_APPROVAL:
            service.store.save_task(record.transition(TaskState.ACTIVE))
        publish("approval.resolved", {"id": request.id})


@router.post("/tasks/{identity}/approvals/{approval_id}")
async def decide(identity: str, approval_id: str, body: ApprovalDecision) -> dict[str, Any]:
    service = service_for()
    require_task(service, identity)
    digest = service.manager.changes(task_worktree(service.get(identity))).patch_digest
    with service.store._transaction() as db:
        row = db.execute(
            "SELECT * FROM task_approvals WHERE id=? AND task_id=?", (approval_id, identity)
        ).fetchone()
        run = db.execute(
            "SELECT status FROM task_runs WHERE id=? AND task_id=?", (body.run_id, identity)
        ).fetchone()
        if (
            row is None
            or run is None
            or run[0] != "running"
            or row["run_id"] != body.run_id
            or row["status"] != "pending"
            or row["expires_at"] <= datetime.now(UTC).isoformat()
            or row["patch_digest"] != body.patch_digest
            or digest != body.patch_digest
        ):
            raise AppError(
                "APPROVAL_STALE", "Approval expired, already decided or patch changed", 409
            )
        status = "approved" if body.approved else "rejected"
        db.execute("UPDATE task_approvals SET status=? WHERE id=?", (status, approval_id))
    return {"id": approval_id, "status": status}


def streaming_task(
    service: AgentTaskService,
    identity: str,
    request: Request,
    execute: Callable[[Path, Callable[[str, dict[str, Any]], None]], Awaitable[dict[str, Any]]],
) -> StreamingResponse:
    lease = task_lease(service, identity, capacity=True)
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
    producer: asyncio.Task[None] | None = None

    def publish(event: str, data: dict[str, Any]) -> None:
        queue.put_nowait(encode(event, data))

    async def cleanup() -> None:
        with anyio.CancelScope(shield=True):
            if producer is not None and not producer.done():
                producer.cancel()
            if producer is not None:
                with suppress(asyncio.CancelledError):
                    await producer

    async def stream() -> AsyncGenerator[str, None]:
        nonlocal producer

        async def produce() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="codemate-web-task-") as temporary:
                    async with asyncio.timeout(service.settings.agent_timeout_seconds):
                        result = await execute(Path(temporary), publish)
                    await queue.put(encode("task.done", result))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(
                    encode(
                        "error",
                        {
                            "code": getattr(exc, "code", type(exc).__name__),
                            "message": "Task failed; inspect saved evidence before resuming",
                            "request_id": getattr(request.state, "request_id", None),
                        },
                    )
                )

        producer = asyncio.create_task(produce())
        try:
            yield encode(
                "task.started",
                {"task_id": identity, "request_id": getattr(request.state, "request_id", None)},
            )
            while not producer.done() or not queue.empty():
                if await request.is_disconnected():
                    return
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
        finally:
            await cleanup()
            lease.close()

    return LeasedStreamingResponse(
        stream(),
        lease,
        cleanup,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{identity}/run")
async def run_task(identity: str, body: TaskRun, request: Request) -> StreamingResponse:
    service = service_for()
    require_task(service, identity)
    record = service.get(identity)
    if record.state in {TaskState.DELIVERED, TaskState.DISCARDED}:
        raise AppError("TASK_TERMINAL", "Delivered/discarded tasks cannot run", 409)
    with service.store._connect() as db:
        spec = db.execute("SELECT * FROM task_specs WHERE task_id=?", (identity,)).fetchone()
    mode = PermissionMode(spec["mode"] if spec else "confirm")
    config = (
        ModelProfiles(service.store).get(spec["profile_id"])[0]
        if spec and spec["profile_id"]
        else service.settings.model
    )
    model = build_model_adapter(config)

    async def execute(
        temporary: Path, publish: Callable[[str, dict[str, Any]], None]
    ) -> dict[str, Any]:
        def progress(event: AgentProgressEvent) -> None:
            publish(event.type, event.data)

        async def approve_tool(action: ToolApprovalRequest) -> bool:
            return await approval(service, identity, action, publish)

        async def approve_round(number: int) -> bool:
            return await approve_tool(
                ToolApprovalRequest(
                    f"round:{uuid4().hex}",
                    "round",
                    {"round": number},
                    f"Continue cumulative task round {number}",
                )
            )

        outcome = await service.run_locked(
            identity,
            record.objective,
            mode,
            temporary,
            model,
            config,
            progress=progress,
            approve_tool=approve_tool,
            approve_round=approve_round,
            approve_interrupted_command=body.approve_interrupted_command,
            request_id=getattr(request.state, "request_id", None),
            max_rounds=service.settings.agent_max_rounds,
            max_tokens=service.settings.agent_max_tokens,
            max_replans=service.settings.agent_max_replans,
        )
        return outcome.payload()

    return streaming_task(service, identity, request, execute)


@router.post("/tasks/{identity}/validate")
async def validate_task(identity: str, body: TaskValidation, request: Request) -> StreamingResponse:
    service = service_for()
    require_task(service, identity)
    if not body.approved:
        raise AppError("EXPLICIT_APPROVAL_REQUIRED", "Verification requires explicit approval", 400)
    if body.template_id not in TEMPLATES:
        raise AppError("TEMPLATE_NOT_FOUND", "Unknown verification template", 404)

    async def execute(
        temporary: Path, publish: Callable[[str, dict[str, Any]], None]
    ) -> dict[str, Any]:
        publish("validation.started", {"template_id": body.template_id})
        return await service.validate_locked(
            identity, body.template_id, body.patch_digest, temporary
        )

    return streaming_task(service, identity, request, execute)


async def delivery_action(identity: str, body: TaskAction, rollback: bool) -> dict[str, Any]:
    if not body.approved:
        raise AppError(
            "EXPLICIT_APPROVAL_REQUIRED", "Delivery/rollback requires explicit approval", 400
        )
    service = service_for()
    require_task(service, identity)
    try:
        with task_lease(service, identity):
            action = service.rollback_locked if rollback else service.deliver_locked
            return cast(dict[str, Any], await finish_thread(action, identity, body.patch_digest))
    except WorktreeError as exc:
        raise AppError("TASK_DELIVERY_CONFLICT", str(exc), 409) from exc


@router.post("/tasks/{identity}/deliver")
async def deliver_task(identity: str, body: TaskAction) -> dict[str, Any]:
    return await delivery_action(identity, body, False)


@router.post("/tasks/{identity}/rollback")
async def rollback_task(identity: str, body: TaskAction) -> dict[str, Any]:
    return await delivery_action(identity, body, True)


@router.get("/tasks/{identity}/review")
async def review_task(identity: str) -> dict[str, Any]:
    service = service_for()
    require_task(service, identity)
    current = await asyncio.to_thread(detail, service, identity)
    return {
        "task_id": identity,
        "patch_digest": current["patch_digest"],
        "validation_current": current["validation_current"],
        **review_diff(current["diff"], current["nodes"]),
    }


@router.get("/tasks/{identity}/runs/{run_id}")
async def run_evidence(identity: str, run_id: str) -> dict[str, Any]:
    service = service_for()
    require_task(service, identity)
    with service.store._connect() as db:
        row = db.execute(
            "SELECT task_runs.*,task_run_graphs.graph_id FROM task_runs "
            "LEFT JOIN task_run_graphs ON task_runs.id=task_run_graphs.run_id "
            "WHERE task_runs.id=? AND task_id=?",
            (run_id, identity),
        ).fetchone()
    if row is None:
        raise AppError("RUN_NOT_FOUND", "Task run not found", 404)
    graph = service.store.load_graph(row["graph_id"]) if row["graph_id"] else None
    return {
        **dict(row),
        "nodes": jsonable_encoder([asdict(node) for node in graph.nodes]) if graph else [],
    }
