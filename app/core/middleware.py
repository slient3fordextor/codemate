import asyncio
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from ipaddress import ip_address
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.core.rate_limit import SharedRateLimitError, SQLiteFixedWindowRateLimiter

logger = logging.getLogger("codemate.access")


class APIAccessMiddleware(BaseHTTPMiddleware):
    """Keeps the local control plane local unless token-authenticated remote access is enabled."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        settings = get_settings()
        client_host = request.client.host if request.client is not None else ""
        request_host = request.url.hostname or ""
        if self._is_loopback(client_host) and self._is_trusted_local_host(request_host):
            return await call_next(request)
        if settings.security.allow_remote_api and self._valid_token(
            request,
            settings.security.remote_api_token,
        ):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "REMOTE_API_FORBIDDEN",
                    "message": "CodeMate API access is restricted to the local machine",
                }
            },
        )

    def _is_loopback(self, host: str) -> bool:
        if host == "testclient":
            return True
        try:
            return ip_address(host).is_loopback
        except ValueError:
            return host == "localhost"

    def _is_trusted_local_host(self, host: str) -> bool:
        return host == "testserver" or self._is_loopback(host)

    def _valid_token(self, request: Request, expected: str | None) -> bool:
        if not expected:
            return False
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        return scheme.casefold() == "bearer" and secrets.compare_digest(supplied, expected)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Process-local fixed-window protection for API requests."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._shared_path: str | None = None
        self._shared_limiter: SQLiteFixedWindowRateLimiter | None = None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        settings = get_settings()
        if not settings.security.enable_rate_limit or not request.url.path.startswith("/api/"):
            return await call_next(request)
        key = request.client.host if request.client is not None else "unknown"
        shared_path = settings.security.rate_limit_database_path
        if shared_path is not None:
            try:
                if self._shared_path != str(shared_path):
                    self._shared_limiter = SQLiteFixedWindowRateLimiter(shared_path)
                    self._shared_path = str(shared_path)
                if self._shared_limiter is None:
                    raise SharedRateLimitError("Shared rate limiter was not initialized")
                allowed = await asyncio.to_thread(
                    self._shared_limiter.allow,
                    key,
                    time.time(),
                    settings.security.rate_limit_per_minute,
                )
            except SharedRateLimitError:
                logger.exception("shared rate-limit storage unavailable")
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "RATE_LIMIT_STORAGE_UNAVAILABLE",
                            "message": "Shared rate-limit storage is unavailable",
                        }
                    },
                )
            if not allowed:
                return self._limited_response()
            return await call_next(request)

        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            window = self._requests[key]
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= settings.security.rate_limit_per_minute:
                return self._limited_response()
            window.append(now)
        return await call_next(request)

    @staticmethod
    def _limited_response() -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many API requests",
                }
            },
            headers={"Retry-After": "60"},
        )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


async def access_log_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started_at = time.perf_counter()
    response = await call_next(request)
    settings = get_settings()
    if not settings.logging.access_log_enabled:
        return response

    if isinstance(response, StreamingResponse):
        iterator = response.body_iterator

        async def logged_iterator() -> AsyncIterator[str | bytes | memoryview]:
            try:
                async for chunk in iterator:
                    yield chunk
            finally:
                _write_access_log(request, response, started_at)

        response.body_iterator = logged_iterator()
        return response
    _write_access_log(request, response, started_at)
    return response


def _write_access_log(request: Request, response: Response, started_at: float) -> None:
    latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    logger.info(
        "request completed",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
