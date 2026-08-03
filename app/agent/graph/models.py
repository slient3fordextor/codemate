from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    MODEL = "model"
    READ = "read"
    SEARCH = "search"
    GIT = "git"
    PROPOSE_PATCH = "propose_patch"
    APPLY_PATCH = "apply_patch"
    COMMAND = "command"
    APPROVAL = "approval"
    JOIN = "join"
    SUMMARY = "summary"


class NodeState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            NodeState.SUCCEEDED,
            NodeState.FAILED,
            NodeState.SKIPPED,
            NodeState.CANCELLED,
        }


class EdgeTrigger(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    ALWAYS = "always"
    CONDITION = "condition"


class ActivationMode(StrEnum):
    ALL = "all"
    ANY = "any"


class GraphEventType(StrEnum):
    NODE_ADDED = "node_added"
    EDGE_ADDED = "edge_added"
    STATE_CHANGED = "state_changed"


@dataclass(frozen=True)
class NodePolicy:
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_output_bytes: int | None = None
    retryable: bool = False
    idempotency_key: str | None = None
    requires_approval: bool = False


@dataclass
class GraphNode:
    id: str
    name: str
    type: NodeType
    input: dict[str, Any] = field(default_factory=dict)
    policy: NodePolicy = field(default_factory=NodePolicy)
    activation: ActivationMode = ActivationMode.ALL
    state: NodeState = NodeState.PENDING
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempt_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    target_id: str
    trigger: EdgeTrigger = EdgeTrigger.SUCCESS
    condition_key: str | None = None
    condition_value: Any = None

    def __post_init__(self) -> None:
        if self.trigger is EdgeTrigger.CONDITION and not self.condition_key:
            raise ValueError("condition_key is required for condition edges")


@dataclass(frozen=True)
class GraphEvent:
    sequence: int
    graph_id: str
    type: GraphEventType
    at: datetime
    node_id: str | None = None
    previous_state: NodeState | None = None
    state: NodeState | None = None
    detail: dict[str, Any] = field(default_factory=dict)
