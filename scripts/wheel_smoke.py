"""Build from a temporary source copy and smoke-test a clean wheel installation."""

import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

SMOKE = r"""
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import app

assert "site-packages" in str(Path(app.__file__).resolve()), app.__file__

class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attributes):
        for name, value in attributes:
            if name in {"src", "href"} and value and value.startswith("/static/"):
                self.paths.add(urlsplit(value).path)

with urlopen(BASE_URL + "/") as response:
    assert response.status == 200
    html = response.read().decode()
    assert "CodeMate" in html
assets = Assets()
assets.feed(html)
assets.paths.add("/static/computer.js")
assert {"/static/app.js", "/static/styles.css", "/static/theme.js"} <= assets.paths
for path in assets.paths:
    with urlopen(BASE_URL + path) as response:
        assert response.status == 200 and response.read(), path
with urlopen(BASE_URL + "/health") as response:
    assert json.load(response)["status"] == "ok"
print("Installed wheel: homepage, static assets and health passed")
"""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="codemate-wheel-smoke-") as temporary:
        temporary_root = Path(temporary)
        source = temporary_root / "source"
        source.mkdir()
        shutil.copy2(root / "pyproject.toml", source)
        readme = source / "docs" / "requirements"
        readme.mkdir(parents=True)
        shutil.copy2(root / "docs" / "requirements" / "README.md", readme)
        shutil.copytree(
            root / "app",
            source / "app",
            ignore=shutil.ignore_patterns(
                "__pycache__",
                "*.pyc",
                "images",
            ),
        )
        wheels = temporary_root / "wheels"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheels),
                str(source),
            ],
            check=True,
            timeout=120,
        )
        (wheel,) = wheels.glob("codemate-*.whl")
        environment_root = temporary_root / "installed"
        venv.EnvBuilder(with_pip=True).create(environment_root)
        python = environment_root / "bin" / "python"
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-c",
                str(root / "requirements.lock"),
                str(wheel),
            ],
            check=True,
            timeout=240,
        )
        cwd = temporary_root / "workspace"
        cwd.mkdir()
        environment = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "MODEL_PROVIDER": "mock",
            "SESSION_MEMORY_DATABASE_PATH": str(cwd / "memory.sqlite3"),
        }
        executable = environment_root / "bin" / "codemate"
        subprocess.run(
            [str(executable), "--version"], cwd=cwd, env=environment, check=True, timeout=30
        )
        subprocess.run(
            [str(executable), "run", "Explain this workspace", "--json"],
            cwd=cwd,
            env=environment,
            check=True,
            timeout=30,
        )
        # Uvicorn binds an ephemeral port, then reports its actual address.
        import socket

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.set_inheritable(True)
            address = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--fd",
                    str(listener.fileno()),
                ],
                cwd=cwd,
                env=environment,
                pass_fds=(listener.fileno(),),
            )
            try:
                import time
                from urllib.error import URLError
                from urllib.request import urlopen

                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError("Installed server exited before readiness")
                    try:
                        with urlopen(address + "/health", timeout=0.5):
                            break
                    except (URLError, TimeoutError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Installed server did not become ready")
                subprocess.run(
                    [str(python), "-c", f"BASE_URL = {address!r}\n" + SMOKE],
                    cwd=cwd,
                    env=environment,
                    check=True,
                    timeout=30,
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
