from pathlib import Path

from app.schemas.chat import ChatMessage
from app.services.language.detector import LanguageDetector
from app.services.language.interfaces import CodeContext, LanguageContextConfig
from app.services.language.registry import LanguageRegistry


class LanguageContextService:
    def __init__(
        self,
        workspace_root: Path,
        detector: LanguageDetector,
        registry: LanguageRegistry,
        config: LanguageContextConfig | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._detector = detector
        self._registry = registry
        self._config = config or LanguageContextConfig()

    def build_context_message(
        self,
        current_file: str | None,
        selected_text: str | None,
    ) -> ChatMessage | None:
        if not self._config.enabled:
            return self._legacy_context_message(current_file, selected_text)
        if not current_file and not selected_text:
            return None

        code_context = self.collect_context(current_file, selected_text)
        parts = self._render_code_context(code_context)
        return ChatMessage(role="system", content="\n\n".join(parts))

    def collect_context(self, current_file: str | None, selected_text: str | None) -> CodeContext:
        language_info = self._detector.detect(current_file)
        adapter = self._registry.get(language_info.language)
        if adapter is None:
            return CodeContext(
                language=language_info.language,
                current_file=language_info.file_path,
                selected_text=selected_text,
                system_hints=(f"Current language: {language_info.language}",),
            )

        project = adapter.detect_project(self._workspace_root, language_info.file_path)
        if project is None:
            return CodeContext(
                language=language_info.language,
                current_file=language_info.file_path,
                selected_text=selected_text,
                system_hints=(f"Current language: {language_info.language}",),
            )
        return adapter.collect_context(project, language_info.file_path, selected_text)

    def _render_code_context(self, code_context: CodeContext) -> list[str]:
        parts = [f"Current language: {code_context.language}"]
        if code_context.current_file:
            parts.append(f"Current file: {code_context.current_file}")
        if code_context.project_summary:
            parts.append(f"Project summary: {code_context.project_summary}")
        if code_context.system_hints:
            rendered_hints = "\n".join(f"- {hint}" for hint in code_context.system_hints)
            parts.append("Language hints:\n" + rendered_hints)
        if code_context.selected_text:
            parts.append(f"Selected text:\n{code_context.selected_text}")
        return parts

    def _legacy_context_message(
        self,
        current_file: str | None,
        selected_text: str | None,
    ) -> ChatMessage | None:
        context_parts = []
        if current_file:
            context_parts.append(f"Current file: {current_file}")
        if selected_text:
            context_parts.append(f"Selected text:\n{selected_text}")
        if not context_parts:
            return None
        return ChatMessage(role="system", content="\n\n".join(context_parts))
