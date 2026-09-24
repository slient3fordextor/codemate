#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
TARGET="$TARGET_DIR/codemate.desktop"
mkdir -p "$TARGET_DIR"
sed "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" "$PROJECT_ROOT/packaging/codemate.desktop.in" > "$TARGET"
chmod 644 "$TARGET"
update-desktop-database "$TARGET_DIR" >/dev/null 2>&1 || true
echo "已安装 CodeMate 桌面启动器: $TARGET"
