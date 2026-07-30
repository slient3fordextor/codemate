from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ChatRole = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: ChatRole
    content: str
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    message: str | None = None
    messages: list[ChatMessage] | None = None
    current_file: str | None = None
    selected_text: str | None = None
    stream: bool = True
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_message_or_messages(self) -> "ChatCompletionRequest":
        if not self.message and not self.messages:
            msg = "Either message or messages is required"
            raise ValueError(msg)
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
        "error",
    ]
    data: dict[str, Any]
