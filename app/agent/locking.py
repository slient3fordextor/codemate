"""Process-owned advisory locks. Never unlink lock files: that splits lock ownership."""

import fcntl
import json
import math
import os
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


@contextmanager
def process_lock(path: Path, *, action: str, timeout_seconds: float = 0) -> Iterator[None]:
    from app.agent.worktree.models import WorktreeError

    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("Lock timeout must be finite and non-negative")
    descriptor: int | None = None
    acquired = False
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    owner = os.pread(descriptor, 4096, 0).decode("utf-8", errors="replace")
                    raise WorktreeError(
                        f"Lock busy: {path.name}; owner: {owner or 'unknown'}"
                    ) from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        owner_data = json.dumps(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "action": action,
                "acquired_at": datetime.now(UTC).isoformat(),
            }
        ).encode()
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, owner_data, 0)
        yield
    except OSError as exc:
        raise WorktreeError(f"Lock operation failed for {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            try:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
