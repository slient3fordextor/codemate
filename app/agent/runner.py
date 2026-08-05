import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters.models.base import (
    ModelAdapter,
    ModelProviderError,
    ModelRequest,
    ModelToolDefinition,
)
from app.agent.graph import EdgeTrigger, ExecutionGraph, NodeType, ReActBudget, ReActDecision
from app.agent.graph.models import GraphEdge, NodePolicy
from app.agent.tools import ReadonlyToolError, ToolExecutionResult
from app.schemas.chat import ChatMessage
from app.services.context_budget import ContextBudgetExceededError, ContextBudgetPlanner
from app.services.token_counter import EstimatedTokenCounter


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_EVIDENCE = "needs_evidence"
    WAITING_APPROVAL = "waiting_approval"
    FAILED = "failed"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tool", "final"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "AgentDecision":
        if self.type == "tool" and (not self.tool or self.answer is not None):
            raise ValueError("Tool decisions require tool and cannot include answer")
        if self.type == "final" and (not self.answer or self.tool is not None or self.arguments):
            raise ValueError("Final decisions require answer and cannot include a tool")
        return self


@dataclass(frozen=True)
class AgentProgressEvent:
    type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class AgentRunResult:
    status: AgentRunStatus
    answer: str
    graph: ExecutionGraph
    rounds: int
    tool_calls: int
    tokens_used: int
    error: str | None = None


@dataclass(frozen=True)
class NativeToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class CollectedModelResponse:
    content: str
    tool_calls: tuple[NativeToolCall, ...]
    tokens: int


ProgressCallback = Callable[[AgentProgressEvent], None]
ApprovalCallback = Callable[[int], Awaitable[bool]]


@dataclass(frozen=True)
class ToolApprovalRequest:
    id: str
    tool: str
    arguments: dict[str, Any]
    preview: str


ToolApprovalCallback = Callable[[ToolApprovalRequest], Awaitable[bool]]
GraphCheckpoint = Callable[[ExecutionGraph], None]


class AgentToolExecutor(Protocol):
    definitions: tuple[dict[str, Any], ...]

    @property
    def model_definitions(self) -> tuple[ModelToolDefinition, ...]: ...

    @property
    def system_prompt(self) -> str: ...

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        approval_id: str | None = None,
    ) -> ToolExecutionResult: ...

    def requires_approval(self, tool: str) -> bool: ...

    def approval_preview(self, tool: str, arguments: dict[str, Any]) -> str: ...

    def cacheable(self, tool: str) -> bool: ...

    def completion_error(self) -> str | None: ...

    @property
    def validated_patch_digest(self) -> str | None: ...


