from collections.abc import AsyncIterator

from app.adapters.models.base import ModelChunk, ModelRequest


class MockModelAdapter:
    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield ModelChunk(type="message.start", raw={"model": request.model or "mock-model"})

        user_message = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        content = f"Mock response: {user_message}"
        for word in content.split(" "):
            yield ModelChunk(type="message.delta", content=f"{word} ")

        yield ModelChunk(
            type="usage.update",
            input_tokens=sum(len(message.content.split()) for message in request.messages),
            output_tokens=len(content.split()),
        )
        yield ModelChunk(type="message.done", finish_reason="stop")
