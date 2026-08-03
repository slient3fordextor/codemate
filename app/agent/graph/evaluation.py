from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class CriterionState(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CriterionKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class EvaluationVerdict(StrEnum):
    COMPLETED = "completed"
    NEEDS_EVIDENCE = "needs_evidence"
    NEEDS_IMPROVEMENT = "needs_improvement"
    BLOCKED = "blocked"


class EvidenceKind(StrEnum):
    APPROVAL = "approval"
    COMMAND = "command"
    DIFF = "diff"
    FILE = "file"
    MODEL = "model"
    POLICY = "policy"


@dataclass(frozen=True)
class EvaluationEvidence:
    id: str
    kind: EvidenceKind
    source_id: str
    attempt: int = 0
    valid: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.source_id:
            raise ValueError("Evidence id and source id are required")
        if self.attempt < 0:
            raise ValueError("Evidence attempt cannot be negative")


@dataclass(frozen=True)
class EvaluationCriterion:
    key: str
    kind: CriterionKind
    preference_weight: int = 0
    evidence_kinds: tuple[EvidenceKind, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("Evaluation criterion key is required")
        if self.kind is CriterionKind.HARD and self.preference_weight:
            raise ValueError("Hard criteria cannot have a preference weight")
        if self.kind is CriterionKind.SOFT and self.preference_weight <= 0:
            raise ValueError("Soft criteria require a positive preference weight")
        if not self.evidence_kinds:
            raise ValueError("Evaluation criteria must declare accepted evidence kinds")


@dataclass(frozen=True)
class CriterionAssessment:
    state: CriterionState
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    verdict: EvaluationVerdict
    hard_failures: tuple[str, ...]
    hard_unknowns: tuple[str, ...]
    quality_score: float
    unmet_preferences: tuple[str, ...]
    invalid_evidence: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationContract:
    criteria: tuple[EvaluationCriterion, ...]
    minimum_quality_score: float | None = None

    def __post_init__(self) -> None:
        keys = [criterion.key for criterion in self.criteria]
        if len(keys) != len(set(keys)):
            raise ValueError("Evaluation criterion keys must be unique")
        if self.minimum_quality_score is not None and not 0 <= self.minimum_quality_score <= 1:
            raise ValueError("minimum_quality_score must be between zero and one")

    def evaluate(
        self,
        assessments: Mapping[str, CriterionAssessment],
        evidence_catalog: Mapping[str, EvaluationEvidence] | None = None,
    ) -> EvaluationResult:
        unknown_keys = set(assessments) - {criterion.key for criterion in self.criteria}
        if unknown_keys:
            raise ValueError(f"Unknown evaluation criteria: {sorted(unknown_keys)!r}")

        hard_failures: list[str] = []
        hard_unknowns: list[str] = []
        unmet_preferences: list[str] = []
        invalid_evidence: list[str] = []
        quality_total = 0
        quality_met = 0
        evidence_catalog = evidence_catalog or {}
        for criterion in self.criteria:
            assessment = assessments.get(criterion.key, CriterionAssessment(CriterionState.UNKNOWN))
            state = assessment.state
            if state is CriterionState.PASS:
                resolved = [
                    evidence_catalog.get(evidence_id) for evidence_id in assessment.evidence_ids
                ]
                if not resolved or not any(
                    evidence is not None
                    and evidence.valid
                    and evidence.kind in criterion.evidence_kinds
                    for evidence in resolved
                ):
                    invalid_evidence.append(criterion.key)
                    state = CriterionState.UNKNOWN
            if criterion.kind is CriterionKind.HARD:
                if state is CriterionState.FAIL:
                    hard_failures.append(criterion.key)
                elif state in {CriterionState.UNKNOWN, CriterionState.NOT_APPLICABLE}:
                    hard_unknowns.append(criterion.key)
                continue
            if state is CriterionState.NOT_APPLICABLE:
                continue
            quality_total += criterion.preference_weight
            if state is CriterionState.PASS:
                quality_met += criterion.preference_weight
            else:
                unmet_preferences.append(criterion.key)

        if hard_failures:
            verdict = EvaluationVerdict.BLOCKED
        elif hard_unknowns:
            verdict = EvaluationVerdict.NEEDS_EVIDENCE
        else:
            verdict = EvaluationVerdict.COMPLETED
        score = 1.0 if not quality_total else quality_met / quality_total
        if (
            verdict is EvaluationVerdict.COMPLETED
            and self.minimum_quality_score is not None
            and score < self.minimum_quality_score
        ):
            verdict = EvaluationVerdict.NEEDS_IMPROVEMENT
        return EvaluationResult(
            verdict=verdict,
            hard_failures=tuple(hard_failures),
            hard_unknowns=tuple(hard_unknowns),
            quality_score=score,
            unmet_preferences=tuple(unmet_preferences),
            invalid_evidence=tuple(invalid_evidence),
        )
