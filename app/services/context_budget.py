from dataclasses import dataclass
from math import ceil

from app.schemas.chat import ChatMessage
from app.services.token_counter import TokenCounter


class ContextBudgetExceededError(Exception):
    code = "CONTEXT_BUDGET_EXCEEDED"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    output_reserve: int
    protocol_margin: int
    input_budget: int


class ContextBudgetPlanner:
    def __init__(
        self,
        token_counter: TokenCounter,
        context_window: int,
        default_output_tokens: int = 1_024,
        protocol_margin_ratio: float = 0.05,
    ) -> None:
        if context_window <= 0:
            raise ValueError("context_window must be greater than 0")
        if default_output_tokens <= 0:
            raise ValueError("default_output_tokens must be greater than 0")
        if not 0 <= protocol_margin_ratio < 1:
            raise ValueError("protocol_margin_ratio must be between 0 and 1")
        self._token_counter = token_counter
        self._context_window = context_window
        self._default_output_tokens = default_output_tokens
        self._protocol_margin_ratio = protocol_margin_ratio

    @property
    def token_counter(self) -> TokenCounter:
        return self._token_counter

    def budget_for(self, requested_output_tokens: int | None) -> ContextBudget:
        output_reserve = requested_output_tokens or self._default_output_tokens
        protocol_margin = ceil(self._context_window * self._protocol_margin_ratio)
        input_budget = self._context_window - output_reserve - protocol_margin
        if input_budget <= 0:
            raise ContextBudgetExceededError(
                "Output reserve and protocol margin leave no room for input context"
            )
        return ContextBudget(
            context_window=self._context_window,
            output_reserve=output_reserve,
            protocol_margin=protocol_margin,
            input_budget=input_budget,
        )

    def remaining_for_memory(
        self,
        required_messages: list[ChatMessage],
        requested_output_tokens: int | None,
    ) -> int:
        budget = self.budget_for(requested_output_tokens)
        required_tokens = self._token_counter.count_messages(required_messages)
        remaining = budget.input_budget - required_tokens
        if remaining < 0:
            raise ContextBudgetExceededError(
                f"Required context uses {required_tokens} tokens, exceeding the "
                f"{budget.input_budget}-token input budget"
            )
        return remaining

    def validate(
        self,
        messages: list[ChatMessage],
        requested_output_tokens: int | None,
    ) -> None:
        budget = self.budget_for(requested_output_tokens)
        used_tokens = self._token_counter.count_messages(messages)
        if used_tokens > budget.input_budget:
            raise ContextBudgetExceededError(
                f"Prepared context uses {used_tokens} tokens, exceeding the "
                f"{budget.input_budget}-token input budget"
            )
