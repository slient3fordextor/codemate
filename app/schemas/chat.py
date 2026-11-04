import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ChatRole = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str = Field(max_length=128000)
    name: str | None = Field(default=None, max_length=128)


class ContextAttachment(BaseModel):
    kind: Literal["file", "selection", "diff"]
    path: str | None = Field(default=None, max_length=1024)
    content: str = Field(max_length=120000)


class ChatCompletionRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    turn_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    message: str | None = Field(default=None, max_length=64000)
    messages: list[ChatMessage] | None = Field(default=None, max_length=100)
    current_file: str | None = Field(default=None, max_length=1024)
    selected_text: str | None = Field(default=None, max_length=120000)
    context: list[ContextAttachment] = Field(default_factory=list, max_length=8)
    stream: bool = True
    model: str | None = Field(default=None, max_length=256)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_message_or_messages(self) -> "ChatCompletionRequest":
        if not self.message and not self.messages:
            msg = "Either message or messages is required"
            raise ValueError(msg)
        if len(json.dumps(self.metadata).encode()) > 8192 or len(self.metadata) > 32:
            raise ValueError("Metadata exceeds limit")
        if sum(len(item.content.encode()) for item in self.context) > 120000:
            raise ValueError("Attached context exceeds limit")
        return self


class ChatCompletionResponse(BaseModel):
    session_id: str | None = None
    model: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


class ChatStreamEvent(BaseModel):
    event: Literal[
        "message.start",
        "message.delta",
        "message.done",
        "usage.update",
        "task.update",
        "context.usage",
        "memory.warning",
        "completion.done",
        "error",
    ]
    data: dict[str, Any]
