"""Host-local lock identity for the supported local X11 desktop."""

import hashlib
import os
import re
import socket
import stat
from pathlib import Path

from app.agent.worktree.models import WorktreeError


def desktop_lock_path() -> Path:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        raise WorktreeError("电脑控制目前不支持 Wayland")
    display = os.environ.get("DISPLAY", "")
    match = re.fullmatch(r"(?P<host>[^:]*):(?P<number>\d+)(?:\.\d+)?", display)
    if match is None or match["host"] not in {
        "",
        "unix",
        "localhost",
        "127.0.0.1",
        socket.gethostname(),
    }:
        raise WorktreeError("电脑控制需要受支持的本机 X11 DISPLAY，远程显示会话不受支持")
    # Different screen numbers and local aliases still address the same X server.
    identity = hashlib.sha256(str(int(match["number"])).encode()).hexdigest()[:16]
    directory = Path("/tmp") / f"codemate-desktop-{os.getuid()}"
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise WorktreeError("桌面锁目录归属或权限不安全")
    except OSError as exc:
        raise WorktreeError(f"无法准备桌面锁目录：{exc}") from exc
    return directory / f"display-{identity}.lock"
