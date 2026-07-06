from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.adapters.models.base import ModelChunk, ModelProviderError, ModelRequest
from app.core.config import ModelProviderConfig


class AnthropicClaudeModelAdapter:
    def __init__(self, config: ModelProviderConfig) -> None:
        self._config = config

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        payload = self._build_payload(request)
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._config.api_key:
            headers["x-api-key"] = self._config.api_key

        timeout = httpx.Timeout(self._config.timeout_seconds)
        base_url = self._config.base_url or "https://api.anthropic.com/v1"
        url = f"{base_url.rstrip('/')}/messages"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ModelProviderError(
                            "MODEL_PROVIDER_ERROR",
                            f"Model provider returned HTTP {response.status_code}: {body[:200]!r}",
                        )

                    async for line in response.aiter_lines():
                        chunk = self._parse_sse_line(line)
                        if chunk is not None:
                            yield chunk
        except httpx.TimeoutException as exc:
            raise ModelProviderError(
                "MODEL_PROVIDER_TIMEOUT",
                "Model provider request timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError(
                "MODEL_PROVIDER_ERROR",
                "Model provider request failed",
            ) from exc

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []

        for message in request.messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue

            role = "assistant" if message.role == "assistant" else "user"
            messages.append({"role": role, "content": message.content})

        payload: dict[str, Any] = {
            "model": request.model or self._config.name,
            "messages": messages,
            "stream": True,
            "max_tokens": request.max_tokens or 1024,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload

    def _parse_sse_line(self, line: str) -> ModelChunk | None:
        if not line.startswith("data: "):
            return None

        data = line.removeprefix("data: ").strip()
        if not data:
            return None

        try:
            payload = httpx.Response(200, content=data).json()
        except ValueError:
            return None

        event_type = payload.get("type")
        if event_type == "message_start":
            usage = payload.get("message", {}).get("usage", {})
            return ModelChunk(
                type="message.start",
                input_tokens=usage.get("input_tokens"),
                raw=payload,
            )

        if event_type == "content_block_delta":
            delta = payload.get("delta", {})
            text = delta.get("text")
            if text:
                return ModelChunk(type="message.delta", content=text, raw=payload)

        if event_type == "message_delta":
            usage = payload.get("usage", {})
            if usage:
                return ModelChunk(
                    type="usage.update",
                    output_tokens=usage.get("output_tokens"),
                    finish_reason=payload.get("delta", {}).get("stop_reason"),
                    raw=payload,
                )

        if event_type == "message_stop":
            return ModelChunk(type="message.done", finish_reason="stop", raw=payload)

        if event_type == "error":
            error = payload.get("error", {})
            raise ModelProviderError(
                str(error.get("type") or "MODEL_PROVIDER_ERROR"),
                str(error.get("message") or "Model provider returned an error"),
            )

        return None
