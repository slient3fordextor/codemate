import pytest

from app.agent.tools.desktop import DesktopControl
from app.agent.tools.readonly import ReadonlyToolError


def test_desktop_commands_are_allowlisted() -> None:
    assert DesktopControl._command("desktop_click", {"button": 1}) == [
        "xdotool",
        "click",
        "1",
    ]
    assert DesktopControl._command("desktop_type", {"text": "hello"})[-1] == "hello"


def test_desktop_commands_reject_shell_like_input() -> None:
    with pytest.raises(ReadonlyToolError):
        DesktopControl._command("desktop_click", {"button": 9})
    with pytest.raises(ReadonlyToolError):
        DesktopControl._command("desktop_move_cursor", {"x": "1", "y": 2})
    with pytest.raises(ReadonlyToolError):
        DesktopControl._command("desktop_type", {"text": "a\x00b"})
