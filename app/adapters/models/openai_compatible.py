from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.adapters.models.base import ModelChunk, ModelProviderError, ModelRequest
from app.core.config import ModelProviderConfig


class OpenAICompatibleModelAdapter:
    def __init__(self, config: ModelProviderConfig) -> None:
        self._config = config

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        payload = self._build_payload(request)
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        timeout = httpx.Timeout(self._config.timeout_seconds)
        url = (
            f"{self._config.base_url.rstrip('/')}/chat/completions"
            if self._config.base_url
            else ""
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ModelProviderError(
                            "MODEL_PROVIDER_ERROR",
                            f"Model provider returned HTTP {response.status_code}: {body[:200]!r}",
                        )

                    yield ModelChunk(type="message.start", raw={"model": payload["model"]})
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
        payload: dict[str, Any] = {
            "model": request.model or self._config.name,
            "messages": [
                message.model_dump(exclude_none=True) for message in request.messages
            ],
            "stream": True,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
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

        choice = payload.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        content = delta.get("content")
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
        if finish_reason:
            return ModelChunk(type="message.done", finish_reason=finish_reason, raw=payload)
        return None
