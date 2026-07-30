import hashlib
import re
from dataclasses import dataclass
from typing import Literal

MemoryScope = Literal["global", "project", "temporary"]
ConflictRelation = Literal["compatible", "duplicate", "supersedes", "contradicts", "uncertain"]

_SENSITIVE_PATTERNS = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:api[ _-]?key|access[ _-]?token|password|passwd|secret)"
        r"\s*(?::|=|is\b|是)\s*[^\s,，;；]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+|cookie\s*[:=]\s*)"
        r"[^\s,，;；]+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:ghp|github_pat|glpat)-?[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)
_PROJECT_MARKERS = re.compile(
    r"(?:本项目|当前项目|这个项目|项目约定|项目内|in\s+this\s+project)",
    re.IGNORECASE,
)
_TEMPORARY_MARKERS = re.compile(
    r"(?:这次|本次|当前回答|这一轮|临时|for\s+this\s+(?:turn|request)|temporarily)",
    re.IGNORECASE,
)
_STABLE_MARKERS = (
    re.compile(r"(?:以后|今后|之后).{0,12}(?:请|都|默认|始终|一直|不要|使用|用)"),
    re.compile(r"请(?:始终|一直|默认|永远|以后)"),
    re.compile(r"(?:我|本人)(?:一直|通常|习惯|偏好|更喜欢|不喜欢)"),
    re.compile(r"(?:默认|始终)(?:使用|采用|用|不要)"),
    re.compile(
        r"(?:remember that|from now on|i (?:always |usually )?(?:prefer|like|dislike))",
        re.IGNORECASE,
    ),
)
_DIRECTIVE_MARKERS = re.compile(
    r"(?:请|使用|采用|改用|切换|不要|禁止|必须|回答|use|prefer|always|never|must)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?;；\n])")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class LongTermPreference:
    scope: MemoryScope
    category: str
    key: str
    value: str
    content: str
    stable: bool = True
    active: bool = True
    preference_id: int | None = None
    updated_at: str = ""


@dataclass(frozen=True)
class DetectedConflict:
    relation: ConflictRelation
    key: str
    winning_content: str
    losing_preference_id: int | None
    confidence: float = 1.0


@dataclass(frozen=True)
class MemoryConflictAudit:
    conflict_id: int
    left_preference_id: int
    right_preference_id: int
    relation: ConflictRelation
    confidence: float
    resolution: str
    resolved_by: str
    created_at: str
    resolved_at: str | None


class SensitiveContentDetector:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def contains_sensitive_content(self, text: str) -> bool:
        return self._enabled and any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS)

    def redact(self, text: str) -> str:
        if not self._enabled:
            return text
        redacted = text
        for pattern in _SENSITIVE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted


class PreferenceExtractor:
    def __init__(self, sensitive_detector: SensitiveContentDetector | None = None) -> None:
        self._sensitive_detector = sensitive_detector or SensitiveContentDetector()

    def extract(self, text: str) -> list[LongTermPreference]:
        return [preference for preference in self.extract_directives(text) if preference.stable]

    def extract_directives(self, text: str) -> list[LongTermPreference]:
        preferences: list[LongTermPreference] = []
        for raw_sentence in _SENTENCE_SPLIT.split(text):
            sentence = _WHITESPACE.sub(" ", raw_sentence).strip()
            if not 4 <= len(sentence) <= 500:
                continue
            if self._sensitive_detector.contains_sensitive_content(sentence):
                continue

            is_project = bool(_PROJECT_MARKERS.search(sentence))
            is_temporary = bool(_TEMPORARY_MARKERS.search(sentence))
            is_stable = is_project or any(pattern.search(sentence) for pattern in _STABLE_MARKERS)
            if not _DIRECTIVE_MARKERS.search(sentence):
                continue

            category, key, value = self._structured_value(sentence)
            preferences.append(
                LongTermPreference(
                    scope="project" if is_project else "global" if is_stable else "temporary",
                    category=category,
                    key=key,
                    value=value,
                    content=sentence,
                    stable=is_stable and not is_temporary,
                )
            )
        return preferences

    def _structured_value(self, sentence: str) -> tuple[str, str, str]:
        normalized = sentence.casefold()
        if any(keyword in normalized for keyword in ("中文", "chinese")):
            return "language", "response_language", "zh"
        if any(keyword in normalized for keyword in ("英文", "英语", "english")):
            return "language", "response_language", "en"
        if "pytest" in normalized:
            if any(keyword in normalized for keyword in ("不要", "禁止", "不用", "never", "not")):
                return "testing", "test_framework", "not_pytest"
            return "testing", "test_framework", "pytest"
        if any(keyword in normalized for keyword in ("不写测试", "不要测试", "不运行测试")):
            return "testing", "test_policy", "disabled"
        if any(keyword in normalized for keyword in ("测试", "test", "coverage")):
            return "testing", "test_policy", self._normalized_value(sentence)
        if any(keyword in normalized for keyword in ("文档", "document")):
            return "workflow", "documentation_policy", self._normalized_value(sentence)
        if any(keyword in normalized for keyword in ("提交", "commit")):
            return "workflow", "commit_policy", self._normalized_value(sentence)
        if any(keyword in normalized for keyword in ("代码", "注释", "命名", "格式", "code")):
            return "coding_style", "coding_style", self._normalized_value(sentence)
        if any(keyword in normalized for keyword in ("工具", "命令", "框架", "tool", "command")):
            return "tooling", "tooling_policy", self._normalized_value(sentence)
        digest = hashlib.sha256(self._normalized_value(sentence).encode("utf-8")).hexdigest()[:12]
        return "general", f"general:{digest}", self._normalized_value(sentence)

    def _normalized_value(self, text: str) -> str:
        return _WHITESPACE.sub(" ", text).strip().casefold()


class ContextConflictDetector:
    def resolve(
        self,
        stored_preferences: list[LongTermPreference],
        current_directives: list[LongTermPreference],
    ) -> tuple[list[LongTermPreference], list[DetectedConflict]]:
        conflicts: list[DetectedConflict] = []
        current_by_key = {directive.key: directive for directive in current_directives}
        selected_by_key: dict[str, LongTermPreference] = {}

        for preference in stored_preferences:
            current = current_by_key.get(preference.key)
            if current is not None:
                relation: ConflictRelation = (
                    "duplicate" if current.value == preference.value else "contradicts"
                )
                conflicts.append(
                    DetectedConflict(
                        relation=relation,
                        key=preference.key,
                        winning_content=current.content,
                        losing_preference_id=preference.preference_id,
                    )
                )
                continue

            selected = selected_by_key.get(preference.key)
            if selected is None:
                selected_by_key[preference.key] = preference
                continue

            selected_rank = self._scope_rank(selected.scope)
            preference_rank = self._scope_rank(preference.scope)
            if preference_rank > selected_rank:
                selected_by_key[preference.key] = preference
                winner, loser = preference, selected
            else:
                winner, loser = selected, preference
            conflicts.append(
                DetectedConflict(
                    relation="duplicate" if winner.value == loser.value else "supersedes",
                    key=preference.key,
                    winning_content=winner.content,
                    losing_preference_id=loser.preference_id,
                )
            )

        return list(selected_by_key.values()), conflicts

    def _scope_rank(self, scope: MemoryScope) -> int:
        return {"temporary": 3, "project": 2, "global": 1}[scope]
