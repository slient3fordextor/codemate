import hashlib
from contextlib import ExitStack
from pathlib import Path

from app.agent.locking import process_lock
from app.agent.worktree import WorktreeError
from app.core.errors import AppError


def session_lease(root: Path, session_id: str, action: str) -> ExitStack:
    stack = ExitStack()
    identity = hashlib.sha256(session_id.encode()).hexdigest()
    try:
        stack.enter_context(process_lock(root / f"session-{identity}.lock", action=action))
    except WorktreeError as exc:
        stack.close()
        raise AppError(
            "SESSION_BUSY", "Conversation is currently running; retry after it finishes", 409
        ) from exc
    return stack


def chat_lease(root: Path, session_id: str, slots: int) -> ExitStack:
    stack = session_lease(root, session_id, "chat")
    for index in range(slots):
        try:
            stack.enter_context(process_lock(root / f"stream-{index}.lock", action="chat-stream"))
            return stack
        except WorktreeError:
            continue
    stack.close()
    raise AppError("CHAT_OVERLOADED", "Concurrent chat limit reached; retry later", 429)
