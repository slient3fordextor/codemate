import asyncio
import fcntl
import os
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.model_config import ModelConfigResponse, ModelConfigUpdateRequest

router = APIRouter(prefix="/model-config")

MODEL_ENV_KEYS = {
    "MODEL_PROVIDER",
    "MODEL_BASE_URL",
    "MODEL_NAME",
    "MODEL_API_KEY",
    "MODEL_NOTE",
    "MODEL_TIMEOUT_SECONDS",
    "MODEL_MAX_RETRIES",
    "MODEL_CONTEXT_WINDOW",
}
_ENV_WRITE_LOCK = threading.Lock()


@router.get("", response_model=ModelConfigResponse)
async def get_model_config() -> ModelConfigResponse:
    settings = get_settings()
    model = settings.model
    return ModelConfigResponse(
        provider=model.provider,
        base_url=model.base_url,
        name=model.name,
        api_key_set=bool(model.api_key),
        note=model.note,
        timeout_seconds=model.timeout_seconds,
        max_retries=model.max_retries,
        context_window=model.context_window,
    )


@router.put("", response_model=ModelConfigResponse)
async def update_model_config(request: ModelConfigUpdateRequest) -> ModelConfigResponse:
    settings = get_settings()
    env_path = Path.cwd() / ".env"
    current_api_key = settings.model.api_key or ""

    overridden = sorted(MODEL_ENV_KEYS & set(os.environ))
    if overridden:
        raise AppError(
            "MODEL_CONFIG_EXTERNALLY_MANAGED",
            "Model configuration is managed by process environment variables",
            status_code=409,
        )

    if request.api_key_mode == "replace":
        api_key = request.api_key or ""
    elif request.api_key_mode == "clear":
        api_key = ""
    else:
        api_key = current_api_key

    values = {
        "MODEL_PROVIDER": request.provider,
        "MODEL_BASE_URL": request.base_url or "",
        "MODEL_NAME": request.name,
        "MODEL_API_KEY": api_key,
        "MODEL_NOTE": request.note or "",
        "MODEL_TIMEOUT_SECONDS": str(request.timeout_seconds),
        "MODEL_MAX_RETRIES": str(request.max_retries),
        "MODEL_CONTEXT_WINDOW": str(request.context_window),
    }

    await asyncio.to_thread(_write_env_values, env_path, values)
    get_settings.cache_clear()
    return await get_model_config()


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
    with _ENV_WRITE_LOCK:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = env_path.with_name(f"{env_path.name}.lock")
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            _write_env_values_locked(env_path, values)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)


def _write_env_values_locked(env_path: Path, values: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(values)
    next_lines: list[str] = []

    for line in lines:
        key = _env_key(line)
        if key in remaining:
            next_lines.append(f"{key}={_format_env_value(remaining.pop(key))}")
            continue
        next_lines.append(line)

    if remaining:
        if next_lines and next_lines[-1] != "":
            next_lines.append("")
        for key, value in remaining.items():
            next_lines.append(f"{key}={_format_env_value(value)}")

    rendered = "\n".join(next_lines).rstrip() + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=env_path.parent,
        prefix=".codemate-env-",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, env_path)
        directory_descriptor = os.open(env_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def _env_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key if key in MODEL_ENV_KEYS else None


def _format_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() or char in {'"', "'", "#"} for char in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
