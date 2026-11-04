from fastapi import APIRouter

from app.api.v1 import (
    agent,
    chat,
    computer,
    health,
    memory,
    model_config,
    model_profiles,
    project,
    sessions,
)

api_router = APIRouter()
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(computer.router, tags=["computer"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(model_config.router, tags=["model-config"])
api_router.include_router(memory.router, tags=["memory"])
api_router.include_router(sessions.router, tags=["sessions"])
api_router.include_router(project.router, tags=["project"])
api_router.include_router(agent.router, tags=["agent"])
api_router.include_router(model_profiles.router, tags=["model-profiles"])
