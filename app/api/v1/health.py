from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.health import HealthResponse, VersionResponse

router = APIRouter()


def build_health_response() -> HealthResponse:
    settings = get_settings()
    model_configured = settings.model.provider == "mock" or bool(settings.model.api_key)
    return HealthResponse(
        status="ok",
        app=settings.app.name,
        version=settings.app.version,
        checks={
            "config": "ok",
            "storage": "enabled" if settings.storage.enabled else "disabled",
            "model": "configured" if model_configured else "not_configured",
        },
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return build_health_response()


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    settings = get_settings()
    return VersionResponse(
        name=settings.app.name,
        version=settings.app.version,
        api_version="v1",
    )
