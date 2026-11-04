import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapters.models.base import (
    ModelAdapter,
    ModelChunk,
    ModelProviderError,
    ModelRequest,
    ModelToolDefinition,
)
from app.agent.budget import TaskBudget
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
    reservation_id: str | None = None


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
        task_budget: TaskBudget | None = None,
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
        self._task_budget = task_budget
        self._live_messages: list[ChatMessage] = []

    async def run(
        self,
        objective: str,
        *,
        on_progress: ProgressCallback | None = None,
        approve_round: ApprovalCallback | None = None,
        approve_tool: ToolApprovalCallback | None = None,
        checkpoint: GraphCheckpoint | None = None,
        initial_messages: list[ChatMessage] | None = None,
    ) -> AgentRunResult:
        if not objective.strip():
            raise ValueError("Agent objective cannot be empty")
        graph = ExecutionGraph()
        messages = (
            list(initial_messages)
            if initial_messages
            else [
                ChatMessage(role="system", content=self._system_prompt()),
                ChatMessage(role="user", content=objective.strip()),
            ]
        )
        if initial_messages:
            messages[0] = ChatMessage(role="system", content=self._system_prompt())
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        f"Continue this retained task: {objective.strip()}. "
                        "Inspect current files before "
                        "acting. Old patch proposal IDs and approvals have expired. Do not assume "
                        "an interrupted action succeeded; obtain fresh evidence and approvals."
                    ),
                )
            )
        self._live_messages = messages
        previous_node_id: str | None = None
        cache: dict[str, ToolExecutionResult] = {}
        tool_calls = 0
        round_number = 0
        decision_errors = 0
        tokens_used = 0

        if self._task_budget and self._task_budget.snapshot()["rounds"] >= 15:
            next_round = self._task_budget.snapshot()["rounds"] + 1
            if approve_round is None or not await approve_round(next_round):
                return AgentRunResult(
                    AgentRunStatus.WAITING_APPROVAL,
                    "",
                    graph,
                    0,
                    0,
                    0,
                    error=f"Round {next_round} requires approval",
                )

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
                self._settle(response)
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
                    self._settle(response)
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
                self._settle(response)
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
            self._settle(response)
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
                    result = await self._execute_tool(
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
            self._checkpoint(checkpoint, graph)
            previous_node_id = tool_node_id
            completed_rounds = (
                self._task_budget.snapshot()["rounds"] if self._task_budget else round_number
            )
            next_decision = self._react_budget.next_decision(
                completed_rounds,
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
                approved = approve_round is not None and await approve_round(completed_rounds + 1)
                if not approved:
                    return AgentRunResult(
                        status=AgentRunStatus.WAITING_APPROVAL,
                        answer="",
                        graph=graph,
                        rounds=round_number,
                        tool_calls=tool_calls,
                        tokens_used=tokens_used,
                        error=f"Round {completed_rounds + 1} requires approval",
                    )
                approved_decision = self._react_budget.next_decision(
                    completed_rounds,
                    has_new_evidence=not cached,
                    approved_round=completed_rounds + 1,
                )
                if approved_decision is not ReActDecision.CONTINUE:
                    raise RuntimeError("Approved ReAct round did not continue")

    async def _collect_model(self, messages: list[ChatMessage]) -> CollectedModelResponse:
        self._fit_context(messages)
        parts: list[str] = []
        budget = self._context_budget_planner.budget_for(None)
        input_tokens = self._context_budget_planner.token_counter.count_messages(messages)
        reservation_id = None
        output_reserve = budget.output_reserve
        if self._task_budget is not None:
            reservation_id, output_reserve = self._task_budget.reserve(input_tokens, output_reserve)
        reported_input_tokens: int | None = None
        reported_output_tokens: int | None = None
        tool_calls: list[dict[str, str]] = []
        request = ModelRequest(
            messages=list(messages),
            model=self._model,
            stream=True,
            max_tokens=output_reserve,
            tools=self._tools.model_definitions if self._native_tools else (),
        )
        done = False
        output_size = 0
        async with aclosing(
            cast(AsyncGenerator[ModelChunk, None], self._model_adapter.stream_chat(request))
        ) as upstream:
            async for chunk in upstream:
                if chunk.type == "message.done":
                    done = True
                if chunk.type == "message.delta" and chunk.content:
                    parts.append(chunk.content)
                    output_size += len(chunk.content)
                if chunk.input_tokens is not None:
                    if chunk.input_tokens < 0:
                        raise ModelProviderError("INVALID_USAGE", "Negative model token usage")
                    reported_input_tokens = chunk.input_tokens
                if chunk.output_tokens is not None:
                    if chunk.output_tokens < 0:
                        raise ModelProviderError("INVALID_USAGE", "Negative model token usage")
                    reported_output_tokens = chunk.output_tokens
                if chunk.type == "tool.call.start":
                    if len(tool_calls) >= 8:
                        raise ModelProviderError("MODEL_OUTPUT_TOO_LARGE", "Too many tool calls")
                    output_size += len(chunk.tool_arguments or "") + len(chunk.tool_name or "")
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
                        output_size += len(chunk.tool_name)
                    if chunk.tool_arguments:
                        current["arguments"] += chunk.tool_arguments
                        output_size += len(chunk.tool_arguments)
                if output_size > 200000:
                    raise ModelProviderError(
                        "MODEL_OUTPUT_TOO_LARGE", "Agent model output exceeds limit"
                    )
        if not done:
            raise ModelProviderError(
                "INCOMPLETE_STREAM", "Agent model stream ended without completion"
            )
        content = "".join(parts).strip()
        output_tokens = self._context_budget_planner.token_counter.count_text(
            content + json.dumps(tool_calls)
        )
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
            reservation_id=reservation_id,
        )

    def _settle(self, response: CollectedModelResponse) -> None:
        if self._task_budget is not None and response.reservation_id is not None:
            self._task_budget.settle(response.reservation_id, response.tokens)

    async def _execute_tool(self, *args: Any, **kwargs: Any) -> ToolExecutionResult:
        pending = asyncio.create_task(asyncio.to_thread(self._tools.execute, *args, **kwargs))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancel = getattr(self._tools, "cancel", None)
            if cancel is not None:
                await asyncio.to_thread(cancel)
            with suppress(Exception):
                await pending
            raise

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

    def _checkpoint(self, callback: GraphCheckpoint | None, graph: ExecutionGraph) -> None:
        if self._task_budget is not None:
            self._task_budget.checkpoint(graph, self._live_messages)
        if callback is not None:
            callback(graph)
