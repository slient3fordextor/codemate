import pytest

from app.agent.graph import (
    CriterionAssessment,
    CriterionKind,
    CriterionState,
    EdgeTrigger,
    EvaluationContract,
    EvaluationCriterion,
    EvaluationEvidence,
    EvaluationVerdict,
    EvidenceKind,
    ExecutionGraph,
    GraphLoop,
    LoopBudget,
    LoopDecisionType,
    LoopState,
    LoopStep,
    NodeState,
    NodeType,
    ReActAction,
    ReActBudget,
    ReActDecision,
)
from app.agent.graph.models import GraphEdge
from app.agent.graph.runtime import GraphStateError


def test_graph_branches_and_promotes_independent_reads_in_parallel() -> None:
    graph = ExecutionGraph()
    graph.add_node("read_code", "Read code", NodeType.READ)
    graph.add_node("read_tests", "Read tests", NodeType.READ)
    graph.add_node("analyse", "Analyse", NodeType.MODEL)
    graph.add_edge(GraphEdge("read_code", "analyse"))
    graph.add_edge(GraphEdge("read_tests", "analyse"))

    assert {node.id for node in graph.ready_nodes()} == {"read_code", "read_tests"}
    for node_id in ("read_code", "read_tests"):
        graph.start(node_id)
        graph.succeed(node_id)

    assert [node.id for node in graph.ready_nodes()] == ["analyse"]


def test_waiting_approval_requires_explicit_resume() -> None:
    graph = ExecutionGraph()
    graph.add_node("patch", "Apply patch", NodeType.APPLY_PATCH)
    graph.start("patch")
    graph.wait_for_approval("patch", {"approval_id": "approval-1", "diff": "example"})

    assert graph.node("patch").state is NodeState.WAITING
    graph.resume("patch", approval_id="approval-1", decided_by="user")
    assert graph.node("patch").state is NodeState.READY


def test_graph_loop_expands_iterations_without_back_edges() -> None:
    graph = ExecutionGraph()
    loop = GraphLoop(
        "repair",
        (
            LoopStep("Diagnose", NodeType.MODEL, {}),
            LoopStep("Verify", NodeType.COMMAND, {}),
        ),
        LoopBudget(max_iterations=2, max_total_tokens=100),
    )
    first_iteration = loop.start(graph)
    graph.start(first_iteration[0])
    graph.succeed(first_iteration[0])
    graph.start(first_iteration[1])
    graph.succeed(first_iteration[1], {"passed": False})

    decision = loop.continue_after_failure(
        graph,
        first_iteration[1],
        passed=False,
        used_tokens=40,
    )

    assert decision.type is LoopDecisionType.CONTINUE
    second_iteration = decision.node_ids
    assert graph.node(second_iteration[0]).input["iteration"] == 2
    assert graph.node(second_iteration[0]).state is NodeState.READY
    graph.start(second_iteration[0])
    graph.succeed(second_iteration[0])
    graph.start(second_iteration[1])
    graph.succeed(second_iteration[1], {"passed": False})
    exhausted = loop.continue_after_failure(
        graph,
        second_iteration[1],
        passed=False,
        used_tokens=60,
    )
    assert exhausted.type is LoopDecisionType.BUDGET_EXHAUSTED
    assert "iteration_limit_reached" in (exhausted.reason or "")
    assert "token_limit_reached" in (exhausted.reason or "")


def test_cycles_and_invalid_state_changes_are_rejected() -> None:
    graph = ExecutionGraph()
    graph.add_node("a", "A", NodeType.READ)
    graph.add_node("b", "B", NodeType.READ)
    graph.add_edge(GraphEdge("a", "b", EdgeTrigger.SUCCESS))

    with pytest.raises(GraphStateError):
        graph.add_edge(GraphEdge("b", "a", EdgeTrigger.SUCCESS))
    with pytest.raises(GraphStateError):
        graph.succeed("a")


def test_public_node_snapshots_cannot_mutate_graph_state() -> None:
    graph = ExecutionGraph()
    graph.add_node("read", "Read", NodeType.READ, input={"paths": ["app.py"]})

    snapshot = graph.node("read")
    snapshot.state = NodeState.CANCELLED
    snapshot.input["paths"].append("secret.txt")

    current = graph.node("read")
    assert current.state is NodeState.PENDING
    assert current.input == {"paths": ["app.py"]}


