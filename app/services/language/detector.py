from pathlib import Path

from app.services.language.interfaces import LanguageDetectionRule, LanguageInfo


class LanguageDetector:
    def __init__(self, workspace_root: Path, rules: tuple[LanguageDetectionRule, ...]) -> None:
        self._workspace_root = workspace_root.resolve()
        self._rules = rules

    def detect(self, current_file: str | Path | None) -> LanguageInfo:
        file_path = self._normalize_current_file(current_file)
        by_extension = self._detect_by_extension(file_path)
        by_project = self._detect_by_project_files(file_path)

        if by_extension is not None and by_project is not None:
            return LanguageInfo(
                language=by_extension.language,
                file_path=file_path,
                project_type=by_project.language,
                package_manager=by_project.package_manager,
                confidence=max(by_extension.confidence, by_project.confidence),
            )
        if by_extension is not None:
            return by_extension
        if by_project is not None:
            return by_project
        return LanguageInfo(language="text", file_path=file_path, confidence=0.1)

    def _detect_by_extension(self, file_path: Path | None) -> LanguageInfo | None:
        if file_path is None:
            return None
        suffix = file_path.suffix.lower()
        for rule in self._rules:
            if suffix in rule.extensions:
                return LanguageInfo(language=rule.language, file_path=file_path, confidence=0.9)
        return None

    def _detect_by_project_files(self, file_path: Path | None) -> LanguageInfo | None:
        start = self._workspace_root
        if file_path is not None:
            start = file_path if file_path.is_dir() else file_path.parent

        candidates = [start, *start.parents]
        for candidate in candidates:
            if self._workspace_root not in (candidate, *candidate.parents):
                continue
            for rule in self._rules:
                matched_files = [
                    name for name in rule.project_files if (candidate / name).is_file()
                ]
                if matched_files:
                    return LanguageInfo(
                        language=rule.language,
                        file_path=file_path,
                        project_type=rule.language,
                        package_manager=self._detect_package_manager(candidate, rule),
                        confidence=0.75,
                    )
        return None

    def _detect_package_manager(self, root: Path, rule: LanguageDetectionRule) -> str | None:
        for file_name, package_manager in rule.package_manager_files.items():
            if (root / file_name).is_file():
                return package_manager
        return rule.default_package_manager

    def _normalize_current_file(self, current_file: str | Path | None) -> Path | None:
        if not current_file:
            return None
        path = Path(current_file)
        if not path.is_absolute():
            path = self._workspace_root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            return None
        return resolved
