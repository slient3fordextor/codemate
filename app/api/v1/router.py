from fastapi import APIRouter

from app.api.v1 import chat, health, model_config

api_router = APIRouter()
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(model_config.router, tags=["model-config"])
