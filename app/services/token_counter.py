from __future__ import annotations

import importlib
from dataclasses import dataclass
from math import ceil
from typing import Protocol

from app.schemas.chat import ChatMessage


class TokenCounter(Protocol):
    @property
    def counter_id(self) -> str: ...

    @property
    def estimated(self) -> bool: ...

    def count_text(self, text: str) -> int: ...

    def count_messages(self, messages: list[ChatMessage]) -> int: ...


@dataclass(frozen=True)
class EstimatedTokenCounter:
    counter_id: str = "estimated:utf8-v1"
    estimated: bool = True

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return ceil(len(text.encode("utf-8")) / 3)

    def count_messages(self, messages: list[ChatMessage]) -> int:
        tokens = 2
        for message in messages:
            tokens += 4
            tokens += self.count_text(message.role)
            tokens += self.count_text(message.content)
            if message.name:
                tokens += self.count_text(message.name)
        return tokens


class TiktokenTokenCounter:
    estimated = False

    def __init__(self, model: str) -> None:
        tiktoken = importlib.import_module("tiktoken")
        self._encoding = tiktoken.encoding_for_model(model)
        self.counter_id = f"tiktoken:{model}:{self._encoding.name}"

    def count_text(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

    def count_messages(self, messages: list[ChatMessage]) -> int:
        tokens = 3
        for message in messages:
            tokens += 3
            tokens += self.count_text(message.role)
            tokens += self.count_text(message.content)
            if message.name:
                tokens += 1 + self.count_text(message.name)
        return tokens


def build_token_counter(provider: str, model: str) -> TokenCounter:
    if provider == "openai_compatible":
        try:
            return TiktokenTokenCounter(model)
        except (ImportError, KeyError, OSError, ValueError):
            pass
    return EstimatedTokenCounter(counter_id=f"estimated:utf8-v1:{provider}:{model}")
