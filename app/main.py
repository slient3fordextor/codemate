from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.health import health_check
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware, access_log_middleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging.level)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.middleware("http")(access_log_middleware)
    register_exception_handlers(app)
    app.add_api_route("/health", health_check, methods=["GET"], tags=["health"])
    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
