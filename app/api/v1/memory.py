from typing import cast

from fastapi import APIRouter, Request

from app.schemas.memory import (
    MemoryCapabilitiesResponse,
    MemoryConflictResponse,
    MemoryOperationResponse,
    MemoryPreferenceResponse,
)
from app.services.memory_conflicts import LongTermPreference, MemoryConflictAudit
from app.services.session_memory import SessionMemoryStore

router = APIRouter(prefix="/memory")


def _store(request: Request) -> SessionMemoryStore:
    return cast(SessionMemoryStore, request.app.state.session_memory_store)


@router.get("/capabilities", response_model=MemoryCapabilitiesResponse)
async def get_memory_capabilities(request: Request) -> MemoryCapabilitiesResponse:
    capabilities = _store(request).capabilities
    return MemoryCapabilitiesResponse(
        backend=capabilities.backend,
        layers=list(capabilities.layers),
        persistent=capabilities.persistent,
        cross_process=capabilities.cross_process,
        preference_management=capabilities.preference_management,
    )


@router.get("/preferences", response_model=list[MemoryPreferenceResponse])
async def list_memory_preferences(request: Request) -> list[MemoryPreferenceResponse]:
    preferences = await _store(request).list_preferences()
    return [_preference_response(preference) for preference in preferences]


@router.get("/conflicts", response_model=list[MemoryConflictResponse])
async def list_memory_conflicts(request: Request) -> list[MemoryConflictResponse]:
    conflicts = await _store(request).list_conflicts()
    return [_conflict_response(conflict) for conflict in conflicts]


@router.delete(
    "/preferences/{preference_id}",
    response_model=MemoryOperationResponse,
)
async def delete_memory_preference(
    preference_id: int,
    request: Request,
) -> MemoryOperationResponse:
    deleted = await _store(request).delete_preference(preference_id)
    return MemoryOperationResponse(status="ok" if deleted else "not_found")


@router.delete("/sessions/{session_id}", response_model=MemoryOperationResponse)
async def delete_memory_session(
    session_id: str,
    request: Request,
) -> MemoryOperationResponse:
    await _store(request).delete_session(session_id)
    return MemoryOperationResponse(status="ok")


@router.delete("/project", response_model=MemoryOperationResponse)
async def clear_project_memory(request: Request) -> MemoryOperationResponse:
    await _store(request).clear_project()
    return MemoryOperationResponse(status="ok")


@router.delete("/global-preferences", response_model=MemoryOperationResponse)
async def clear_global_memory_preferences(request: Request) -> MemoryOperationResponse:
    await _store(request).clear_global_preferences()
    return MemoryOperationResponse(status="ok")


def _preference_response(preference: object) -> MemoryPreferenceResponse:
    if not isinstance(preference, LongTermPreference):
        raise TypeError("Memory backend returned an unsupported preference type")
    if preference.preference_id is None:
        raise ValueError("Stored preference is missing its identifier")
    return MemoryPreferenceResponse(
        id=preference.preference_id,
        scope=preference.scope,
        category=preference.category,
        key=preference.key,
        value=preference.value,
        content=preference.content,
        active=preference.active,
        updated_at=preference.updated_at,
    )


def _conflict_response(conflict: object) -> MemoryConflictResponse:
    if not isinstance(conflict, MemoryConflictAudit):
        raise TypeError("Memory backend returned an unsupported conflict type")
    return MemoryConflictResponse(
        id=conflict.conflict_id,
        left_preference_id=conflict.left_preference_id,
        right_preference_id=conflict.right_preference_id,
        relation=conflict.relation,
        confidence=conflict.confidence,
        resolution=conflict.resolution,
        resolved_by=conflict.resolved_by,
        created_at=conflict.created_at,
        resolved_at=conflict.resolved_at,
    )
