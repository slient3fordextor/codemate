from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.agent.graph.models import (
    ActivationMode,
    EdgeTrigger,
    GraphEdge,
    GraphEvent,
    GraphEventType,
    GraphNode,
    NodePolicy,
    NodeState,
    NodeType,
)


class GraphStateError(ValueError):
    """Raised when a graph mutation would violate its execution contract."""


_TRANSITIONS: dict[NodeState, set[NodeState]] = {
    NodeState.PENDING: {NodeState.READY, NodeState.SKIPPED, NodeState.CANCELLED},
    NodeState.READY: {NodeState.RUNNING, NodeState.CANCELLED},
    NodeState.RUNNING: {
        NodeState.SUCCEEDED,
        NodeState.FAILED,
        NodeState.WAITING,
        NodeState.INTERRUPTED,
        NodeState.CANCELLED,
    },
    NodeState.WAITING: {NodeState.READY, NodeState.CANCELLED},
    NodeState.INTERRUPTED: {NodeState.READY, NodeState.WAITING, NodeState.CANCELLED},
    NodeState.SUCCEEDED: set(),
    NodeState.FAILED: {NodeState.READY, NodeState.CANCELLED},
    NodeState.SKIPPED: set(),
    NodeState.CANCELLED: set(),
}


class ExecutionGraph:
    """Append-only graph state with deterministic readiness calculation."""

    def __init__(self, graph_id: str | None = None) -> None:
        self.graph_id = graph_id or uuid4().hex
        self._nodes: dict[str, GraphNode] = {}
        self._incoming: dict[str, list[GraphEdge]] = defaultdict(list)
        self._outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        self._events: list[GraphEvent] = []

    @property
    def events(self) -> tuple[GraphEvent, ...]:
        return tuple(deepcopy(self._events))

    @property
    def nodes(self) -> tuple[GraphNode, ...]:
        return tuple(deepcopy(node) for node in self._nodes.values())

    def node(self, node_id: str) -> GraphNode:
        return deepcopy(self._node(node_id))

    def _node(self, node_id: str) -> GraphNode:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise GraphStateError(f"Unknown graph node: {node_id}") from exc

    def add_node(
        self,
        node_id: str,
        name: str,
        node_type: NodeType,
        *,
        input: dict[str, Any] | None = None,
        policy: NodePolicy | None = None,
        activation: ActivationMode = ActivationMode.ALL,
    ) -> GraphNode:
        if node_id in self._nodes:
            raise GraphStateError(f"Duplicate graph node: {node_id}")
        node = GraphNode(
            id=node_id,
            name=name,
            type=node_type,
            input=deepcopy(input) if input is not None else {},
            policy=policy or NodePolicy(),
            activation=activation,
        )
        self._nodes[node_id] = node
        self._append_event(
            GraphEventType.NODE_ADDED,
            node_id=node.id,
            state=NodeState.PENDING,
            detail={
                "name": node.name,
                "node_type": node.type.value,
                "input": deepcopy(node.input),
                "policy": {
                    "timeout_seconds": node.policy.timeout_seconds,
                    "max_tokens": node.policy.max_tokens,
                    "max_output_bytes": node.policy.max_output_bytes,
                    "retryable": node.policy.retryable,
                    "idempotency_key": node.policy.idempotency_key,
                    "requires_approval": node.policy.requires_approval,
                },
                "activation": node.activation.value,
                "created_at": node.created_at,
            },
        )
        return deepcopy(node)

    def add_edge(self, edge: GraphEdge) -> None:
        self._node(edge.source_id)
        target = self._node(edge.target_id)
        if target.state is not NodeState.PENDING:
            raise GraphStateError(
                f"Cannot add an incoming edge after node {edge.target_id} left pending"
            )
        if edge in self._incoming[edge.target_id]:
            raise GraphStateError("Duplicate graph edge")
        if edge.source_id == edge.target_id or self._has_path(edge.target_id, edge.source_id):
            raise GraphStateError("Execution graphs cannot contain cycles")
        self._incoming[edge.target_id].append(edge)
        self._outgoing[edge.source_id].append(edge)
        self._append_event(
            GraphEventType.EDGE_ADDED,
            node_id=edge.target_id,
            detail={
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "trigger": edge.trigger.value,
                "condition_key": edge.condition_key,
                "condition_value": deepcopy(edge.condition_value),
            },
        )

    def refresh(self) -> None:
        """Promote eligible nodes and skip exhausted branches without executing work."""
        changed = True
        while changed:
            changed = False
            for node in self._nodes.values():
                if node.state is not NodeState.PENDING:
                    continue
                incoming = self._incoming[node.id]
                if not incoming:
                    self._transition(node, NodeState.READY, {"reason": "root_node"})
                    changed = True
                    continue
                matches = [self._edge_matches(edge) for edge in incoming]
                if node.activation is ActivationMode.ALL and all(matches):
                    self._transition(node, NodeState.READY, {"reason": "all_edges_matched"})
                    changed = True
                elif node.activation is ActivationMode.ANY and any(matches):
                    self._transition(node, NodeState.READY, {"reason": "an_edge_matched"})
                    changed = True
                elif all(self._node(edge.source_id).state.terminal for edge in incoming):
                    self._transition(node, NodeState.SKIPPED, {"reason": "no_matching_edge"})
                    changed = True

    def ready_nodes(self) -> tuple[GraphNode, ...]:
        self.refresh()
        return tuple(
            deepcopy(node) for node in self._nodes.values() if node.state is NodeState.READY
        )

    def start(self, node_id: str) -> None:
        self.refresh()
        node = self._node(node_id)
        if node.state is not NodeState.READY:
            raise GraphStateError(f"Node {node_id} is not ready")
        node.attempt_count += 1
        self._transition(node, NodeState.RUNNING, {"attempt_count": node.attempt_count})

    def succeed(self, node_id: str, output: dict[str, Any] | None = None) -> None:
        node = self._node(node_id)
        self._require_running(node)
        node.output = deepcopy(output) if output is not None else {}
        node.error = None
        self._transition(node, NodeState.SUCCEEDED, {"output": deepcopy(node.output)})
        self.refresh()

    def fail(self, node_id: str, error: str) -> None:
        node = self._node(node_id)
        self._require_running(node)
        node.error = error
        self._transition(node, NodeState.FAILED, {"error": error})
        self.refresh()

    def wait_for_approval(self, node_id: str, detail: dict[str, Any]) -> None:
        node = self._node(node_id)
        self._require_running(node)
        if not detail.get("approval_id"):
            raise GraphStateError("Approval detail must include approval_id")
        self._transition(node, NodeState.WAITING, detail)

    def resume(
        self,
        node_id: str,
        *,
        approved: bool = True,
        approval_id: str | None = None,
        decided_by: str | None = None,
    ) -> None:
        node = self._node(node_id)
        if node.state not in {NodeState.WAITING, NodeState.INTERRUPTED}:
            raise GraphStateError(f"Node {node_id} is not waiting or interrupted")
        if node.state is NodeState.WAITING and (not approval_id or not decided_by):
            raise GraphStateError("Approval resume requires approval_id and decided_by")
        if node.state is NodeState.WAITING and approval_id != self._pending_approval_id(node_id):
            raise GraphStateError("Approval id does not match the pending request")
        self._transition(
            node,
            NodeState.READY if approved else NodeState.CANCELLED,
            {
                "approved": approved,
                "approval_id": approval_id,
                "decided_by": decided_by,
            },
        )

    def _pending_approval_id(self, node_id: str) -> str | None:
        for event in reversed(self._events):
            if (
                event.node_id == node_id
                and event.type is GraphEventType.STATE_CHANGED
                and event.state is NodeState.WAITING
            ):
                approval_id = event.detail.get("approval_id")
                return approval_id if isinstance(approval_id, str) else None
        return None

    def interrupt_running(self) -> None:
        for node in self._nodes.values():
            if node.state is NodeState.RUNNING:
                self._transition(node, NodeState.INTERRUPTED, {"reason": "runtime_interrupted"})

    def _edge_matches(self, edge: GraphEdge) -> bool:
        source = self._node(edge.source_id)
        if edge.trigger is EdgeTrigger.SUCCESS:
            return source.state is NodeState.SUCCEEDED
        if edge.trigger is EdgeTrigger.FAILURE:
            return source.state is NodeState.FAILED
        if edge.trigger is EdgeTrigger.ALWAYS:
            return source.state.terminal
        if edge.condition_key is None:
            return False
        return (
            source.state is NodeState.SUCCEEDED
            and source.output.get(edge.condition_key) == edge.condition_value
        )

    def _has_path(self, start_id: str, target_id: str) -> bool:
        pending = [start_id]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(edge.target_id for edge in self._outgoing[current])
        return False

    def _require_running(self, node: GraphNode) -> None:
        if node.state is not NodeState.RUNNING:
            raise GraphStateError(f"Node {node.id} is not running")

    def _transition(self, node: GraphNode, state: NodeState, detail: dict[str, Any]) -> None:
        if state not in _TRANSITIONS[node.state]:
            raise GraphStateError(f"Invalid transition: {node.state} -> {state}")
        previous_state = node.state
        node.state = state
        event_detail = deepcopy(detail)
        event_detail.setdefault("attempt_count", node.attempt_count)
        if node.error is not None:
            event_detail.setdefault("error", node.error)
        self._append_event(
            GraphEventType.STATE_CHANGED,
            node_id=node.id,
            previous_state=previous_state,
            state=state,
            detail=event_detail,
        )

    def _append_event(
        self,
        event_type: GraphEventType,
        *,
        node_id: str | None = None,
        previous_state: NodeState | None = None,
        state: NodeState | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            GraphEvent(
                sequence=len(self._events) + 1,
                graph_id=self.graph_id,
                type=event_type,
                at=datetime.now(UTC),
                node_id=node_id,
                previous_state=previous_state,
                state=state,
                detail=deepcopy(detail) if detail is not None else {},
            )
        )

    @classmethod
    def from_events(cls, events: tuple[GraphEvent, ...]) -> "ExecutionGraph":
        if not events:
            raise GraphStateError("Cannot replay an empty graph event stream")
        graph_ids = {event.graph_id for event in events}
        if len(graph_ids) != 1:
            raise GraphStateError("Graph event stream contains multiple graph ids")
        graph = cls(graph_id=events[0].graph_id)
        expected_sequence = 1
        for event in events:
            if event.sequence != expected_sequence:
                raise GraphStateError("Graph event sequence is not contiguous")
            expected_sequence += 1
            graph._replay_event(event)
        graph._events = list(deepcopy(events))
        return graph

    def _replay_event(self, event: GraphEvent) -> None:
        detail = event.detail
        if event.type is GraphEventType.NODE_ADDED:
            if event.node_id is None or event.node_id in self._nodes:
                raise GraphStateError("Invalid node creation event")
            policy = detail["policy"]
            self._nodes[event.node_id] = GraphNode(
                id=event.node_id,
                name=detail["name"],
                type=NodeType(detail["node_type"]),
                input=deepcopy(detail["input"]),
                policy=NodePolicy(**policy),
                activation=ActivationMode(detail["activation"]),
                created_at=detail["created_at"],
            )
            return
        if event.type is GraphEventType.EDGE_ADDED:
            edge = GraphEdge(
                source_id=detail["source_id"],
                target_id=detail["target_id"],
                trigger=EdgeTrigger(detail["trigger"]),
                condition_key=detail["condition_key"],
                condition_value=deepcopy(detail["condition_value"]),
            )
            self._node(edge.source_id)
            self._node(edge.target_id)
            self._incoming[edge.target_id].append(edge)
            self._outgoing[edge.source_id].append(edge)
            return
        if event.type is GraphEventType.STATE_CHANGED:
            if event.node_id is None or event.previous_state is None or event.state is None:
                raise GraphStateError("Invalid state transition event")
            node = self._node(event.node_id)
            if (
                node.state is not event.previous_state
                or event.state not in _TRANSITIONS[node.state]
            ):
                raise GraphStateError("Event transition does not match replayed graph state")
            node.state = event.state
            node.attempt_count = int(detail.get("attempt_count", node.attempt_count))
            if "output" in detail:
                node.output = deepcopy(detail["output"])
            if "error" in detail:
                node.error = detail["error"]
            return
        raise GraphStateError(f"Unsupported graph event type: {event.type}")
