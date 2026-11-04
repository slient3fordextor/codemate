import asyncio
from typing import Any, Literal, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.services.chat_capacity import session_lease
from app.services.conversations import ConversationStore

router = APIRouter(prefix="/sessions")


class SessionChanges(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    archived: bool | None = None


def store_for(request: Request) -> ConversationStore:
    return cast(ConversationStore, request.app.state.conversation_store)


@router.get("")
async def list_sessions(
    request: Request,
    archived: bool = False,
    q: str = Query(default="", max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await asyncio.to_thread(
        store_for(request).list, archived=archived, query=q, offset=offset, limit=limit
    )


@router.post("")
async def create_session(request: Request, changes: SessionChanges) -> dict[str, Any]:
    return await asyncio.to_thread(store_for(request).create, changes.title or "新对话")


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await asyncio.to_thread(store_for(request).turns, session_id, offset=offset, limit=limit)


@router.get("/stats")
async def session_stats(request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(store_for(request).stats)


@router.patch("/{session_id}")
async def update_session(
    session_id: str, request: Request, changes: SessionChanges
) -> dict[str, Any]:
    store = store_for(request)
    with session_lease(store.path.parent / "chat-locks", session_id, "session-update"):
        return await asyncio.to_thread(store.update, session_id, changes.title, changes.archived)


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, str]:
    store = store_for(request)
    with session_lease(store.path.parent / "chat-locks", session_id, "delete-history"):
        await asyncio.to_thread(store.delete, session_id)
    return {"status": "ok", "memory": "unchanged"}


@router.get("/{session_id}/export", response_model=None)
async def export_session(
    session_id: str, request: Request, format: Literal["json", "markdown"] = "json"
) -> JSONResponse | PlainTextResponse:
    payload = await asyncio.to_thread(store_for(request).export, session_id)
    if format == "json":
        return JSONResponse(payload)
    lines = [f"# {payload['session']['title']}"]
    for turn in payload["turns"]:
        lines.extend(
            ["\n## 用户", turn["user"], f"\n## 助手（{turn['status']}）", turn["assistant"]]
        )
    return PlainTextResponse("\n\n".join(lines), media_type="text/markdown")
