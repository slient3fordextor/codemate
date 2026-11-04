import asyncio
from typing import Any

from fastapi import APIRouter, Query

from app.agent.tools.readonly import ReadonlyToolError
from app.core.config import get_settings
from app.core.errors import AppError
from app.services.project_context import ProjectContext

router = APIRouter(prefix="/project")


@router.get("/{operation}")
async def project_read(
    operation: str, path: str = Query(default=".", max_length=1024)
) -> dict[str, Any]:
    settings = get_settings()
    context = ProjectContext(
        settings.workspace.root,
        settings.language_support.context_max_bytes,
        settings.language_support.context_max_files,
    )
    try:
        if operation == "files":
            return await asyncio.to_thread(context.files, path)
        if operation == "file":
            return await asyncio.to_thread(context.read, path)
        if operation == "diff":
            return await asyncio.to_thread(context.diff)
        raise AppError("NOT_FOUND", "Project operation not found", 404)
    except (ReadonlyToolError, OSError) as exc:
        raise AppError("PROJECT_CONTEXT_REJECTED", str(exc), 400) from exc
