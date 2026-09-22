"""Jev benchmark corpus — deterministic SOLScript judgment test cases.

This module provides a fixed, redacted corpus of unresolved SOLScript questions
covering the three TypeSafe primitives:

1. **Noul** (proposition truth) — P(yes) ∈ [0, 1]
2. **Choice** (classification) — per-label probabilities + confidence + flag_for_review
3. **Score** (rubric evaluation) — probability-weighted expected value + legend

Each case includes:
- question: the bounded judgment question
- expected_outcome_type: "noul" | "choice" | "score"
- evidence_scope: required state fields and frame dimensions
- reference_answer: human or existing-rule reference (where available)
- split: "train" | "eval" — for reproducible train/eval separation
- metadata: additional context for calibration measurement

No secrets. All data is synthetic/redacted.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class JevPrimitive(str, Enum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


class CorpusSplit(str, Enum):
    TRAIN = "train"
    EVAL = "eval"


@dataclass
class EvidenceScope:
    """Evidence/read-set scope for the judgment."""
    required_state_fields: List[str] = field(default_factory=list)
    frame_dimensions: List[str] = field(default_factory=list)
    evidence_completeness_threshold: float = 0.8


@dataclass
class JevBenchmarkCase:
    """A single benchmark case for Jev evaluation."""
    id: str
    question: str
    expected_outcome_type: JevPrimitive
    evidence_scope: EvidenceScope
    reference_answer: Optional[Dict[str, Any]] = None
    split: CorpusSplit = CorpusSplit.EVAL
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # For Choice: the label set
    choice_labels: Optional[List[str]] = None
    
    # For Score: the rubric description
    score_rubric: Optional[str] = None


# ── Corpus Definition ──────────────────────────────────────────────────

CORPUS: List[JevBenchmarkCase] = [
    # ========== NOUL CASES (Proposition Truth) ==========
    
    JevBenchmarkCase(
        id="noul_001",
        question="Is the entity's readiness_score greater than 0.85?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["readiness_score"],
            frame_dimensions=["readiness"],
            evidence_completeness_threshold=1.0,
        ),
        reference_answer={"probability": 1.0, "rationale": "readiness_score=0.92 > 0.85"},
        split=CorpusSplit.TRAIN,
        metadata={"difficulty": "easy", "category": "threshold_comparison"},
    ),
    
    JevBenchmarkCase(
        id="noul_002",
        question="Does the proposition 'entity has valid_license' hold given current state?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["valid_license", "license_expiry"],
            frame_dimensions=["compliance"],
            evidence_completeness_threshold=1.0,
        ),
        reference_answer={"probability": 0.0, "rationale": "license_expiry=2024-01-15 < now"},
        split=CorpusSplit.TRAIN,
        metadata={"difficulty": "easy", "category": "temporal_validity"},
    ),
    
    JevBenchmarkCase(
        id="noul_003",
        question="Is the drift_type classified as 'transcript_drift' rather than 'concept_drift'?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["drift_type", "drift_evidence"],
            frame_dimensions=["drift_classification"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={"probability": 0.85, "rationale": "drift_evidence shows transcript pattern match 0.87"},
        split=CorpusSplit.EVAL,
        metadata={"difficulty": "medium", "category": "drift_classification"},
    ),
    
    JevBenchmarkCase(
        id="noul_004",
        question="Will the transition_guard for 'activate' pass given current entity state?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["guard_conditions", "entity_attributes"],
            frame_dimensions=["transition_validity"],
            evidence_completeness_threshold=1.0,
        ),
        reference_answer={"probability": 0.95, "rationale": "all guard_conditions satisfied per rule R-204"},
        split=CorpusSplit.EVAL,
        metadata={"difficulty": "medium", "category": "guard_evaluation"},
    ),
    
    JevBenchmarkCase(
        id="noul_005",
        question="Is the representation 'customer_view' consistent with the canonical representation?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["representation_comparison", "identity_match"],
            frame_dimensions=["representation_consistency"],
            evidence_completeness_threshold=0.85,
        ),
        reference_answer={"probability": 0.72, "rationale": "identity_match=0.72, below 0.85 threshold"},
        split=CorpusSplit.EVAL,
        metadata={"difficulty": "hard", "category": "representation_consistency"},
    ),
    
    JevBenchmarkCase(
        id="noul_006",
        question="Does the entity satisfy invariant 'unique_canonical_asset'?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["asset_id", "canonical_asset_id", "duplicates"],
            frame_dimensions=["asset_uniqueness"],
            evidence_completeness_threshold=1.0,
        ),
        reference_answer={"probability": 0.99, "rationale": "no duplicates found in shrapnel facts"},
        split=CorpusSplit.TRAIN,
        metadata={"difficulty": "easy", "category": "invariant_check"},
    ),
    
    JevBenchmarkCase(
        id="noul_007",
        question="Is the evidence_completeness for proposition P-1042 above 0.9?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["evidence_items", "required_evidence"],
            frame_dimensions=["evidence_completeness"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={"probability": 0.65, "rationale": "7 of 12 required evidence items present"},
        split=CorpusSplit.EVAL,
        metadata={"difficulty": "medium", "category": "evidence_completeness"},
    ),
    
    JevBenchmarkCase(
        id="noul_008",
        question="Will the concept 'ServiceRequest' transition to 'Completed' within 24 hours?",
        expected_outcome_type=JevPrimitive.NOUL,
        evidence_scope=EvidenceScope(
            required_state_fields=["current_state", "transition_history", "sla_deadline"],
            frame_dimensions=["temporal_projection"],
            evidence_completeness_threshold=0.8,
        ),
        reference_answer={"probability": 0.78, "rationale": "historical avg 18h, current 6h elapsed, no blockers"},
        split=CorpusSplit.EVAL,
        metadata={"difficulty": "hard", "category": "temporal_projection"},
    ),
    
    # ========== CHOICE CASES (Classification) ==========
    
    JevBenchmarkCase(
        id="choice_001",
        question="Classify the drift_type for the observed entity divergence.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["drift_magnitude", "attribute_diffs", "temporal_pattern"],
            frame_dimensions=["drift_classification"],
            evidence_completeness_threshold=0.85,
        ),
        reference_answer={
            "label_probabilities": {"concept_drift": 0.12, "transcript_drift": 0.78, "schema_drift": 0.05, "noise": 0.05},
            "top_label": "transcript_drift",
            "flag_for_review": False,
        },
        split=CorpusSplit.TRAIN,
        choice_labels=["concept_drift", "transcript_drift", "schema_drift", "noise"],
        metadata={"difficulty": "easy", "category": "drift_classification", "reference_source": "cpf_compute rules"},
    ),
    
    JevBenchmarkCase(
        id="choice_002",
        question="Classify the representation discrepancy severity.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["identity_delta", "attribute_deltas", "relationship_deltas"],
            frame_dimensions=["representation_quality"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={
            "label_probabilities": {"critical": 0.05, "major": 0.25, "minor": 0.60, "cosmetic": 0.10},
            "top_label": "minor",
            "flag_for_review": True,  # concentration below threshold
        },
        split=CorpusSplit.TRAIN,
        choice_labels=["critical", "major", "minor", "cosmetic"],
        metadata={"difficulty": "medium", "category": "representation_quality"},
    ),
    
    JevBenchmarkCase(
        id="choice_003",
        question="Determine the readiness tier for the current evaluation frame.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["readiness_score", "blocking_issues", "evidence_coverage"],
            frame_dimensions=["readiness"],
            evidence_completeness_threshold=0.8,
        ),
        reference_answer={
            "label_probabilities": {"ready": 0.15, "nearly_ready": 0.55, "needs_work": 0.25, "blocked": 0.05},
            "top_label": "nearly_ready",
            "flag_for_review": False,
        },
        split=CorpusSplit.EVAL,
        choice_labels=["ready", "nearly_ready", "needs_work", "blocked"],
        metadata={"difficulty": "medium", "category": "readiness_tiering"},
    ),
    
    JevBenchmarkCase(
        id="choice_004",
        question="Classify the proposition grounding_status.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["grounding_evidence", "source_reliability", "corroboration_count"],
            frame_dimensions=["grounding_quality"],
            evidence_completeness_threshold=0.85,
        ),
        reference_answer={
            "label_probabilities": {"grounded": 0.70, "partially_grounded": 0.20, "ungrounded": 0.08, "conflicted": 0.02},
            "top_label": "grounded",
            "flag_for_review": False,
        },
        split=CorpusSplit.EVAL,
        choice_labels=["grounded", "partially_grounded", "ungrounded", "conflicted"],
        metadata={"difficulty": "hard", "category": "grounding_classification"},
    ),
    
    JevBenchmarkCase(
        id="choice_005",
        question="Determine the priority tier for candidate triage.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["candidate_value", "urgency", "resource_cost", "dependency_risk"],
            frame_dimensions=["triage_priority"],
            evidence_completeness_threshold=0.8,
        ),
        reference_answer={
            "label_probabilities": {"P0_critical": 0.10, "P1_high": 0.30, "P2_medium": 0.45, "P3_low": 0.15},
            "top_label": "P2_medium",
            "flag_for_review": False,
        },
        split=CorpusSplit.TRAIN,
        choice_labels=["P0_critical", "P1_high", "P2_medium", "P3_low"],
        metadata={"difficulty": "medium", "category": "triage_priority", "reference_source": "PEB prioritization rules"},
    ),
    
    JevBenchmarkCase(
        id="choice_006",
        question="Classify the transition outcome prediction.",
        expected_outcome_type=JevPrimitive.CHOICE,
        evidence_scope=EvidenceScope(
            required_state_fields=["transition_id", "pre_state", "guard_results", "historical_success_rate"],
            frame_dimensions=["transition_prediction"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={
            "label_probabilities": {"committed": 0.75, "refused": 0.15, "rejected": 0.08, "failed": 0.02},
            "top_label": "committed",
            "flag_for_review": False,
        },
        split=CorpusSplit.EVAL,
        choice_labels=["committed", "refused", "rejected", "failed"],
        metadata={"difficulty": "hard", "category": "transition_prediction"},
    ),
    
    # ========== SCORE CASES (Rubric Evaluation) ==========
    
    JevBenchmarkCase(
        id="score_001",
        question="Evaluate the overall readiness of the evaluation frame against the standard rubric.",
        expected_outcome_type=JevPrimitive.SCORE,
        evidence_scope=EvidenceScope(
            required_state_fields=["readiness_dimensions", "blocking_criteria", "evidence_matrix"],
            frame_dimensions=["readiness", "completeness", "validity"],
            evidence_completeness_threshold=0.85,
        ),
        reference_answer={
            "expected_value": 0.82,
            "legend": {"excellent": ">0.90", "good": "0.75-0.90", "acceptable": "0.60-0.75", "needs_improvement": "<0.60"},
        },
        split=CorpusSplit.TRAIN,
        score_rubric="""
