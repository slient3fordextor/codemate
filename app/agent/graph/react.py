from dataclasses import dataclass
from enum import StrEnum


class ReActDecision(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    WAIT_FOR_APPROVAL = "wait_for_approval"


class ReActAction(StrEnum):
    ACT = "act"
    REPLAN = "replan"


@dataclass(frozen=True)
class ReActBudget:
    """Bounded ReAct policy for a single Agent execution unit.

    Ten rounds are the normal budget. Rounds 11-15 require fresh evidence or
    validation progress. Every round beyond 15 requires explicit approval.
    """

    default_rounds: int = 10
    approval_after_rounds: int = 15
    max_replans: int = 3

    def __post_init__(self) -> None:
        if self.default_rounds <= 0:
            raise ValueError("default_rounds must be greater than zero")
        if self.approval_after_rounds < self.default_rounds:
            raise ValueError("approval_after_rounds cannot be below default_rounds")
        if self.max_replans < 0:
            raise ValueError("max_replans cannot be negative")

    def next_decision(
        self,
        completed_rounds: int,
        *,
        has_new_evidence: bool,
        replans: int = 0,
        requested_action: ReActAction = ReActAction.ACT,
        approved_round: int | None = None,
        approved_replan: int | None = None,
    ) -> ReActDecision:
        if completed_rounds < 0:
            raise ValueError("completed_rounds cannot be negative")
        if replans < 0:
            raise ValueError("replans cannot be negative")
        if approved_round is not None and approved_round <= 0:
            raise ValueError("approved_round must be positive")
        if approved_replan is not None and approved_replan <= 0:
            raise ValueError("approved_replan must be positive")
        if (
            requested_action is ReActAction.REPLAN
            and replans >= self.max_replans
            and approved_replan != replans + 1
        ):
            return ReActDecision.WAIT_FOR_APPROVAL
        if completed_rounds < self.default_rounds:
            return ReActDecision.CONTINUE
        if completed_rounds < self.approval_after_rounds:
            return ReActDecision.CONTINUE if has_new_evidence else ReActDecision.STOP
        return (
            ReActDecision.CONTINUE
            if approved_round == completed_rounds + 1
            else ReActDecision.WAIT_FOR_APPROVAL
        )
