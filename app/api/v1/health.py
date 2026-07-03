from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.health import HealthResponse, VersionResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        checks={
            "config": "ok",
            "storage": "disabled",
            "model": "not_configured" if not settings.model_api_key else "configured",
        },
    )


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    settings = get_settings()
    return VersionResponse(
        name=settings.app_name,
        version=settings.app_version,
        api_version="v1",
    )