def test_incoming_edges_are_frozen_after_target_activation() -> None:
    graph = ExecutionGraph()
    graph.add_node("a", "A", NodeType.READ)
    graph.add_node("b", "B", NodeType.READ)
    graph.ready_nodes()

    with pytest.raises(GraphStateError, match="left pending"):
        graph.add_edge(GraphEdge("a", "b"))


def test_graph_events_replay_topology_state_attempt_and_output() -> None:
    graph = ExecutionGraph("graph-1")
    graph.add_node("read", "Read", NodeType.READ)
    graph.add_node("analyse", "Analyse", NodeType.MODEL)
    graph.add_edge(GraphEdge("read", "analyse"))
    graph.start("read")
    graph.succeed("read", {"files": 2})
    graph.start("analyse")
    graph.wait_for_approval("analyse", {"approval_id": "approval-1"})

    replayed = ExecutionGraph.from_events(graph.events)

    assert replayed.graph_id == graph.graph_id
    assert replayed.node("read").attempt_count == 1
    assert replayed.node("read").output == {"files": 2}
    assert replayed.node("analyse").state is NodeState.WAITING
    assert replayed.events == graph.events


def test_react_budget_requires_progress_then_explicit_approval() -> None:
    budget = ReActBudget()

    assert budget.next_decision(9, has_new_evidence=False) is ReActDecision.CONTINUE
    assert budget.next_decision(10, has_new_evidence=False) is ReActDecision.STOP
    assert budget.next_decision(10, has_new_evidence=True) is ReActDecision.CONTINUE
    assert budget.next_decision(15, has_new_evidence=True) is ReActDecision.WAIT_FOR_APPROVAL
    assert (
        budget.next_decision(15, has_new_evidence=True, approved_round=16) is ReActDecision.CONTINUE
    )


def test_react_budget_requires_approval_after_replan_limit() -> None:
    budget = ReActBudget(max_replans=1)

    assert (
        budget.next_decision(
            1,
            has_new_evidence=True,
            replans=1,
            requested_action=ReActAction.REPLAN,
        )
        is ReActDecision.WAIT_FOR_APPROVAL
    )
    assert (
        budget.next_decision(
            1,
            has_new_evidence=True,
            replans=1,
            requested_action=ReActAction.REPLAN,
            approved_replan=2,
        )
        is ReActDecision.CONTINUE
    )

    assert budget.next_decision(1, has_new_evidence=True, replans=1) is ReActDecision.CONTINUE


def test_loop_replan_approval_resumes_without_double_counting_tokens() -> None:
    graph = ExecutionGraph()
    loop = GraphLoop(
        "repair",
        (LoopStep("Verify", NodeType.COMMAND, {}),),
        LoopBudget(max_iterations=3, max_total_tokens=100, max_replans=0),
    )
    first = loop.start(graph)
    graph.start(first[0])
    graph.succeed(first[0], {"passed": False})

    waiting = loop.continue_after_failure(graph, first[0], passed=False, used_tokens=25)
    assert waiting.type is LoopDecisionType.WAIT_FOR_APPROVAL
    assert waiting.state == LoopState(
        iterations=1,
        used_tokens=25,
        pending_replan_from=first[0],
        processed_validation_ids=(first[0],),
    )

    resumed = loop.resume_after_replan_approval(graph, first[0], approval_id="approval-1")
    assert resumed.type is LoopDecisionType.CONTINUE
    assert resumed.state.used_tokens == 25
    assert resumed.state.replans == 1
    assert resumed.state.pending_replan_from is None
    assert resumed.state.last_replan_approval_id == "approval-1"

    with pytest.raises(GraphStateError, match="already been consumed"):
        loop.continue_after_failure(graph, first[0], passed=False, used_tokens=25)


def test_approval_resume_must_match_pending_approval_id() -> None:
    graph = ExecutionGraph()
    graph.add_node("patch", "Patch", NodeType.APPLY_PATCH)
    graph.start("patch")
    graph.wait_for_approval("patch", {"approval_id": "approval-1"})

    with pytest.raises(GraphStateError, match="does not match"):
        graph.resume("patch", approval_id="approval-2", decided_by="user")


