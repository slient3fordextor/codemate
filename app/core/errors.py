import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from app.schemas.errors import ErrorDetail, ErrorResponse
from app.services.persistent_memory import MemoryStoreError

logger = logging.getLogger("codemate.errors")

# 自定义异常类
class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def _error_response(
    code: str,
    message: str,
    request: Request,
    status_code: int,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=_request_id(request),
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(MemoryStoreError)
    async def memory_store_error_handler(
        request: Request,
        exc: MemoryStoreError,
    ) -> JSONResponse:
        return _error_response(
            exc.code,
            exc.message,
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.code, exc.message, request, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            "VALIDATION_ERROR",
            "Request validation failed",
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled request error",
            extra={
                "request_id": _request_id(request),
                "method": request.method,
                "path": request.url.path,
            },
        )
        return _error_response(
            "INTERNAL_ERROR",
            "Internal server error",
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
