#!/usr/bin/env python3
"""Linux desktop client for CodeMate, backed by the local FastAPI app."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.0")
from gi.repository import Gtk, WebKit2  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT = ROOT / "scripts" / "start.sh"


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prepare_runtime() -> Path:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists() or subprocess.run(
        [str(venv_python), "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        env = os.environ.copy()
        env["CODEMATE_PREPARE_ONLY"] = "true"
        subprocess.run([str(PREPARE_SCRIPT)], cwd=ROOT, env=env, check=True)
    return venv_python


def backend_ready(url: str) -> bool:
    try:
        with urlopen(f"{url}/health", timeout=0.5) as response:
            return response.status < 500
    except Exception:
        return False


class CodeMateClient(Gtk.Window):
    def __init__(self, backend: subprocess.Popen[str], url: str) -> None:
        super().__init__(title="CodeMate")
        self._backend = backend
        self._url = url
        self.set_default_size(1440, 920)
        self.set_size_request(960, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", self._close)

        settings = WebKit2.WebsiteDataManager.new_ephemeral()
        context = WebKit2.WebContext.new_with_website_data_manager(settings)
        self._webview = WebKit2.WebView.new_with_context(context)
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        self._webview.connect("load-failed", self._load_failed)
        self.add(self._webview)
        self.show_all()
        self._webview.load_uri(url)

    def _load_failed(self, _view: object, _event: object, error: object) -> bool:
        print(f"CodeMate 页面加载失败: {error}", file=sys.stderr)
        return False

    def _close(self, _window: object) -> None:
        if self._backend.poll() is None:
            self._backend.terminate()
            try:
                self._backend.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._backend.kill()
        Gtk.main_quit()


def main() -> int:
    port = choose_port()
    url = f"http://127.0.0.1:{port}"
    python = prepare_runtime()
    env = os.environ.copy()
    env.update({"HOST": "127.0.0.1", "PORT": str(port), "PYTHONUNBUFFERED": "1"})
    backend = subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        start_new_session=True,
        text=True,
    )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            return backend.returncode or 1
        if backend_ready(url):
            break
        time.sleep(0.2)
    else:
        backend.terminate()
        raise RuntimeError("本地 CodeMate 服务启动超时")

    client = CodeMateClient(backend, url)
    signal.signal(signal.SIGTERM, lambda *_: client.close())
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
