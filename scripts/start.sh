#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${CODEMATE_VENV_DIR:-$PROJECT_ROOT/.venv}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
RELOAD="${CODEMATE_RELOAD:-false}"

if [[ ! -x "$VENV_DIR/bin/python" ]] || ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
  if [[ -d "$VENV_DIR" ]]; then
    echo "[CodeMate] 修复不完整的 Python 虚拟环境"
    rm -rf "$VENV_DIR"
  fi
  echo "[CodeMate] 创建 Python 虚拟环境: $VENV_DIR"
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    cat >&2 <<'MSG'
无法创建虚拟环境。请先安装 Python 3.12 和 venv 支持：
  Debian/Ubuntu: sudo apt install python3.12-venv
MSG
    exit 1
  fi
fi

PYTHON="$VENV_DIR/bin/python"
STAMP_FILE="$VENV_DIR/.codemate-dependencies"
LOCK_HASH="$(sha256sum requirements.lock | awk '{print $1}')"

if [[ ! -f "$STAMP_FILE" ]] || [[ "$(cat "$STAMP_FILE")" != "$LOCK_HASH" ]]; then
  echo "[CodeMate] 安装锁定依赖..."
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -c requirements.lock -e .
  printf '%s' "$LOCK_HASH" > "$STAMP_FILE"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[CodeMate] 已创建 .env，可按需修改模型配置"
fi

if [[ "${CODEMATE_PREPARE_ONLY:-false}" == "true" ]]; then
  echo "[CodeMate] 运行环境准备完成"
  exit 0
fi

echo "[CodeMate] 启动地址: http://${HOST}:${PORT}"
echo "[CodeMate] 健康检查: http://${HOST}:${PORT}/health"

UVICORN_ARGS=(app.main:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
elif [[ "$WORKERS" != "1" ]]; then
  UVICORN_ARGS+=(--workers "$WORKERS")
fi

exec "$PYTHON" -m uvicorn "${UVICORN_ARGS[@]}"
