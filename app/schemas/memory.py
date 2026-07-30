from typing import Literal

from pydantic import BaseModel


class MemoryCapabilitiesResponse(BaseModel):
    backend: str
    layers: list[str]
    persistent: bool
    cross_process: bool
    preference_management: bool


class MemoryPreferenceResponse(BaseModel):
    id: int
    scope: Literal["global", "project", "temporary"]
    category: str
    key: str
    value: str
    content: str
    active: bool
    updated_at: str


class MemoryOperationResponse(BaseModel):
    status: Literal["ok", "not_found"]


class MemoryConflictResponse(BaseModel):
    id: int
    left_preference_id: int
    right_preference_id: int
    relation: str
    confidence: float
    resolution: str
    resolved_by: str
    created_at: str
    resolved_at: str | None
