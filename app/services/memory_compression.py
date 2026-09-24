import re
from dataclasses import dataclass

from app.services.token_counter import TokenCounter

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?;；\n])")
_WHITESPACE = re.compile(r"\s+")
_CONSTRAINT = re.compile(r"(?:必须|不要|禁止|只能|要求|不得|must|never|required)", re.I)
_DECISION = re.compile(r"(?:决定|选择|采用|改为|确认|约定|decid|choose|adopt)", re.I)
_OPEN_ITEM = re.compile(r"(?:待办|下一步|需要|尚未|未完成|阻塞|todo|next|pending|blocked)", re.I)
_EVIDENCE = re.compile(
    r"(?:error|exception|traceback|失败|报错|错误|"
    r"(?-i:\b[A-Z][A-Z0-9_]{3,}\b)|[/\\][\w./\\-]+)",
    re.I,
)


@dataclass(frozen=True)
class CompressionResult:
    content: str
    token_count: int
    required_items: tuple[str, ...]
    level: int
    stage: str
    reason: str
    input_token_count: int
    dropped_items: tuple[str, ...]
    version: str = "deterministic-v1"


class CompressionInvariantError(ValueError):
    pass


class DeterministicMemoryCompressor:
    def compress_turn(
        self,
        turn_id: int,
        user_message: str,
        assistant_message: str,
        token_counter: TokenCounter,
        max_tokens: int,
    ) -> CompressionResult:
        user_sentences = self._sentences(user_message)
        assistant_sentences = self._sentences(assistant_message)
        classified = self._classify((*user_sentences, *assistant_sentences))
        lines = [f"Turn {turn_id}:"]
        required: list[str] = []

        for label in ("Constraints", "Decisions", "Open items", "Evidence"):
            items = classified[label]
            if not items:
                continue
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in items)
            required.extend(items)

        if user_sentences:
            lines.append(f"Request: {user_sentences[0]}")
        if assistant_sentences:
            lines.append(f"Outcome: {assistant_sentences[-1]}")

        input_token_count = token_counter.count_text(f"{user_message}\n{assistant_message}")
        content = self._fit_lines(lines, token_counter, max_tokens)
        self._validate_required_items(content, required)
        dropped_items = tuple(line for line in lines if line not in content)
        return CompressionResult(
            content=content,
            token_count=token_counter.count_text(content),
            required_items=tuple(dict.fromkeys(required)),
            level=2,
            stage="C4" if dropped_items else "C2",
            reason="older_turn_rollup",
            input_token_count=input_token_count,
            dropped_items=dropped_items,
        )

    def merge_chunks(
        self,
        contents: list[str],
        required_items: list[str],
        token_counter: TokenCounter,
        max_tokens: int,
        level: int,
    ) -> CompressionResult:
        lines: list[str] = [f"Compressed history level {level}:"]
        seen: set[str] = set()
        for required in required_items:
            normalized = self._normalize(required)
            if normalized and normalized not in seen:
                lines.append(f"- REQUIRED: {required}")
                seen.add(normalized)
        for content in contents:
            for line in content.splitlines():
                normalized = self._normalize(line)
                if not normalized or normalized in seen or line.startswith("Compressed history"):
                    continue
                lines.append(line)
                seen.add(normalized)

        source = "\n\n".join(contents)
        fitted = self._fit_lines(lines, token_counter, max_tokens)
        self._validate_required_items(fitted, required_items)
        dropped_items = tuple(line for line in lines if line not in fitted)
        return CompressionResult(
            content=fitted,
            token_count=token_counter.count_text(fitted),
            required_items=tuple(dict.fromkeys(required_items)),
            level=level,
            stage="C4" if dropped_items else "C2",
            reason="summary_budget_compaction",
            input_token_count=token_counter.count_text(source),
            dropped_items=dropped_items,
        )

    def _validate_required_items(self, content: str, required_items: list[str]) -> None:
        missing = [item for item in dict.fromkeys(required_items) if item not in content]
        if missing:
            raise CompressionInvariantError(
                "Required memory items exceed the configured summary token budget"
            )

    def _classify(self, sentences: tuple[str, ...]) -> dict[str, list[str]]:
        classified: dict[str, list[str]] = {
            "Constraints": [],
            "Decisions": [],
            "Open items": [],
            "Evidence": [],
        }
        for sentence in sentences:
            for label, pattern in (
                ("Constraints", _CONSTRAINT),
                ("Decisions", _DECISION),
                ("Open items", _OPEN_ITEM),
                ("Evidence", _EVIDENCE),
            ):
                if pattern.search(sentence):
                    classified[label].append(sentence)
        return classified

    def _sentences(self, text: str) -> tuple[str, ...]:
        return tuple(
            sentence for raw in _SENTENCE_SPLIT.split(text) if (sentence := self._normalize(raw))
        )

    def _fit_lines(
        self,
        lines: list[str],
        token_counter: TokenCounter,
        max_tokens: int,
    ) -> str:
        if max_tokens <= 0:
            return ""
        selected: list[str] = []
        for line in lines:
            candidate = "\n".join((*selected, line))
            if token_counter.count_text(candidate) <= max_tokens:
                selected.append(line)
                continue
            if not selected:
                selected.append(self._truncate_text(line, token_counter, max_tokens))
            break
        return "\n".join(selected).strip()

    def _truncate_text(
        self,
        text: str,
        token_counter: TokenCounter,
        max_tokens: int,
    ) -> str:
        marker = " [truncated]"
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[:middle].rstrip() + marker
            if token_counter.count_text(candidate) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        if low == 0:
            return marker.strip() if token_counter.count_text(marker.strip()) <= max_tokens else ""
        return text[:low].rstrip() + marker

    def _normalize(self, text: str) -> str:
        return _WHITESPACE.sub(" ", text).strip()
