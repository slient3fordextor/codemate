from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.chat import ChatMessage


@dataclass(frozen=True)
class SessionMemoryPolicy:
    max_turns: int = 10
    max_sessions: int = 100

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            msg = "max_turns must be greater than 0"
            raise ValueError(msg)
        if self.max_sessions <= 0:
            msg = "max_sessions must be greater than 0"
            raise ValueError(msg)

    @property
    def max_messages(self) -> int:
        return self.max_turns * 2


class SessionMemoryStore(Protocol):
    def get_messages(self, session_id: str) -> list[ChatMessage]:
        ...

    def append_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        ...

    def clear(self) -> None:
        ...


@dataclass
class SessionMemory:
    messages: list[ChatMessage] = field(default_factory=list)


class InMemorySessionMemoryStore:
    def __init__(self, max_turns: int = 10, max_sessions: int = 100) -> None:
        self._policy = SessionMemoryPolicy(max_turns=max_turns, max_sessions=max_sessions)
        self._sessions: OrderedDict[str, SessionMemory] = OrderedDict()

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        memory = self._sessions.get(session_id)
        if memory is None:
            return []
        self._sessions.move_to_end(session_id)
        return [message.model_copy(deep=True) for message in memory.messages]

    def append_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        memory = self._sessions.setdefault(session_id, SessionMemory())
        memory.messages.extend(
            [
                ChatMessage(role="user", content=user_message),
                ChatMessage(role="assistant", content=assistant_message),
            ],
        )
        if len(memory.messages) > self._policy.max_messages:
            memory.messages = memory.messages[-self._policy.max_messages :]

        self._sessions.move_to_end(session_id)
        while len(self._sessions) > self._policy.max_sessions:
            self._sessions.popitem(last=False)

    def clear(self) -> None:
        self._sessions.clear()
