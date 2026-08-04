from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.chat import ChatMessage
from app.services.token_counter import EstimatedTokenCounter, TokenCounter


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


@dataclass(frozen=True)
class MemoryCapabilities:
    backend: str
    layers: tuple[str, ...]
    persistent: bool
    cross_process: bool
    preference_management: bool


class SessionMemoryStore(Protocol):
    @property
    def capabilities(self) -> MemoryCapabilities: ...

    async def ping(self) -> None: ...

    async def get_messages(
        self,
        session_id: str,
        current_user_message: str = "",
        token_counter: TokenCounter | None = None,
        token_budget: int | None = None,
    ) -> list[ChatMessage]: ...

    async def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        token_counter: TokenCounter | None = None,
        request_id: str | None = None,
    ) -> None: ...

    async def clear(self) -> None: ...

    async def clear_project(self) -> None: ...

    async def clear_global_preferences(self) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def delete_preference(self, preference_id: int) -> bool: ...

    async def list_preferences(self) -> list[Any]: ...

    async def list_conflicts(self) -> list[Any]: ...


@dataclass
class SessionMemory:
    messages: list[ChatMessage] = field(default_factory=list)


class InMemorySessionMemoryStore:
    def __init__(self, max_turns: int = 10, max_sessions: int = 100) -> None:
        self._policy = SessionMemoryPolicy(max_turns=max_turns, max_sessions=max_sessions)
        self._sessions: OrderedDict[str, SessionMemory] = OrderedDict()

    @property
    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            backend="memory",
            layers=("L1",),
            persistent=False,
            cross_process=False,
            preference_management=False,
        )

    async def ping(self) -> None:
        return None

    async def get_messages(
        self,
        session_id: str,
        current_user_message: str = "",
        token_counter: TokenCounter | None = None,
        token_budget: int | None = None,
    ) -> list[ChatMessage]:
        del current_user_message
        memory = self._sessions.get(session_id)
        if memory is None:
            return []
        self._sessions.move_to_end(session_id)
        messages = [message.model_copy(deep=True) for message in memory.messages]
        if token_budget is None:
            return messages

        counter = token_counter or EstimatedTokenCounter()
        selected: list[ChatMessage] = []
        for index in range(len(messages) - 2, -1, -2):
            turn = messages[index : index + 2]
            candidate = [*turn, *selected]
            if counter.count_messages(candidate) <= max(0, token_budget):
                selected = candidate
                continue
            break
        return selected

    async def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        token_counter: TokenCounter | None = None,
        request_id: str | None = None,
    ) -> None:
        del token_counter, request_id
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

    async def clear(self) -> None:
        self._sessions.clear()

    async def clear_project(self) -> None:
        await self.clear()

    async def clear_global_preferences(self) -> None:
        return None

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def delete_preference(self, preference_id: int) -> bool:
        del preference_id
        return False

    async def list_preferences(self) -> list[Any]:
        return []

    async def list_conflicts(self) -> list[Any]:
        return []
