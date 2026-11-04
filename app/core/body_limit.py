import asyncio
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Bound JSON bodies before framework parsing, including chunked requests."""

    def __init__(self, app: ASGIApp, max_bytes: int = 524288, timeout_seconds: float = 30) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PATCH", "PUT"}:
            await self.app(scope, receive, send)
            return
        chunks = []
        size = 0
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while True:
            try:
                message = await asyncio.wait_for(
                    receive(), max(0, deadline - asyncio.get_running_loop().time())
                )
            except TimeoutError:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 408,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": (
                            b'{"error":{"code":"BODY_TIMEOUT","message":"Request body timed out"}}'
                        ),
                    }
                )
                return
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                body = json.dumps(
                    {"error": {"code": "BODY_TOO_LARGE", "message": "Request body exceeds 512 KiB"}}
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)
