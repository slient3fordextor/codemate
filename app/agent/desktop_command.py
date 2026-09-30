"""Exec xdotool with a Linux parent-death signal, without thread-unsafe preexec_fn."""

import ctypes
import os
import signal
import sys


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if sys.platform != "linux" or len(arguments) < 3 or arguments[1:3] != ["--", "xdotool"]:
        return 1
    try:
        parent = int(arguments[0])
        if parent <= 1 or os.getppid() != parent:
            return 1
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Cannot set desktop parent-death signal")
        if os.getppid() != parent:
            return 1
        os.execvp("xdotool", arguments[2:])
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
