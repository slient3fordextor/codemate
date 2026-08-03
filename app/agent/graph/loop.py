from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.agent.graph.models import GraphEdge, NodeState, NodeType
from app.agent.graph.runtime import ExecutionGraph, GraphStateError


@dataclass(frozen=True)
class LoopBudget:
    max_iterations: int = 3
    max_total_tokens: int = 12_000
    max_replans: int = 3

    def __post_init__(self) -> None:
        if self.max_iterations <= 0 or self.max_total_tokens <= 0 or self.max_replans < 0:
            raise ValueError("Loop limits must be positive, except max_replans may be zero")


@dataclass(frozen=True)
class LoopStep:
    name: str
    type: NodeType
    input: dict[str, Any]


class LoopDecisionType(StrEnum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class LoopState:
    iterations: int = 0
    used_tokens: int = 0
    replans: int = 0
    pending_replan_from: str | None = None
    processed_validation_ids: tuple[str, ...] = ()
    last_replan_approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.iterations < 0 or self.used_tokens < 0 or self.replans < 0:
            raise ValueError("Loop state counters cannot be negative")


@dataclass(frozen=True)
class LoopDecision:
    type: LoopDecisionType
    state: LoopState
    node_ids: tuple[str, ...] = ()
    reason: str | None = None


class GraphLoop:
    """Materializes bounded iterations as new DAG nodes instead of graph back-edges."""

    def __init__(
        self,
        loop_id: str,
        steps: tuple[LoopStep, ...],
        budget: LoopBudget,
        *,
        state: LoopState | None = None,
    ) -> None:
        if not steps:
            raise ValueError("A graph loop requires at least one step")
        self._loop_id = loop_id
        self._steps = steps
        self._budget = budget
        restored = state or LoopState()
        self._iterations = restored.iterations
        self._used_tokens = restored.used_tokens
        self._replans = restored.replans
        self._pending_replan_from = restored.pending_replan_from
        self._processed_validation_ids = set(restored.processed_validation_ids)
        self._last_replan_approval_id = restored.last_replan_approval_id

    @property
    def state(self) -> LoopState:
        return LoopState(
            self._iterations,
            self._used_tokens,
            self._replans,
            self._pending_replan_from,
            tuple(sorted(self._processed_validation_ids)),
            self._last_replan_approval_id,
        )

    @property
    def exhausted(self) -> bool:
        return (
            self._iterations >= self._budget.max_iterations
            or self._used_tokens >= self._budget.max_total_tokens
        )

    def start(self, graph: ExecutionGraph) -> tuple[str, ...]:
        if self._iterations:
            raise GraphStateError("Graph loop has already started")
        return self._append_iteration(graph)

    def continue_after_failure(
        self,
        graph: ExecutionGraph,
        validation_node_id: str,
        *,
        passed: bool,
        used_tokens: int,
        requires_replan: bool = True,
    ) -> LoopDecision:
        if used_tokens < 0:
            raise ValueError("used_tokens cannot be negative")
        if self._pending_replan_from is not None:
            raise GraphStateError("Graph loop is waiting for replan approval")
        if validation_node_id in self._processed_validation_ids:
            raise GraphStateError("Loop validation result has already been consumed")
        self._validate_result(graph, validation_node_id, passed)
        self._processed_validation_ids.add(validation_node_id)
        self._used_tokens += used_tokens
        if passed:
            return LoopDecision(LoopDecisionType.COMPLETE, self.state, reason="validation_passed")
        if self.exhausted:
            return LoopDecision(
                LoopDecisionType.BUDGET_EXHAUSTED,
                self.state,
                reason=self._exhaustion_reason(),
            )
        if requires_replan:
            if self._replans >= self._budget.max_replans:
                self._pending_replan_from = validation_node_id
                return LoopDecision(
                    LoopDecisionType.WAIT_FOR_APPROVAL,
                    self.state,
                    reason="replan_limit_reached",
                )
            self._replans += 1
        node_ids = self._append_iteration(graph, validation_node_id)
        return LoopDecision(LoopDecisionType.CONTINUE, self.state, node_ids=node_ids)

    def resume_after_replan_approval(
        self,
        graph: ExecutionGraph,
        validation_node_id: str,
        *,
        approval_id: str,
    ) -> LoopDecision:
        if not approval_id:
            raise ValueError("Replan approval id is required")
        if self._pending_replan_from != validation_node_id:
            raise GraphStateError("No matching replan approval is pending")
        if self.exhausted:
            return LoopDecision(
                LoopDecisionType.BUDGET_EXHAUSTED,
                self.state,
                reason=self._exhaustion_reason(),
            )
        self._pending_replan_from = None
        self._replans += 1
        self._last_replan_approval_id = approval_id
        node_ids = self._append_iteration(graph, validation_node_id)
        return LoopDecision(LoopDecisionType.CONTINUE, self.state, node_ids=node_ids)

    def _validate_result(
        self,
        graph: ExecutionGraph,
        validation_node_id: str,
        passed: bool,
    ) -> None:
        validation = graph.node(validation_node_id)
        if validation.state is not NodeState.SUCCEEDED:
            raise GraphStateError("Loop validation node has not succeeded")
        if validation.output.get("passed") is not passed:
            raise GraphStateError("Loop result does not match validation evidence")

    def _exhaustion_reason(self) -> str:
        reasons: list[str] = []
        if self._iterations >= self._budget.max_iterations:
            reasons.append("iteration_limit_reached")
        if self._used_tokens >= self._budget.max_total_tokens:
            reasons.append("token_limit_reached")
        return ",".join(reasons)

    def _append_iteration(
        self,
        graph: ExecutionGraph,
        previous_validation_id: str | None = None,
    ) -> tuple[str, ...]:
        if self.exhausted:
            raise GraphStateError("Graph loop budget exhausted")
        self._iterations += 1
        node_ids: list[str] = []
        for index, step in enumerate(self._steps):
            node_id = f"{self._loop_id}.{self._iterations}.{index}"
            graph.add_node(
                node_id,
                f"{step.name} (iteration {self._iterations})",
                step.type,
                input={**step.input, "loop_id": self._loop_id, "iteration": self._iterations},
            )
            node_ids.append(node_id)
            if index:
                graph.add_edge(GraphEdge(source_id=node_ids[index - 1], target_id=node_id))
        if previous_validation_id is not None:
            graph.add_edge(GraphEdge(source_id=previous_validation_id, target_id=node_ids[0]))
        graph.refresh()
        return tuple(node_ids)
