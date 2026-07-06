import json
from collections.abc import AsyncIterator
from typing import Any

from app.adapters.models.base import ModelAdapter, ModelProviderError, ModelRequest
from app.schemas.chat import ChatCompletionRequest, ChatMessage


class ChatService:
    def __init__(self, model_adapter: ModelAdapter, default_model: str) -> None:
        self._model_adapter = model_adapter
        self._default_model = default_model

    async def stream_completion(
        self,
        request: ChatCompletionRequest,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        try:
            model_request = ModelRequest(
                messages=self._build_messages(request),
                model=request.model or self._default_model,
                stream=True,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                metadata=request.metadata,
            )
            async for chunk in self._model_adapter.stream_chat(model_request):
                yield self._encode_sse(
                    chunk.type,
                    {
                        "content": chunk.content,
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                        "finish_reason": chunk.finish_reason,
                    },
                )
        except ModelProviderError as exc:
            yield self._encode_sse(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )

    def _build_messages(self, request: ChatCompletionRequest) -> list[ChatMessage]:
        messages = list(request.messages or [])

        if request.current_file or request.selected_text:
            context_parts = []
            if request.current_file:
                context_parts.append(f"Current file: {request.current_file}")
            if request.selected_text:
                context_parts.append(f"Selected text:\n{request.selected_text}")
            messages.append(ChatMessage(role="system", content="\n\n".join(context_parts)))

        if request.message:
            messages.append(ChatMessage(role="user", content=request.message))

        return messages

    def _encode_sse(self, event: str, data: dict[str, Any]) -> str:
        compact_data = {key: value for key, value in data.items() if value is not None}
        return f"event: {event}\ndata: {json.dumps(compact_data, ensure_ascii=False)}\n\n"
