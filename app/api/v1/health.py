from fastapi import APIRouter, Request, Response, status

from app.adapters.models import build_model_adapter
from app.adapters.models.base import ModelProviderError, ModelRequest
from app.core.config import get_settings
from app.schemas.chat import ChatMessage
from app.schemas.health import HealthResponse, VersionResponse
from app.services.persistent_memory import MemoryStoreError
from app.services.session_memory import SessionMemoryStore

router = APIRouter()


async def build_health_response(
    memory_store: SessionMemoryStore | None = None,
    *,
    probe_model: bool = False,
) -> HealthResponse:
    settings = get_settings()
    model_configured = settings.model.provider in {"mock", "ollama"} or bool(settings.model.api_key)
    memory_status = "disabled"
    overall_status = "ok"
    if settings.session_memory.enabled and memory_store is not None:
        try:
            await memory_store.ping()
            memory_status = "ok"
        except MemoryStoreError:
            memory_status = "unavailable"
            overall_status = "degraded"
    model_status = "configured" if model_configured else "not_configured"
    if probe_model and model_configured:
        try:
            request = ModelRequest(
                messages=[ChatMessage(role="user", content="Reply with OK.")],
                model=settings.model.name,
                max_tokens=1,
            )
            async for _chunk in build_model_adapter(settings.model).stream_chat(request):
                pass
            model_status = "ok"
        except ModelProviderError:
            model_status = "unavailable"
            overall_status = "degraded"
    return HealthResponse(
        status=overall_status,
        app=settings.app.name,
        version=settings.app.version,
        checks={
            "config": "ok",
            "storage": "enabled" if settings.storage.enabled else "disabled",
            "model": model_status,
            "session_memory": memory_status,
        },
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(
    request: Request,
    response: Response,
    probe_model: bool = False,
) -> HealthResponse:
    result = await build_health_response(
        request.app.state.session_memory_store,
        probe_model=probe_model,
    )
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    settings = get_settings()
    return VersionResponse(
        name=settings.app.name,
        version=settings.app.version,
        api_version="v1",
    )
