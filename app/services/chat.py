import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.adapters.models.base import ModelAdapter, ModelProviderError, ModelRequest
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.session_memory import SessionMemoryStore


class ChatService:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        default_model: str,
        session_memory_store: SessionMemoryStore | None = None,
        session_memory_enabled: bool = True,
    ) -> None:
        self._model_adapter = model_adapter
        self._default_model = default_model
        self._session_memory_store = session_memory_store
        self._session_memory_enabled = session_memory_enabled

    async def stream_completion(
        self,
        request: ChatCompletionRequest,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        session_id = request.session_id or self._new_session_id()
        assistant_parts: list[str] = []
        try:
            model_request = ModelRequest(
                messages=self._build_messages(request, session_id),
                model=request.model or self._default_model,
                stream=True,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                metadata=request.metadata,
            )
            async for chunk in self._model_adapter.stream_chat(model_request):
                if chunk.type == "message.delta" and chunk.content:
                    assistant_parts.append(chunk.content)
                yield self._encode_sse(
                    chunk.type,
                    {
                        "session_id": session_id if chunk.type == "message.start" else None,
                        "content": chunk.content,
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                        "finish_reason": chunk.finish_reason,
                    },
                )
            self._remember_turn(request, session_id, "".join(assistant_parts).strip())
        except ModelProviderError as exc:
            yield self._encode_sse(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )

    def _build_messages(self, request: ChatCompletionRequest, session_id: str) -> list[ChatMessage]:
        messages = list(request.messages or [])
        memory_store = self._memory_store_for_request(request)
        if memory_store is not None:
            messages.extend(memory_store.get_messages(session_id))

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

    def _memory_store_for_request(
        self,
        request: ChatCompletionRequest,
    ) -> SessionMemoryStore | None:
        if not self._session_memory_enabled:
            return None
        if self._session_memory_store is None:
            return None
        if request.message is None or request.messages:
            return None
        return self._session_memory_store

    def _remember_turn(
        self,
        request: ChatCompletionRequest,
        session_id: str,
        assistant_message: str,
    ) -> None:
        memory_store = self._memory_store_for_request(request)
        if memory_store is None or not assistant_message:
            return
        memory_store.append_turn(session_id, request.message or "", assistant_message)

    def _new_session_id(self) -> str:
        return f"ses_{uuid4().hex}"

    def _encode_sse(self, event: str, data: dict[str, Any]) -> str:
        compact_data = {key: value for key, value in data.items() if value is not None}
        return f"event: {event}\ndata: {json.dumps(compact_data, ensure_ascii=False)}\n\n"
