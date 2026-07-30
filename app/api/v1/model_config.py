from pathlib import Path

from fastapi import APIRouter

from app.core.config import get_settings
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
    env_path = settings.workspace.root / ".env"
    current_api_key = settings.model.api_key or ""

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

    _write_env_values(env_path, values)
    get_settings.cache_clear()
    return await get_model_config()


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
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

    env_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


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
