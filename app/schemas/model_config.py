from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import ModelProviderName


class ModelConfigResponse(BaseModel):
    provider: ModelProviderName
    base_url: str | None = None
    name: str
    api_key_set: bool = False
    note: str | None = None
    timeout_seconds: float
    max_retries: int


class ModelConfigUpdateRequest(BaseModel):
    provider: ModelProviderName
    base_url: str | None = None
    name: str = Field(min_length=1)
    api_key: str | None = None
    note: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    api_key_mode: Literal["preserve", "replace", "clear"] = "preserve"
