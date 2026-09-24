#!/usr/bin/env python3
"""Linux desktop client for CodeMate, backed by the local FastAPI app."""

from __future__ import annotations

import json
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
    if (
        not venv_python.exists()
        or subprocess.run(
            [str(venv_python), "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        != 0
    ):
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
        manager = self._webview.get_user_content_manager()
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        self._theme_path = config_root / "codemate" / "theme.json"
        theme = "princess"
        try:
            saved = json.loads(self._theme_path.read_text())
            if saved in ("princess", "starry"):
                theme = saved
        except (OSError, ValueError):
            pass
        manager.register_script_message_handler("codemateTheme")
        manager.connect("script-message-received::codemateTheme", self._save_theme)
        manager.add_script(
            WebKit2.UserScript.new(
                "window.codemateTheme = " + json.dumps(theme) + ";",
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
        self._webview.set_hexpand(True)
        self._webview.set_vexpand(True)
        self._webview.connect("load-failed", self._load_failed)
        self.add(self._webview)
        self.show_all()
        self._webview.load_uri(url)

    def _save_theme(self, _manager: object, result: object) -> None:
        # Accept only a skin identifier; never arbitrary paths or native commands.
        if self._webview.get_uri() != self._url and not (self._webview.get_uri() or "").startswith(
            self._url + "/"
        ):
            return
        theme = result.get_js_value().to_string()
        if theme not in ("princess", "starry"):
            return
        try:
            self._theme_path.parent.mkdir(parents=True, exist_ok=True)
            self._theme_path.write_text(json.dumps(theme))
        except OSError as exc:
            print(f"无法保存皮肤偏好: {exc}", file=sys.stderr)

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
