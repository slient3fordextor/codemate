import json
from collections.abc import AsyncIterator

from app.adapters.models.base import ModelChunk, ModelRequest


class MockModelAdapter:
    supports_tools = False

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield ModelChunk(type="message.start", raw={"model": request.model or "mock-model"})

        user_message = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        agent_mode = any(
            message.role == "system" and "coding agent" in message.content
            for message in request.messages
        )
        tool_result = next(
            (
                message.content
                for message in reversed(request.messages)
                if message.name == "tool_result"
            ),
            None,
        )
        if agent_mode and tool_result is None:
            content = json.dumps({"type": "tool", "tool": "list_files", "arguments": {"path": "."}})
        elif agent_mode:
            content = json.dumps(
                {
                    "type": "final",
                    "answer": (
                        "Mock agent completed a read-only workspace inspection. "
                        "Configure a real model for evidence-based analysis."
                    ),
                }
            )
        else:
            content = f"Mock response: {user_message}"
        for word in content.split(" "):
            yield ModelChunk(type="message.delta", content=f"{word} ")

        yield ModelChunk(
            type="usage.update",
            input_tokens=sum(len(message.content.split()) for message in request.messages),
            output_tokens=len(content.split()),
        )
        yield ModelChunk(type="message.done", finish_reason="stop")
