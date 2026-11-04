import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any, cast

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.adapters.models import build_model_adapter
from app.adapters.models.base import ModelChunk, ModelProviderError, ModelRequest
from app.agent.persistence import SQLiteAgentStore, default_agent_state_path
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.chat import ChatMessage
from app.schemas.model_config import ModelConfigUpdateRequest
from app.services.chat_capacity import chat_lease
from app.services.model_profiles import ModelProfiles

router = APIRouter(prefix="/model-profiles")


class ProfileChanges(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    model: ModelConfigUpdateRequest

    @model_validator(mode="after")
    def bounded(self) -> "ProfileChanges":
        if len(json.dumps(self.model.model_dump()).encode()) > 8192:
            raise ValueError("Model profile exceeds its size limit")
        return self


class ConnectionCheck(BaseModel):
    acknowledged_cost: bool = False


def profiles() -> ModelProfiles:
    return ModelProfiles(SQLiteAgentStore(default_agent_state_path(get_settings().workspace.root)))


@router.get("")
async def list_profiles() -> list[dict[str, Any]]:
    return await asyncio.to_thread(lambda: profiles().list())


@router.post("")
async def create_profile(changes: ProfileChanges) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(lambda: profiles().save(changes.label, changes.model))
    except ValueError as exc:
        raise AppError("PROFILE_INVALID", "Invalid model profile configuration", 422) from exc


@router.put("/{identity}")
async def update_profile(identity: str, changes: ProfileChanges) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            lambda: profiles().save(changes.label, changes.model, identity)
        )
    except ValueError as exc:
        raise AppError("PROFILE_INVALID", "Invalid model profile configuration", 422) from exc


@router.delete("/{identity}")
async def delete_profile(identity: str) -> dict[str, str]:
    await asyncio.to_thread(lambda: profiles().delete(identity))
    return {"status": "deleted"}


@router.post("/{identity}/check")
async def check_profile(identity: str, check: ConnectionCheck) -> dict[str, Any]:
    if not check.acknowledged_cost:
        raise AppError(
            "CHECK_REQUIRES_CONFIRMATION", "Confirm that this check may incur usage", 400
        )
    config, _ = await asyncio.to_thread(lambda: profiles().get(identity))
    config = config.model_copy(
        update={"timeout_seconds": min(config.timeout_seconds, 15), "max_retries": 0}
    )
    settings = get_settings()
    with chat_lease(
        settings.session_memory.database_path.parent / "chat-locks",
        f"profile-check:{identity}",
        settings.chat_max_concurrency,
    ):
        try:
            model = build_model_adapter(config)
            done = False
            usage: dict[str, int] = {}
            async with asyncio.timeout(15):
                async with aclosing(
                    cast(
                        AsyncGenerator[ModelChunk, None],
                        model.stream_chat(
                            ModelRequest(
                                messages=[ChatMessage(role="user", content="Reply OK")],
                                model=config.name,
                                stream=True,
                                max_tokens=8,
                            )
                        ),
                    )
                ) as upstream:
                    async for chunk in upstream:
                        done |= chunk.type == "message.done"
                        if chunk.input_tokens is not None:
                            usage["input_tokens"] = chunk.input_tokens
                        if chunk.output_tokens is not None:
                            usage["output_tokens"] = chunk.output_tokens
            if not done:
                raise AppError("INCOMPLETE_STREAM", "Connection check did not complete", 502)
            return {
                "connected": True,
                "model": config.name,
                "provider": config.provider,
                "supports_tools": bool(getattr(model, "supports_tools", False)),
                "usage": usage,
            }
        except (ModelProviderError, ValueError, TimeoutError) as exc:
            raise AppError("MODEL_CHECK_FAILED", "Model connection check failed", 502) from exc
