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
from app.services.persistent_memory import SQLiteSessionMemoryStore
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
    if settings.session_memory.backend == "sqlite":
        app.state.session_memory_store = SQLiteSessionMemoryStore(
            database_path=settings.session_memory.database_path,
            project_root=settings.workspace.root,
            max_turns=settings.session_memory.max_turns,
            max_sessions=settings.session_memory.max_sessions,
            medium_term_enabled=settings.session_memory.medium_term_enabled,
            medium_term_max_tokens=settings.session_memory.medium_term_max_tokens,
            long_term_enabled=settings.session_memory.long_term_enabled,
            long_term_max_items=settings.session_memory.long_term_max_items,
            sensitive_content_detection_enabled=(
                settings.security.enable_sensitive_content_detection
            ),
        )
    else:
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
