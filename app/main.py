from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.health import build_health_response
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware, access_log_middleware
from app.services.session_memory import InMemorySessionMemoryStore


async def root_health_check() -> object:
    return build_health_response()


def web_index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.logging.level)

    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
    )
    app.state.session_memory_store = InMemorySessionMemoryStore(
        max_turns=settings.session_memory.max_turns,
        max_sessions=settings.session_memory.max_sessions,
    )
    app.add_middleware(RequestIdMiddleware)
    app.middleware("http")(access_log_middleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.add_api_route("/", web_index, methods=["GET"], tags=["web"])
    app.add_api_route("/health", root_health_check, methods=["GET"], tags=["health"])

    return app


app = create_app()
