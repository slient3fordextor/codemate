#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -n "${CODEMATE_SYSTEM_PYTHON:-}" ]]; then
  PYTHON_BIN="$CODEMATE_SYSTEM_PYTHON"
else
  PYTHON_BIN=""
  for candidate in /usr/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
      'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.0")' \
      >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "未找到支持 GTK/WebKitGTK 的 Python。请安装 python3-gi 和 WebKitGTK 运行库。" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$PROJECT_ROOT/desktop/client.py" "$@"
