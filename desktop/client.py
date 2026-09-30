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
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.0")
from gi.repository import Gdk, Gtk, WebKit2  # noqa: E402

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
        self.set_decorated(False)

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
        self._build_window_overlay()
        self._apply_window_theme(theme)
        self.show_all()
        self._webview.load_uri(url)

    def _build_window_overlay(self) -> None:
        self.set_name("codemate-window")
        self.set_decorated(False)
        self._window_style = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            self._window_style,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.connect("destroy", self._remove_window_style)
        overlay = Gtk.Overlay()
        overlay.add(self._webview)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        controls.set_name("codemate-window-controls")
        controls.set_halign(Gtk.Align.END)
        controls.set_valign(Gtk.Align.START)
        controls.set_margin_top(12)
        controls.set_margin_end(12)
        for icon, tooltip, action in (
            ("window-minimize-symbolic", "最小化", lambda _button: self.iconify()),
            ("window-maximize-symbolic", "最大化", lambda _button: self._toggle_maximized()),
            ("window-close-symbolic", "关闭", lambda _button: self.close()),
        ):
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.MENU)
            button.set_tooltip_text(tooltip)
            button.get_accessible().set_name(tooltip)
            button.connect("clicked", action)
            if icon == "window-close-symbolic":
                button.get_style_context().add_class("close")
            elif icon == "window-maximize-symbolic":
                self._maximize_button = button
            controls.pack_start(button, False, False, 0)
        overlay.add_overlay(controls)
        self.connect("window-state-event", self._window_state_changed)

        # Transparent hit targets preserve movement/resizing without a titlebar.
        drag = Gtk.EventBox()
        drag.set_visible_window(False)
        drag.set_halign(Gtk.Align.START)
        drag.set_valign(Gtk.Align.START)
        drag.set_margin_start(12)
        drag.set_size_request(220, 24)
        drag.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        drag.connect("button-press-event", self._drag_window)
        overlay.add_overlay(drag)
        for edge, horizontal, vertical, width, height in (
            (Gdk.WindowEdge.NORTH, Gtk.Align.FILL, Gtk.Align.START, -1, 5),
            (Gdk.WindowEdge.SOUTH, Gtk.Align.FILL, Gtk.Align.END, -1, 5),
            (Gdk.WindowEdge.WEST, Gtk.Align.START, Gtk.Align.FILL, 5, -1),
            (Gdk.WindowEdge.EAST, Gtk.Align.END, Gtk.Align.FILL, 5, -1),
            (Gdk.WindowEdge.NORTH_WEST, Gtk.Align.START, Gtk.Align.START, 9, 9),
            (Gdk.WindowEdge.NORTH_EAST, Gtk.Align.END, Gtk.Align.START, 9, 9),
            (Gdk.WindowEdge.SOUTH_WEST, Gtk.Align.START, Gtk.Align.END, 9, 9),
            (Gdk.WindowEdge.SOUTH_EAST, Gtk.Align.END, Gtk.Align.END, 9, 9),
        ):
            handle = Gtk.EventBox()
            handle.set_visible_window(False)
            handle.set_halign(horizontal)
            handle.set_valign(vertical)
            handle.set_size_request(width, height)
            handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            handle.connect("button-press-event", self._resize_window, edge)
            overlay.add_overlay(handle)
        self.add(overlay)

    def _toggle_maximized(self) -> None:
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def _window_state_changed(self, _window: object, event: object) -> bool:
        maximized = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
        icon = "window-restore-symbolic" if maximized else "window-maximize-symbolic"
        tooltip = "还原" if maximized else "最大化"
        self._maximize_button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        self._maximize_button.set_tooltip_text(tooltip)
        self._maximize_button.get_accessible().set_name(tooltip)
        return False

    def _drag_window(self, _widget: object, event: object) -> bool:
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._toggle_maximized()
        else:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def _resize_window(self, _widget: object, event: object, edge: object) -> bool:
        if event.button != 1 or self.is_maximized():
            return False
        self.begin_resize_drag(edge, event.button, int(event.x_root), int(event.y_root), event.time)
        return True

    def _remove_window_style(self, _window: object) -> None:
        Gtk.StyleContext.remove_provider_for_screen(self.get_screen(), self._window_style)

    def _apply_window_theme(self, theme: str) -> None:
        if theme == "starry":
            background, text, hover = "#0c1229", "#eef0ff", "#252b47"
        else:
            background, text, hover = "#fff2f8", "#795b77", "#f5d6e7"
        self._window_style.load_from_data(
            f"""
            window#codemate-window {{ background-color: {background}; }}
            #codemate-window-controls {{ background: transparent; }}
            #codemate-window-controls button {{
                background: transparent; color: {text}; border: none;
                box-shadow: none; text-shadow: none; border-radius: 8px;
                min-width: 28px; min-height: 28px; padding: 0; margin: 0;
            }}
            #codemate-window-controls button:hover {{ background: {hover}; }}
            #codemate-window-controls button.close:hover {{
                background: #a52545; color: #ffffff;
            }}
        """.encode()
        )

    def _save_theme(self, _manager: object, result: object) -> None:
        # Accept only a skin identifier; never arbitrary paths or native commands.
        if self._webview.get_uri() != self._url and not (self._webview.get_uri() or "").startswith(
            self._url + "/"
        ):
            return
        theme = result.get_js_value().to_string()
        if theme not in ("princess", "starry"):
            return
        self._apply_window_theme(theme)
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
