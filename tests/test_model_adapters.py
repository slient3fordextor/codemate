from collections.abc import AsyncIterator

import httpx
import pytest

from app.adapters.models.anthropic import AnthropicClaudeModelAdapter
from app.adapters.models.base import ModelChunk, ModelRequest
from app.adapters.models.factory import build_model_adapter
from app.adapters.models.openai_compatible import OpenAICompatibleModelAdapter
from app.core.config import ModelProviderConfig
from app.schemas.chat import ChatMessage


def test_factory_builds_anthropic_adapter() -> None:
    config = ModelProviderConfig(provider="anthropic", name="claude-3-5-sonnet-latest")

    adapter = build_model_adapter(config)

    assert isinstance(adapter, AnthropicClaudeModelAdapter)


def test_factory_maps_domestic_providers_to_openai_compatible_adapter() -> None:
    for provider in ("deepseek", "qwen", "zhipu", "moonshot", "baichuan"):
        config = ModelProviderConfig(
            provider=provider,
            base_url="https://example.com/v1",
            name="test-model",
        )

        adapter = build_model_adapter(config)

        assert isinstance(adapter, OpenAICompatibleModelAdapter)


def test_anthropic_payload_moves_system_messages_to_system_field() -> None:
    adapter = AnthropicClaudeModelAdapter(
        ModelProviderConfig(provider="anthropic", name="claude-3-5-sonnet-latest")
    )
    request = ModelRequest(
        messages=[
            ChatMessage(role="system", content="你是代码助手"),
            ChatMessage(role="user", content="解释这个函数"),
        ],
        model="claude-3-5-sonnet-latest",
        max_tokens=2048,
        temperature=0.2,
    )

    payload = adapter._build_payload(request)

    assert payload == {
        "model": "claude-3-5-sonnet-latest",
        "messages": [{"role": "user", "content": "解释这个函数"}],
        "stream": True,
        "max_tokens": 2048,
        "system": "你是代码助手",
        "temperature": 0.2,
    }


def test_anthropic_stream_events_are_normalized_to_model_chunks() -> None:
    adapter = AnthropicClaudeModelAdapter(
        ModelProviderConfig(provider="anthropic", name="claude-3-5-sonnet-latest")
    )

    start = adapter._parse_sse_line(
        'data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}'
    )
    delta = adapter._parse_sse_line(
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你好"}}'
    )
    usage = adapter._parse_sse_line(
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":8}}'
    )
    done = adapter._parse_sse_line('data: {"type":"message_stop"}')

    assert start is not None
    assert start.type == "message.start"
    assert start.input_tokens == 12
    assert delta is not None
    assert delta.type == "message.delta"
    assert delta.content == "你好"
    assert usage is not None
    assert usage.type == "usage.update"
    assert usage.output_tokens == 8
    assert usage.finish_reason == "end_turn"
    assert done is not None
    assert done.type == "message.done"


def test_anthropic_error_event_raises_provider_error() -> None:
    adapter = AnthropicClaudeModelAdapter(
        ModelProviderConfig(provider="anthropic", name="claude-3-5-sonnet-latest")
    )

    with pytest.raises(Exception, match="bad request"):
        adapter._parse_sse_line(
            'data: {"type":"error","error":{"type":"invalid_request_error",'
            '"message":"bad request"}}'
        )


@pytest.mark.asyncio
async def test_openai_adapter_retries_before_stream_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenAICompatibleModelAdapter(
        ModelProviderConfig(
            provider="openai_compatible",
            base_url="https://api.example.com/v1",
            name="test-model",
            max_retries=2,
        )
    )
    attempts = 0

    async def stream_attempt(*args: object, **kwargs: object) -> AsyncIterator[ModelChunk]:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary failure")
        yield ModelChunk(type="message.start")
        yield ModelChunk(type="message.done", finish_reason="stop")

    monkeypatch.setattr(adapter, "_stream_attempt", stream_attempt)
    request = ModelRequest(messages=[ChatMessage(role="user", content="hello")])

    chunks = [chunk async for chunk in adapter.stream_chat(request)]

    assert attempts == 3
    assert [chunk.type for chunk in chunks] == ["message.start", "message.done"]
