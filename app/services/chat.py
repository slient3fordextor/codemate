import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.adapters.models.base import ModelAdapter, ModelProviderError, ModelRequest
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.context_budget import ContextBudgetExceededError, ContextBudgetPlanner
from app.services.language import LanguageContextService
from app.services.persistent_memory import MemoryStoreError
from app.services.session_memory import SessionMemoryStore
from app.services.token_counter import EstimatedTokenCounter


class ChatService:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        default_model: str,
        session_memory_store: SessionMemoryStore | None = None,
        session_memory_enabled: bool = True,
        language_context_service: LanguageContextService | None = None,
        context_budget_planner: ContextBudgetPlanner | None = None,
    ) -> None:
        self._model_adapter = model_adapter
        self._default_model = default_model
        self._session_memory_store = session_memory_store
        self._session_memory_enabled = session_memory_enabled
        self._language_context_service = language_context_service
        self._context_budget_planner = context_budget_planner or ContextBudgetPlanner(
            token_counter=EstimatedTokenCounter(),
            context_window=32_768,
        )

    async def stream_completion(
        self,
        request: ChatCompletionRequest,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        session_id = request.session_id or self._new_session_id()
        assistant_parts: list[str] = []
        try:
            messages = await self._build_messages(request, session_id)
            budget = self._context_budget_planner.budget_for(request.max_tokens)
            model_request = ModelRequest(
                messages=messages,
                model=request.model or self._default_model,
                stream=True,
                temperature=request.temperature,
                max_tokens=budget.output_reserve,
                metadata=request.metadata,
            )
        except (ContextBudgetExceededError, MemoryStoreError) as exc:
            yield self._encode_sse(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )
            return

        counter = self._context_budget_planner.token_counter
        memory_messages = self._memory_messages_for_usage(request, messages)
        yield self._encode_sse(
            "context.usage",
            {
                "counter_id": counter.counter_id,
                "estimated": counter.estimated,
                "input_tokens": counter.count_messages(messages),
                "memory_tokens": counter.count_messages(memory_messages) if memory_messages else 0,
                "input_budget": budget.input_budget,
                "output_reserve": budget.output_reserve,
                "protocol_margin": budget.protocol_margin,
            },
        )

        try:
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
        except ModelProviderError as exc:
            yield self._encode_sse(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )
            return

        try:
            await self._remember_turn(
                request,
                session_id,
                "".join(assistant_parts).strip(),
                request_id,
            )
        except MemoryStoreError as exc:
            yield self._encode_sse(
                "memory.warning",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                },
            )

    async def _build_messages(
        self,
        request: ChatCompletionRequest,
        session_id: str,
    ) -> list[ChatMessage]:
        messages = list(request.messages or [])

        context_message = self._build_context_message(request)
        if context_message is not None:
            messages.append(context_message)

        operation_mode = request.metadata.get("operation_mode")
        if isinstance(operation_mode, str) and operation_mode:
            messages.append(
                ChatMessage(
                    role="system",
                    content=f"Operation mode: {operation_mode}",
                )
            )

        if request.message:
            messages.append(ChatMessage(role="user", content=request.message))

        memory_store = self._memory_store_for_request(request)
        if memory_store is not None:
            memory_budget = self._context_budget_planner.remaining_for_memory(
                messages,
                request.max_tokens,
            )
            memory_messages = await memory_store.get_messages(
                session_id,
                current_user_message=request.message or "",
                token_counter=self._context_budget_planner.token_counter,
                token_budget=memory_budget,
            )
            current_message = messages.pop()
            messages.extend(memory_messages)
            messages.append(current_message)

        self._context_budget_planner.validate(messages, request.max_tokens)
        return messages

    def _build_context_message(self, request: ChatCompletionRequest) -> ChatMessage | None:
        if not request.current_file and not request.selected_text:
            return None
        if self._language_context_service is not None:
            return self._language_context_service.build_context_message(
                current_file=request.current_file,
                selected_text=request.selected_text,
            )

        context_parts = []
        if request.current_file:
            context_parts.append(f"Current file: {request.current_file}")
        if request.selected_text:
            context_parts.append(f"Selected text:\n{request.selected_text}")
        return ChatMessage(role="system", content="\n\n".join(context_parts))

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

    def _memory_messages_for_usage(
        self,
        request: ChatCompletionRequest,
        messages: list[ChatMessage],
    ) -> list[ChatMessage]:
        if self._memory_store_for_request(request) is None:
            return []
        prefix_count = int(bool(request.current_file or request.selected_text))
        operation_mode = request.metadata.get("operation_mode")
        if isinstance(operation_mode, str) and operation_mode:
            prefix_count += 1
        return messages[prefix_count:-1]

    async def _remember_turn(
        self,
        request: ChatCompletionRequest,
        session_id: str,
        assistant_message: str,
        request_id: str | None,
    ) -> None:
        memory_store = self._memory_store_for_request(request)
        if memory_store is None or not assistant_message:
            return
        await memory_store.append_turn(
            session_id,
            request.message or "",
            assistant_message,
            token_counter=self._context_budget_planner.token_counter,
            request_id=request_id,
        )

    def _new_session_id(self) -> str:
        return f"ses_{uuid4().hex}"

    def _encode_sse(self, event: str, data: dict[str, Any]) -> str:
        compact_data = {key: value for key, value in data.items() if value is not None}
        return f"event: {event}\ndata: {json.dumps(compact_data, ensure_ascii=False)}\n\n"