Standard Readiness Rubric:
- Evidence completeness (weight 0.30): % of required evidence present
- Validity score (weight 0.25): grounding_status distribution
- Blocking criteria (weight 0.20): count of unresolved blockers
- Consistency score (weight 0.15): representation consistency
- Temporal currency (weight 0.10): evidence freshness
""",
        metadata={"difficulty": "medium", "category": "readiness_rubric", "reference_source": "standard readiness thresholds 0.85/0.75/0.70"},
    ),
    
    JevBenchmarkCase(
        id="score_002",
        question="Score the representation quality against the canonical representation.",
        expected_outcome_type=JevPrimitive.SCORE,
        evidence_scope=EvidenceScope(
            required_state_fields=["identity_match", "attribute_fidelity", "relationship_preservation", "completeness"],
            frame_dimensions=["representation_quality"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={
            "expected_value": 0.76,
            "legend": {"faithful": ">0.85", "mostly_faithful": "0.70-0.85", "divergent": "0.50-0.70", "unreliable": "<0.50"},
        },
        split=CorpusSplit.EVAL,
        score_rubric="""
Representation Quality Rubric:
- Identity match (weight 0.40): canonical_asset_id alignment
- Attribute fidelity (weight 0.30): attribute value preservation
- Relationship preservation (weight 0.20): relationship integrity
- Completeness (weight 0.10): no missing required attributes
""",
        metadata={"difficulty": "hard", "category": "representation_scoring"},
    ),
    
    JevBenchmarkCase(
        id="score_003",
        question="Score the evidence quality for the proposition bundle.",
        expected_outcome_type=JevPrimitive.SCORE,
        evidence_scope=EvidenceScope(
            required_state_fields=["evidence_items", "source_diversity", "recency", "corroboration"],
            frame_dimensions=["evidence_quality"],
            evidence_completeness_threshold=0.8,
        ),
        reference_answer={
            "expected_value": 0.68,
            "legend": {"strong": ">0.80", "adequate": "0.60-0.80", "weak": "0.40-0.60", "insufficient": "<0.40"},
        },
        split=CorpusSplit.EVAL,
        score_rubric="""
