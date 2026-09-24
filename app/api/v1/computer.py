"""Same-origin, loopback-only desktop Agent sessions."""

import asyncio
import contextlib
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.adapters.models import build_model_adapter
from app.agent.runner import ReadonlyAgentRunner, ToolApprovalRequest
from app.agent.tools.desktop import DesktopControl
from app.agent.tools.readonly import ReadonlyToolError
from app.core.config import get_settings
from app.services.context_budget import ContextBudgetPlanner
from app.services.token_counter import build_token_counter

router = APIRouter(prefix="/computer")
# One physical desktop must never be driven by concurrent agents.
_desktop_lock = asyncio.Lock()


def local_socket(socket: WebSocket) -> bool:
    origin = urlsplit(socket.headers.get("origin", ""))
    try:
        peer_local = socket.client is not None and ip_address(socket.client.host).is_loopback
        host_local = ip_address(socket.url.hostname or "").is_loopback
    except ValueError:
        return False
    expected_scheme = "https" if socket.url.scheme == "wss" else "http"
    return bool(
        peer_local
        and host_local
        and origin.scheme == expected_scheme
        and origin.netloc == socket.headers.get("host")
    )


@router.websocket("/run")
async def run_computer(socket: WebSocket) -> None:
    if not local_socket(socket):
        await socket.close(code=1008)
        return
    await socket.accept()
    if _desktop_lock.locked():
        await socket.send_json({"type": "error", "message": "已有桌面任务正在运行"})
        await socket.close()
        return
    async with _desktop_lock:
        task: asyncio.Task[None] | None = None
        pending: dict[str, asyncio.Future[bool]] = {}
        tools: DesktopControl | None = None
        try:
            start = await asyncio.wait_for(socket.receive_json(), 30)
            objective = start.get("objective") if isinstance(start, dict) else None
            if not isinstance(objective, str) or not objective.strip() or len(objective) > 16000:
                raise ReadonlyToolError("任务不能为空且不能超过 16000 字符")
            tools = DesktopControl()
            settings = get_settings()
            if settings.model.provider == "mock":
                raise ReadonlyToolError("请先在设置中连接真实模型；Mock 不执行电脑任务")
            runner = ReadonlyAgentRunner(
                build_model_adapter(settings.model),
                settings.model.name,
                tools,
                context_budget_planner=ContextBudgetPlanner(
                    build_token_counter(settings.model.provider, settings.model.name),
                    settings.model.context_window,
                ),
            )

            async def approve(request: ToolApprovalRequest) -> bool:
                future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                pending[request.id] = future
                await socket.send_json(
                    {"type": "approval", "id": request.id, "preview": request.preview}
                )
                try:
                    allowed = await asyncio.wait_for(future, 120)
                    if not allowed:
                        raise ReadonlyToolError("用户取消了桌面操作")
                    return allowed
                finally:
                    pending.pop(request.id, None)

            async def execute() -> None:
                try:
                    result = await runner.run(objective, approve_tool=approve)
                    await socket.send_json(
                        {"type": "done", "answer": result.answer, "error": result.error}
                    )
                except Exception as exc:
                    await socket.send_json({"type": "error", "message": str(exc)})
                finally:
                    await socket.close()

            task = asyncio.create_task(execute())
            while True:
                message: Any = await socket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "stop":
                    break
                future = pending.get(str(message.get("id", "")))
                if future is not None and not future.done():
                    future.set_result(message.get("approved") is True)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ReadonlyToolError, TimeoutError, ValueError) as exc:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.send_json({"type": "error", "message": str(exc)})
        finally:
            if tools is not None:
                tools.cancel()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeError, WebSocketDisconnect):
                    await task
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.close()
