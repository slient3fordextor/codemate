from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.adapters.models import build_model_adapter
from app.core.config import get_settings
from app.schemas.chat import ChatCompletionRequest
from app.services.chat import ChatService
from app.services.context_budget import ContextBudgetPlanner
from app.services.language.factory import build_language_context_service
from app.services.language.interfaces import LanguageContextConfig
from app.services.token_counter import build_token_counter

router = APIRouter(prefix="/chat")


@router.post("/completions", response_model=None)
async def create_chat_completion(
    request_body: ChatCompletionRequest,
    request: Request,
) -> StreamingResponse | JSONResponse:
    settings = get_settings()
    model_name = request_body.model or settings.model.name
    service = ChatService(
        model_adapter=build_model_adapter(settings.model),
        default_model=settings.model.name,
        session_memory_store=request.app.state.session_memory_store,
        session_memory_enabled=settings.session_memory.enabled,
        language_context_service=build_language_context_service(
            settings.workspace.root,
            LanguageContextConfig(
                enabled=settings.language_support.enabled,
                default_level=settings.language_support.default_level,
                max_files=settings.language_support.context_max_files,
                max_bytes=settings.language_support.context_max_bytes,
                enable_tree_sitter=settings.language_support.enable_tree_sitter,
                enable_lsp=settings.language_support.enable_lsp,
            ),
        ),
        context_budget_planner=ContextBudgetPlanner(
            token_counter=build_token_counter(settings.model.provider, model_name),
            context_window=settings.model.context_window,
        ),
    )
    request_id = getattr(request.state, "request_id", None)

    if not request_body.stream:
        response = await service.complete_completion(
            request_body,
            request_id=str(request_id) if request_id else None,
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))

    return StreamingResponse(
        service.stream_completion(
            request_body,
            request_id=str(request_id) if request_id else None,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
    )
