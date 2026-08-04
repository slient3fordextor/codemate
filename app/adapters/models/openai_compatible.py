import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx

from app.adapters.models.base import ModelChunk, ModelProviderError, ModelRequest
from app.core.config import ModelProviderConfig


class OpenAICompatibleModelAdapter:
    supports_tools = True

    def __init__(self, config: ModelProviderConfig) -> None:
        self._config = config

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        payload = self._build_payload(request)
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        timeout = httpx.Timeout(self._config.timeout_seconds)
        url = (
            f"{self._config.base_url.rstrip('/')}/chat/completions" if self._config.base_url else ""
        )

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            for attempt in range(self._config.max_retries + 1):
                emitted = False
                try:
                    async for chunk in self._stream_attempt(client, url, headers, payload):
                        emitted = True
                        yield chunk
                    return
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    if emitted or attempt >= self._config.max_retries:
                        self._raise_transport_error(exc)
                    await asyncio.sleep(min(0.25 * (2**attempt), 2.0))

    async def _stream_attempt(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> AsyncIterator[ModelChunk]:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Retryable model provider response",
                        request=response.request,
                        response=response,
                    )
                raise ModelProviderError(
                    "MODEL_PROVIDER_ERROR",
                    f"Model provider returned HTTP {response.status_code}: {body[:200]!r}",
                )

            yield ModelChunk(type="message.start", raw={"model": payload["model"]})
            async for line in response.aiter_lines():
                chunk = self._parse_sse_line(line)
                if chunk is not None:
                    yield chunk

    def _raise_transport_error(self, exc: httpx.HTTPError) -> None:
        if isinstance(exc, httpx.TimeoutException):
            raise ModelProviderError(
                "MODEL_PROVIDER_TIMEOUT",
                "Model provider request timed out",
            ) from exc
        raise ModelProviderError(
            "MODEL_PROVIDER_ERROR",
            "Model provider request failed",
        ) from exc

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self._config.name,
            "messages": [message.model_dump(exclude_none=True) for message in request.messages],
            "stream": True,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        return payload

    def _parse_sse_line(self, line: str) -> ModelChunk | None:
        if not line.startswith("data: "):
            return None

        data = line.removeprefix("data: ").strip()
        if not data or data == "[DONE]":
            return ModelChunk(type="message.done", finish_reason="stop")

        try:
            payload = httpx.Response(200, content=data).json()
        except ValueError:
            return None

        choices = payload.get("choices") or [{}]
        choice = choices[0]
        delta = choice.get("delta", {})
        content = delta.get("content")
        tool_calls = delta.get("tool_calls") or []
        finish_reason = choice.get("finish_reason")
        usage = payload.get("usage")

        if usage:
            return ModelChunk(
                type="usage.update",
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                raw=payload,
            )
        if content:
            return ModelChunk(type="message.delta", content=content, raw=payload)
        if tool_calls:
            tool_call = tool_calls[0]
            function = tool_call.get("function") or {}
            chunk_type: Literal["tool.call.start", "tool.call.delta"] = (
                "tool.call.start" if tool_call.get("id") else "tool.call.delta"
            )
            return ModelChunk(
                type=chunk_type,
                tool_call_id=tool_call.get("id"),
                tool_name=function.get("name"),
                tool_arguments=function.get("arguments"),
                raw=payload,
            )
        if finish_reason:
            return ModelChunk(type="message.done", finish_reason=finish_reason, raw=payload)
        return None