Evidence Quality Rubric:
- Source diversity (weight 0.25): number of independent sources
- Recency (weight 0.25): max age of evidence items
- Corroboration (weight 0.30): cross-source agreement
- Completeness (weight 0.20): coverage of required claim types
""",
        metadata={"difficulty": "hard", "category": "evidence_scoring"},
    ),
    
    JevBenchmarkCase(
        id="score_004",
        question="Evaluate the candidate package against the concept package readiness criteria.",
        expected_outcome_type=JevPrimitive.SCORE,
        evidence_scope=EvidenceScope(
            required_state_fields=["concept_coverage", "attribute_completeness", "relationship_integrity", "evidence_bundle"],
            frame_dimensions=["concept_package_readiness"],
            evidence_completeness_threshold=0.85,
        ),
        reference_answer={
            "expected_value": 0.73,
            "legend": {"production_ready": ">0.85", "staging_ready": "0.70-0.85", "development": "0.50-0.70", "incomplete": "<0.50"},
        },
        split=CorpusSplit.TRAIN,
        score_rubric="""
Concept Package Readiness Rubric (Aspect A5):
- Concept coverage (weight 0.25): % of required concepts defined
- Attribute completeness (weight 0.25): % of state attributes populated
- Relationship integrity (weight 0.20): referential integrity of relationships
- Evidence bundle (weight 0.30): grounding evidence per concept
""",
        metadata={"difficulty": "hard", "category": "concept_package", "reference_source": "Aspect A5 deliverable"},
    ),
    
    JevBenchmarkCase(
        id="score_005",
        question="Score the governance compliance of the current frame.",
        expected_outcome_type=JevPrimitive.SCORE,
        evidence_scope=EvidenceScope(
            required_state_fields=["policy_violations", "guard_compliance", "audit_trail_completeness", "authorization_status"],
            frame_dimensions=["governance_compliance"],
            evidence_completeness_threshold=0.9,
        ),
        reference_answer={
            "expected_value": 0.91,
            "legend": {"fully_compliant": ">0.95", "compliant": "0.85-0.95", "minor_issues": "0.70-0.85", "non_compliant": "<0.70"},
        },
        split=CorpusSplit.TRAIN,
        score_rubric="""
