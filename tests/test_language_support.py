from pathlib import Path

from app.adapters.models.mock import MockModelAdapter
from app.schemas.chat import ChatCompletionRequest
from app.services.chat import ChatService
from app.services.language.detector import LanguageDetector
from app.services.language.factory import build_language_context_service
from app.services.language.interfaces import LanguageDetectionRule


def test_language_detector_prefers_current_file_language_over_project_type(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    current_file = tmp_path / "src" / "app.ts"
    current_file.parent.mkdir()
    current_file.write_text("export const value = 1\n", encoding="utf-8")

    detector = LanguageDetector(
        tmp_path,
        (
            LanguageDetectionRule(
                language="python",
                extensions=frozenset({".py"}),
                project_files=frozenset({"pyproject.toml"}),
                default_package_manager="python",
            ),
            LanguageDetectionRule(
                language="typescript",
                extensions=frozenset({".ts"}),
                project_files=frozenset({"tsconfig.json"}),
                default_package_manager="npm",
            ),
        ),
    )

    info = detector.detect("src/app.ts")

    assert info.language == "typescript"
    assert info.project_type == "python"
    assert info.file_path == current_file.resolve()


def test_language_context_service_builds_python_context_message(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    source_file = tmp_path / "app" / "main.py"
    source_file.parent.mkdir()
    source_file.write_text("def create_app():\n    pass\n", encoding="utf-8")

    service = build_language_context_service(tmp_path)

    message = service.build_context_message("app/main.py", "create_app()")

    assert message is not None
    assert message.role == "system"
    assert "Current language: python" in message.content
    assert "pyproject.toml" in message.content
    assert "Selected text:\ncreate_app()" in message.content


def test_language_context_service_does_not_accept_paths_outside_workspace(tmp_path: Path) -> None:
    service = build_language_context_service(tmp_path)

    message = service.build_context_message("/etc/passwd", None)

    assert message is not None
    assert "Current language: text" in message.content
    assert "Current file:" not in message.content


async def test_chat_service_adds_language_context_message(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")
    source_file = tmp_path / "src" / "index.ts"
    source_file.parent.mkdir()
    source_file.write_text("export const value: number = 1\n", encoding="utf-8")
    language_context_service = build_language_context_service(tmp_path)
    service = ChatService(
        model_adapter=MockModelAdapter(),
        default_model="mock-model",
        language_context_service=language_context_service,
    )

    messages = await service._build_messages(
        ChatCompletionRequest(message="解释", current_file="src/index.ts"),
        session_id="ses_test",
    )

    system_messages = [message for message in messages if message.role == "system"]
    assert any("Current language: typescript" in message.content for message in system_messages)
    assert any("Package manager: npm" in message.content for message in system_messages)