class ReadonlyAgentRunner:
    """Runs a bounded model/tool loop under the supplied tool policy."""

    def __init__(
        self,
        model_adapter: ModelAdapter,
        model: str,
        tools: AgentToolExecutor,
        *,
        react_budget: ReActBudget | None = None,
        max_decision_errors: int = 2,
        context_budget_planner: ContextBudgetPlanner | None = None,
        max_total_tokens: int = 120_000,
    ) -> None:
        if max_decision_errors < 0:
            raise ValueError("max_decision_errors cannot be negative")
        if max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be greater than zero")
        self._model_adapter = model_adapter
        self._model = model
        self._tools = tools
        self._react_budget = react_budget or ReActBudget()
        self._max_decision_errors = max_decision_errors
        self._context_budget_planner = context_budget_planner or ContextBudgetPlanner(
            EstimatedTokenCounter(),
            32_768,
        )
        self._max_total_tokens = max_total_tokens
        self._native_tools = bool(getattr(model_adapter, "supports_tools", False))

    async def run(
        self,
        objective: str,
        *,
        on_progress: ProgressCallback | None = None,
        approve_round: ApprovalCallback | None = None,
        approve_tool: ToolApprovalCallback | None = None,
        checkpoint: GraphCheckpoint | None = None,
    ) -> AgentRunResult:
        if not objective.strip():
            raise ValueError("Agent objective cannot be empty")
        graph = ExecutionGraph()
        messages = [
            ChatMessage(role="system", content=self._system_prompt()),
            ChatMessage(role="user", content=objective.strip()),
        ]
        previous_node_id: str | None = None
        cache: dict[str, ToolExecutionResult] = {}
        tool_calls = 0
        round_number = 0
        decision_errors = 0
        tokens_used = 0

        while True:
            round_number += 1
            model_node_id = f"model.{round_number}"
            graph.add_node(
                model_node_id,
                f"Decide next action (round {round_number})",
                NodeType.MODEL,
                input={"round": round_number},
            )
            if previous_node_id is not None:
                graph.add_edge(
                    GraphEdge(previous_node_id, model_node_id, trigger=EdgeTrigger.ALWAYS)
                )
            graph.start(model_node_id)
            self._checkpoint(checkpoint, graph)
            self._emit(on_progress, "model.started", {"round": round_number})

            try:
                response = await self._collect_model(messages)
            except (ContextBudgetExceededError, ModelProviderError) as exc:
                graph.fail(model_node_id, str(exc))
                self._checkpoint(checkpoint, graph)
                return AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    answer="",
                    graph=graph,
                    rounds=round_number,
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                    error=str(exc),
                )
            tokens_used += response.tokens
            if tokens_used > self._max_total_tokens:
                error = f"Agent token budget exceeded ({tokens_used}/{self._max_total_tokens})"
                graph.fail(model_node_id, error)
                self._checkpoint(checkpoint, graph)
                return AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    answer="",
                    graph=graph,
                    rounds=round_number,
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                    error=error,
                )
            try:
                decision = self._parse_response(response)
            except ValueError as exc:
                graph.fail(model_node_id, str(exc))
                self._checkpoint(checkpoint, graph)
                decision_errors += 1
                self._emit(
                    on_progress,
                    "model.invalid_decision",
                    {"round": round_number, "error": str(exc)},
                )
                if decision_errors > self._max_decision_errors:
                    return AgentRunResult(
                        status=AgentRunStatus.FAILED,
                        answer="",
                        graph=graph,
                        rounds=round_number,
                        tool_calls=tool_calls,
                        tokens_used=tokens_used,
                        error=str(exc),
                    )
                messages.extend(
                    (
                        ChatMessage(
                            role="assistant",
                            content=response.content or "[invalid native tool call]",
                        ),
                        ChatMessage(
                            role="user",
                            content=(
                                "Your previous response violated the Agent decision schema. "
                                f"Error: {exc}. Return exactly one valid JSON object."
                            ),
                        ),
                    )
                )
                previous_node_id = model_node_id
                continue

            if decision.type == "final":
                evidence_error = self._tools.completion_error()
                if evidence_error is not None:
                    graph.fail(model_node_id, evidence_error)
                    self._checkpoint(checkpoint, graph)
                    return AgentRunResult(
                        status=AgentRunStatus.NEEDS_EVIDENCE,
                        answer=decision.answer or "",
                        graph=graph,
                        rounds=round_number,
                        tool_calls=tool_calls,
                        tokens_used=tokens_used,
                        error=evidence_error,
                    )
                graph.succeed(model_node_id, {"decision": decision.model_dump(mode="json")})
                self._checkpoint(checkpoint, graph)
                answer = decision.answer or ""
                self._emit(on_progress, "agent.completed", {"answer": answer})
                return AgentRunResult(
                    status=AgentRunStatus.COMPLETED,
                    answer=answer,
                    graph=graph,
                    rounds=round_number,
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                )

            graph.succeed(model_node_id, {"decision": decision.model_dump(mode="json")})
            self._checkpoint(checkpoint, graph)

            tool_node_id = f"tool.{round_number}"
            tool_node_type = self._tool_node_type(decision.tool or "")
            graph.add_node(
                tool_node_id,
                f"Run {decision.tool}",
                tool_node_type,
                input={"tool": decision.tool, "arguments": decision.arguments},
                policy=NodePolicy(
                    requires_approval=self._tools.requires_approval(decision.tool or "")
                ),
            )
            graph.add_edge(GraphEdge(model_node_id, tool_node_id))
            graph.start(tool_node_id)
            self._checkpoint(checkpoint, graph)
            cache_key = json.dumps(
                {"tool": decision.tool, "arguments": decision.arguments},
                ensure_ascii=False,
                sort_keys=True,
            )
            cacheable = self._tools.cacheable(decision.tool or "")
            cached = cacheable and cache_key in cache
            approval_id: str | None = None
            try:
                if self._tools.requires_approval(decision.tool or ""):
                    approval_id = f"approval:{graph.graph_id}:{tool_node_id}"
                    preview = self._tools.approval_preview(decision.tool or "", decision.arguments)
                    if approve_tool is None:
                        graph.wait_for_approval(
                            tool_node_id,
                            {
                                "approval_id": approval_id,
                                "tool": decision.tool,
                                "arguments": decision.arguments,
                                "preview": preview,
                            },
                        )
                        self._checkpoint(checkpoint, graph)
                        return AgentRunResult(
                            status=AgentRunStatus.WAITING_APPROVAL,
                            answer="",
                            graph=graph,
                            rounds=round_number,
                            tool_calls=tool_calls,
                            tokens_used=tokens_used,
                            error=f"Tool {decision.tool} requires approval",
                        )
                    approved = await approve_tool(
                        ToolApprovalRequest(
                            approval_id,
                            decision.tool or "",
                            decision.arguments,
                            preview,
                        )
                    )
                    if not approved:
                        raise ReadonlyToolError(f"Tool {decision.tool} was denied")
                result = cache.get(cache_key)
                if result is None:
                    tool_calls += 1
                    result = await asyncio.to_thread(
                        self._tools.execute,
                        decision.tool or "",
                        decision.arguments,
                        approval_id=approval_id,
                    )
                    if cacheable:
                        cache[cache_key] = result
                graph.succeed(
                    tool_node_id,
                    {
                        "tool": result.tool,
                        "content": result.content,
                        "metadata": result.metadata,
                        "cached": cached,
                    },
                )
                self._checkpoint(checkpoint, graph)
            except ReadonlyToolError as exc:
                graph.fail(tool_node_id, str(exc))
                self._checkpoint(checkpoint, graph)
                result_message = json.dumps(
                    {"tool": decision.tool, "error": str(exc)}, ensure_ascii=False
                )
            else:
                result_message = result.as_message()
                self._emit(
                    on_progress,
                    "tool.completed",
                    {"tool": result.tool, "metadata": result.metadata, "cached": cached},
                )

            messages.extend(
                (
                    ChatMessage(
                        role="assistant",
                        content=self._decision_message(decision, response),
                    ),
                    ChatMessage(role="user", name="tool_result", content=result_message),
                )
            )
            previous_node_id = tool_node_id
            next_decision = self._react_budget.next_decision(
                round_number,
                has_new_evidence=not cached,
            )
            if next_decision is ReActDecision.STOP:
                return AgentRunResult(
                    status=AgentRunStatus.FAILED,
                    answer="",
                    graph=graph,
                    rounds=round_number,
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                    error="ReAct stopped because the latest round produced no new evidence",
                )
            if next_decision is ReActDecision.WAIT_FOR_APPROVAL:
                approved = approve_round is not None and await approve_round(round_number + 1)
                if not approved:
                    return AgentRunResult(
                        status=AgentRunStatus.WAITING_APPROVAL,
                        answer="",
                        graph=graph,
                        rounds=round_number,
                        tool_calls=tool_calls,
                        tokens_used=tokens_used,
                        error=f"Round {round_number + 1} requires approval",
                    )
                approved_decision = self._react_budget.next_decision(
                    round_number,
                    has_new_evidence=not cached,
                    approved_round=round_number + 1,
                )
                if approved_decision is not ReActDecision.CONTINUE:
                    raise RuntimeError("Approved ReAct round did not continue")

    async def _collect_model(self, messages: list[ChatMessage]) -> CollectedModelResponse:
        self._fit_context(messages)
        parts: list[str] = []
        budget = self._context_budget_planner.budget_for(None)
        input_tokens = self._context_budget_planner.token_counter.count_messages(messages)
        reported_input_tokens: int | None = None
        reported_output_tokens: int | None = None
        tool_calls: list[dict[str, str]] = []
        request = ModelRequest(
            messages=list(messages),
            model=self._model,
            stream=True,
            max_tokens=budget.output_reserve,
            tools=self._tools.model_definitions if self._native_tools else (),
        )
        async for chunk in self._model_adapter.stream_chat(request):
            if chunk.type == "message.delta" and chunk.content:
                parts.append(chunk.content)
            if chunk.input_tokens is not None:
                reported_input_tokens = chunk.input_tokens
            if chunk.output_tokens is not None:
                reported_output_tokens = chunk.output_tokens
            if chunk.type == "tool.call.start":
                tool_calls.append(
                    {
                        "id": chunk.tool_call_id or f"tool-call-{len(tool_calls) + 1}",
                        "name": chunk.tool_name or "",
                        "arguments": chunk.tool_arguments or "",
                    }
                )
            elif chunk.type == "tool.call.delta":
                if not tool_calls:
                    tool_calls.append(
                        {
                            "id": chunk.tool_call_id or "tool-call-1",
                            "name": chunk.tool_name or "",
                            "arguments": "",
                        }
                    )
                current = tool_calls[-1]
                if chunk.tool_name:
                    current["name"] += chunk.tool_name
                if chunk.tool_arguments:
                    current["arguments"] += chunk.tool_arguments
        content = "".join(parts).strip()
        output_tokens = self._context_budget_planner.token_counter.count_text(content)
        return CollectedModelResponse(
            content=content,
            tool_calls=tuple(
                NativeToolCall(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    arguments_json=tool_call["arguments"] or "{}",
                )
                for tool_call in tool_calls
            ),
            tokens=(reported_input_tokens or input_tokens)
            + (reported_output_tokens or output_tokens),
        )

    def _fit_context(self, messages: list[ChatMessage]) -> None:
        while True:
            try:
                self._context_budget_planner.validate(messages, None)
                return
            except ContextBudgetExceededError:
                if len(messages) <= 4:
                    raise
                del messages[2:4]

    def _parse_decision(self, raw: str) -> AgentDecision:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Model Agent decision must be one JSON object") from exc
        return AgentDecision.model_validate(payload)

    def _parse_response(self, response: CollectedModelResponse) -> AgentDecision:
        if response.tool_calls:
            if len(response.tool_calls) != 1:
                raise ValueError("Agent must request exactly one tool per round")
            tool_call = response.tool_calls[0]
            try:
                arguments = json.loads(tool_call.arguments_json)
            except json.JSONDecodeError as exc:
                raise ValueError("Native tool arguments must be one JSON object") from exc
            if not isinstance(arguments, dict):
                raise ValueError("Native tool arguments must be one JSON object")
            return AgentDecision(type="tool", tool=tool_call.name, arguments=arguments)
        if self._native_tools:
            if not response.content:
                raise ValueError("Model returned neither text nor a tool call")
            return AgentDecision(type="final", answer=response.content)
        return self._parse_decision(response.content)

    @staticmethod
    def _decision_message(
        decision: AgentDecision,
        response: CollectedModelResponse,
    ) -> str:
        if decision.type == "final" and response.content:
            return response.content
        return json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)

    def _system_prompt(self) -> str:
        if self._native_tools:
            return self._tools.system_prompt
        tools = json.dumps(self._tools.definitions, ensure_ascii=False)
        return (
            f"{self._tools.system_prompt} Return exactly one JSON object and no markdown. "
            'Use {"type":"tool","tool":NAME,"arguments":{...}} to inspect the '
            'workspace, or {"type":"final","answer":"..."} when the answer is '
            "supported by collected evidence. "
            f"Available tools: {tools}"
        )

    @staticmethod
    def _tool_node_type(tool: str) -> NodeType:
        if tool.startswith("git_"):
            return NodeType.GIT
        if tool == "propose_write":
            return NodeType.PROPOSE_PATCH
        if tool == "apply_patch":
            return NodeType.APPLY_PATCH
        if tool == "run_command":
            return NodeType.COMMAND
        if tool == "search_text":
            return NodeType.SEARCH
        return NodeType.READ

    def _emit(
        self,
        callback: ProgressCallback | None,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        if callback is not None:
            callback(AgentProgressEvent(event_type, data))

    @staticmethod
    def _checkpoint(callback: GraphCheckpoint | None, graph: ExecutionGraph) -> None:
        if callback is not None:
            callback(graph)