Governance Compliance Rubric:
- Policy violations (weight 0.30): count and severity
- Guard compliance (weight 0.25): % of transitions passing guards
- Audit trail (weight 0.20): completeness of decision trail
- Authorization (weight 0.25): proper role/permission alignment
""",
        metadata={"difficulty": "medium", "category": "governance_compliance"},
    ),
]


# ── Corpus Utilities ───────────────────────────────────────────────────

def get_corpus(split: Optional[CorpusSplit] = None) -> List[JevBenchmarkCase]:
    """Get the full corpus or filtered by split."""
    if split is None:
        return CORPUS
    return [case for case in CORPUS if case.split == split]


def get_corpus_by_type(primitive: JevPrimitive, split: Optional[CorpusSplit] = None) -> List[JevBenchmarkCase]:
    """Get corpus filtered by primitive type and optionally split."""
    cases = [case for case in CORPUS if case.expected_outcome_type == primitive]
    if split is not None:
        cases = [case for case in cases if case.split == split]
    return cases


def get_case_by_id(case_id: str) -> Optional[JevBenchmarkCase]:
    """Get a specific case by ID."""
    for case in CORPUS:
        if case.id == case_id:
            return case
    return None


def get_corpus_stats() -> Dict[str, Any]:
    """Get corpus statistics."""
    train_cases = [c for c in CORPUS if c.split == CorpusSplit.TRAIN]
    eval_cases = [c for c in CORPUS if c.split == CorpusSplit.EVAL]
    
    by_type = {}
    for primitive in JevPrimitive:
        by_type[primitive.value] = {
            "train": len([c for c in train_cases if c.expected_outcome_type == primitive]),
            "eval": len([c for c in eval_cases if c.expected_outcome_type == primitive]),
            "total": len([c for c in CORPUS if c.expected_outcome_type == primitive]),
        }
    
    return {
        "total_cases": len(CORPUS),
        "train_cases": len(train_cases),
        "eval_cases": len(eval_cases),
        "by_type": by_type,
        "difficulty_distribution": {
            "easy": len([c for c in CORPUS if c.metadata.get("difficulty") == "easy"]),
            "medium": len([c for c in CORPUS if c.metadata.get("difficulty") == "medium"]),
            "hard": len([c for c in CORPUS if c.metadata.get("difficulty") == "hard"]),
        },
    }


# ── Fixture Export (for test runners) ──────────────────────────────────

# Export as list of dicts for JSON serialization
CORPUS_DICTS = [
    {
        "id": case.id,
        "question": case.question,
        "expected_outcome_type": case.expected_outcome_type.value,
        "evidence_scope": {
            "required_state_fields": case.evidence_scope.required_state_fields,
            "frame_dimensions": case.evidence_scope.frame_dimensions,
            "evidence_completeness_threshold": case.evidence_scope.evidence_completeness_threshold,
        },
        "reference_answer": case.reference_answer,
        "split": case.split.value,
        "metadata": case.metadata,
        "choice_labels": case.choice_labels,
        "score_rubric": case.score_rubric,
    }
    for case in CORPUS
]