def test_evaluation_contract_separates_hard_gates_from_soft_preferences() -> None:
    contract = EvaluationContract(
        (
            EvaluationCriterion(
                "permission", CriterionKind.HARD, evidence_kinds=(EvidenceKind.APPROVAL,)
            ),
            EvaluationCriterion(
                "verification", CriterionKind.HARD, evidence_kinds=(EvidenceKind.COMMAND,)
            ),
            EvaluationCriterion(
                "style",
                CriterionKind.SOFT,
                preference_weight=2,
                evidence_kinds=(EvidenceKind.COMMAND,),
            ),
        )
    )

    result = contract.evaluate(
        {
            "permission": CriterionAssessment(CriterionState.PASS, ("approval-1",)),
            "verification": CriterionAssessment(CriterionState.PASS, ("test-1",)),
            "style": CriterionAssessment(CriterionState.UNKNOWN),
        },
        {
            "approval-1": EvaluationEvidence("approval-1", EvidenceKind.APPROVAL, "approval-node"),
            "test-1": EvaluationEvidence("test-1", EvidenceKind.COMMAND, "test-node", 1),
        },
    )

    assert result.verdict is EvaluationVerdict.COMPLETED
    assert result.quality_score == 0.0
    assert result.unmet_preferences == ("style",)


def test_hard_pass_without_registered_evidence_cannot_complete() -> None:
    contract = EvaluationContract(
        (EvaluationCriterion("tests", CriterionKind.HARD, evidence_kinds=(EvidenceKind.COMMAND,)),)
    )

    result = contract.evaluate(
        {"tests": CriterionAssessment(CriterionState.PASS, ("missing-test",))}
    )

    assert result.verdict is EvaluationVerdict.NEEDS_EVIDENCE
    assert result.hard_unknowns == ("tests",)
    assert result.invalid_evidence == ("tests",)


def test_not_applicable_soft_criterion_is_excluded_from_quality_score() -> None:
    contract = EvaluationContract(
        (
            EvaluationCriterion(
                "tests", CriterionKind.HARD, evidence_kinds=(EvidenceKind.COMMAND,)
            ),
            EvaluationCriterion(
                "style",
                CriterionKind.SOFT,
                preference_weight=2,
                evidence_kinds=(EvidenceKind.COMMAND,),
            ),
            EvaluationCriterion(
                "docs",
                CriterionKind.SOFT,
                preference_weight=8,
                evidence_kinds=(EvidenceKind.FILE,),
            ),
        )
    )
    result = contract.evaluate(
        {
            "tests": CriterionAssessment(CriterionState.PASS, ("test-1",)),
            "style": CriterionAssessment(CriterionState.PASS, ("style-1",)),
            "docs": CriterionAssessment(CriterionState.NOT_APPLICABLE),
        },
        {
            "test-1": EvaluationEvidence("test-1", EvidenceKind.COMMAND, "test-node", 1),
            "style-1": EvaluationEvidence("style-1", EvidenceKind.COMMAND, "lint-node", 1),
        },
    )

    assert result.verdict is EvaluationVerdict.COMPLETED
    assert result.quality_score == 1.0
    assert result.unmet_preferences == ()


def test_minimum_soft_quality_can_request_bounded_improvement() -> None:
    contract = EvaluationContract(
        (
            EvaluationCriterion(
                "tests", CriterionKind.HARD, evidence_kinds=(EvidenceKind.COMMAND,)
            ),
            EvaluationCriterion(
                "style",
                CriterionKind.SOFT,
                preference_weight=1,
                evidence_kinds=(EvidenceKind.COMMAND,),
            ),
        ),
        minimum_quality_score=0.5,
    )
    result = contract.evaluate(
        {"tests": CriterionAssessment(CriterionState.PASS, ("test-1",))},
        {"test-1": EvaluationEvidence("test-1", EvidenceKind.COMMAND, "test-node", 1)},
    )

    assert result.verdict is EvaluationVerdict.NEEDS_IMPROVEMENT


def test_evidence_from_wrong_dimension_cannot_satisfy_hard_criterion() -> None:
    contract = EvaluationContract(
        (EvaluationCriterion("tests", CriterionKind.HARD, evidence_kinds=(EvidenceKind.COMMAND,)),)
    )
    result = contract.evaluate(
        {"tests": CriterionAssessment(CriterionState.PASS, ("approval-1",))},
        {"approval-1": EvaluationEvidence("approval-1", EvidenceKind.APPROVAL, "approval-node")},
    )

    assert result.verdict is EvaluationVerdict.NEEDS_EVIDENCE
    assert result.invalid_evidence == ("tests",)
