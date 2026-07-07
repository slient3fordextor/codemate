from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.adapters.models import build_model_adapter
from app.core.config import get_settings
from app.schemas.chat import ChatCompletionRequest
from app.services.chat import ChatService

router = APIRouter(prefix="/chat")


@router.post("/completions")
async def create_chat_completion(
    request_body: ChatCompletionRequest,
    request: Request,
) -> StreamingResponse:
    settings = get_settings()
    service = ChatService(
        model_adapter=build_model_adapter(settings.model),
        default_model=settings.model.name,
        session_memory_store=request.app.state.session_memory_store,
        session_memory_enabled=settings.session_memory.enabled,
    )
    request_id = getattr(request.state, "request_id", None)

    return StreamingResponse(
        service.stream_completion(request_body, request_id=str(request_id) if request_id else None),
        media_type="text/event-stream",
    )
