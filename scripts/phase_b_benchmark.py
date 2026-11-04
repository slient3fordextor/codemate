"""Offline chat-capacity baseline using the real HTTP app and a delayed mock."""

import asyncio
import json
import os
import statistics
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import httpx

from app.adapters.models.base import ModelChunk, ModelRequest
from app.adapters.models.mock import MockModelAdapter
from app.core.config import get_settings
from app.main import create_app


class DelayedMock(MockModelAdapter):
    active = 0
    peak = 0

    async def stream_chat(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.2)
            async for chunk in super().stream_chat(request):
                yield chunk
        finally:
            self.active -= 1


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="codemate-capacity-") as temporary:
        environment = {
            "MODEL_PROVIDER": "mock",
            "WORKSPACE_ROOT": temporary,
            "SESSION_MEMORY_DATABASE_PATH": str(Path(temporary) / "memory.sqlite3"),
            "CHAT_MAX_CONCURRENCY": "4",
            "ALLOW_REMOTE_API": "false",
            "LOG_LEVEL": "WARNING",
        }
        with patch.dict(os.environ, environment):
            get_settings.cache_clear()
            try:
                app = create_app()
                transport = httpx.ASGITransport(app=app)
                results = []
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://127.0.0.1"
                ) as client:
                    for concurrency in (1, 4, 8):
                        model = DelayedMock()
                        gate = asyncio.Semaphore(concurrency)

                        async def send(gate: asyncio.Semaphore = gate) -> tuple[int, float]:
                            async with gate:
                                started = time.monotonic()
                                response = await client.post(
                                    "/api/v1/chat/completions", json={"message": "capacity probe"}
                                )
                                return response.status_code, (time.monotonic() - started) * 1000

                        with patch("app.api.v1.chat.build_model_adapter", return_value=model):
                            samples = await asyncio.gather(*(send() for _ in range(8)))
                        durations = sorted(duration for code, duration in samples if code == 200)
                        results.append(
                            {
                                "concurrency": concurrency,
                                "requests": len(samples),
                                "accepted": sum(code == 200 for code, _ in samples),
                                "overloaded": sum(code == 429 for code, _ in samples),
                                "peak_model_streams": model.peak,
                                "accepted_median_ms": round(statistics.median(durations)),
                                "accepted_max_ms": round(max(durations)),
                            }
                        )
                        assert model.peak <= 4 and model.active == 0
                        assert all(code in {200, 429} for code, _ in samples)
                print(json.dumps(results, indent=2))
            finally:
                get_settings.cache_clear()


if __name__ == "__main__":
    asyncio.run(main())
