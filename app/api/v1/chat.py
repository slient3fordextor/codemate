import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import ExitStack, aclosing, suppress
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from app.adapters.models import build_model_adapter
from app.agent.tools.readonly import ReadonlyToolError
from app.api.v1.sessions import store_for
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.chat import ChatCompletionRequest
from app.services.chat import ChatService
from app.services.chat_capacity import chat_lease
from app.services.context_budget import ContextBudgetPlanner
from app.services.language.factory import build_language_context_service
from app.services.language.interfaces import LanguageContextConfig
from app.services.project_context import ProjectContext
from app.services.token_counter import build_token_counter

router = APIRouter(prefix="/chat")
logger = logging.getLogger("codemate.chat")


class LeasedStreamingResponse(StreamingResponse):
    """Release capacity even when headers fail before the iterator starts."""

    def __init__(
        self,
        content: AsyncGenerator[str, None],
        lease: ExitStack,
        on_close: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> None:
        super().__init__(content, **kwargs)
        self._events = content
        self._lease = lease
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self._events.aclose()
                await self._on_close()
            finally:
                self._lease.close()


@router.post("/completions", response_model=None)
async def create_chat_completion(
    request_body: ChatCompletionRequest,
    request: Request,
) -> StreamingResponse | JSONResponse:
    settings = get_settings()
    context = ProjectContext(
        settings.workspace.root,
        settings.language_support.context_max_bytes,
        settings.language_support.context_max_files,
    )
    try:
        context.validate(request_body.context)
        if request_body.selected_text:
            context.validate_text(request_body.selected_text)
    except ReadonlyToolError as exc:
        raise AppError("PROJECT_CONTEXT_REJECTED", str(exc), 400) from exc
    model_name = request_body.model or settings.model.name
    service = ChatService(
        model_adapter=build_model_adapter(settings.model),
        default_model=settings.model.name,
        session_memory_store=request.app.state.session_memory_store,
        session_memory_enabled=settings.session_memory.enabled,
        language_context_service=build_language_context_service(
            settings.workspace.root,
            LanguageContextConfig(
                enabled=settings.language_support.enabled,
                default_level=settings.language_support.default_level,
                max_files=settings.language_support.context_max_files,
                max_bytes=settings.language_support.context_max_bytes,
                enable_tree_sitter=settings.language_support.enable_tree_sitter,
                enable_lsp=settings.language_support.enable_lsp,
            ),
        ),
        context_budget_planner=ContextBudgetPlanner(
            token_counter=build_token_counter(settings.model.provider, model_name),
            context_window=settings.model.context_window,
        ),
        project_context=context,
    )
    request_id = getattr(request.state, "request_id", None)
    identity = str(request_id) if request_id else None
    session_id = request_body.session_id or f"ses_{uuid4().hex}"
    turn_id = request_body.turn_id or uuid4().hex
    request_body = request_body.model_copy(update={"session_id": session_id, "turn_id": turn_id})
    store = store_for(request)
    lease = chat_lease(
        store.path.parent / "chat-locks",
        session_id,
        min(settings.chat_max_concurrency, 2)
        if settings.security.allow_remote_api
        else settings.chat_max_concurrency,
    )
    try:
        await asyncio.to_thread(
            store.begin,
            session_id,
            turn_id,
            request_body.message
            or next(
                (
                    item.content
                    for item in reversed(request_body.messages or [])
                    if item.role == "user"
                ),
                "",
            ),
            identity,
        )
    except BaseException:
        lease.close()
        raise
    started = time.monotonic()
    stream_entered = False

    async def stream() -> AsyncGenerator[str, None]:
        nonlocal stream_entered
        stream_entered = True
        content = ""
        status = "cancelled"
        error: str | None = None
        metrics: dict[str, Any] = {"request_id": identity}
        saved = False
        try:
            async with asyncio.timeout(settings.chat_timeout_seconds):
                async with aclosing(
                    service.stream_completion(
                        request_body, request_id=identity, is_disconnected=request.is_disconnected
                    )
                ) as events:
                    async for event in events:
                        name = event.split("\n", 1)[0][7:]
                        data = json.loads(event.split("data: ", 1)[1])
                        if name == "message.delta":
                            content += data.get("content", "")
                            if "first_token_ms" not in metrics:
                                metrics["first_token_ms"] = round(
                                    (time.monotonic() - started) * 1000
                                )
                        if name in {"context.usage", "usage.update"}:
                            metrics[name] = data
                        if name == "error":
                            status, error = "failed", data.get("code", "MODEL_ERROR")
                        if name == "completion.done":
                            status = "completed" if data.get("memory_saved") else "memory_warning"
                            metrics["duration_ms"] = round((time.monotonic() - started) * 1000)
                            await asyncio.to_thread(
                                store.finish,
                                turn_id,
                                content,
                                status,
                                error,
                                metrics if settings.chat_metrics_enabled else {},
                            )
                            saved = True
                            data.update(
                                {
                                    "turn_id": turn_id,
                                    "history_saved": True,
                                    "duration_ms": metrics["duration_ms"],
                                    "first_token_ms": metrics.get("first_token_ms"),
                                }
                            )
                            event = service._encode_sse(name, data)
                        yield event
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:
            status, error = "failed", getattr(exc, "code", type(exc).__name__)
            yield service._encode_sse(
                "error",
                {
                    "code": error,
                    "message": "Completion failed; partial output retained",
                    "request_id": identity,
                },
            )
        finally:
            try:
                if not saved:
                    metrics["duration_ms"] = round((time.monotonic() - started) * 1000)
                    with suppress(AppError):
                        await asyncio.shield(
                            asyncio.to_thread(
                                store.finish,
                                turn_id,
                                content,
                                status,
                                error,
                                metrics if settings.chat_metrics_enabled else {},
                            )
                        )
                if settings.chat_metrics_enabled:
                    logger.info(
                        "chat finished",
                        extra={
                            "request_id": identity,
                            "session_id": session_id,
                            "status": status,
                            "duration_ms": metrics.get("duration_ms"),
                            "first_token_ms": metrics.get("first_token_ms"),
                            "error_code": error,
                        },
                    )
            finally:
                lease.close()

    if not request_body.stream:
        response_data: dict[str, Any] = {
            "session_id": session_id,
            "model": model_name,
            "content": "",
        }
        async with aclosing(stream()) as events:
            async for event in events:
                name = event.split("\n", 1)[0][7:]
                data = json.loads(event.split("data: ", 1)[1])
                if name == "error":
                    raise AppError(data["code"], data["message"], 502)
                if name == "message.delta":
                    response_data["content"] += data.get("content", "")
                if name == "usage.update":
                    response_data["usage"] = {
                        key: value
                        for key, value in data.items()
                        if key in {"input_tokens", "output_tokens"} and value is not None
                    }
                if name == "message.done":
                    response_data["finish_reason"] = data.get("finish_reason")
                if name == "memory.warning":
                    response_data["memory_warning"] = data["code"]
                if name == "completion.done":
                    response_data.update(data)
        response_data["content"] = response_data["content"].strip()
        return JSONResponse(content=response_data)

    async def finish_unstarted() -> None:
        if not stream_entered:
            with suppress(AppError):
                await asyncio.to_thread(
                    store.finish, turn_id, "", "cancelled", "Disconnected before stream", {}
                )

    return LeasedStreamingResponse(
        stream(),
        lease,
        finish_unstarted,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
