from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.schemas.chat import ChatMessage

ModelChunkType = Literal[
    "message.start",
    "message.delta",
    "message.done",
    "tool.call.start",
    "tool.call.delta",
    "tool.call.done",
    "usage.update",
]


@dataclass(frozen=True)
class ModelToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    messages: list[ChatMessage]
    model: str | None = None
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    tools: tuple[ModelToolDefinition, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelChunk:
    type: ModelChunkType
    content: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    raw: dict[str, Any] | None = None


class ModelProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ModelAdapter(Protocol):
    supports_tools: bool

    def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]: ...
