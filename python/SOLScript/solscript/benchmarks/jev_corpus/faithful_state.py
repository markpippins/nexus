"""Faithful per-case state builder for the Jev benchmark corpus (Jev 6).

The naive benchmark state builder mapped every unknown field to the string
``'present'`` — so ``readiness_score`` arrived as ``'present'`` instead of
``0.92`` and ``valid_license`` as ``'present'`` instead of ``False``. Any
calibration delta measured against that state is meaningless: the model never
saw the evidence the reference answer was computed from.

This module materializes, for each corpus case, the concrete state values
implied by the case's own ``reference_answer`` rationale (e.g. noul_001's
``readiness_score=0.92 > 0.85``, noul_007's ``7 of 12 evidence items``).
All values are synthetic/redacted, matching the corpus itself.

Contract:
- ``faithful_state(case_or_id)`` returns a dict covering every field in the
  case's ``evidence_scope.required_state_fields``.
- Lookup failure (unknown case id, or a corpus case with no entry here) raises
  loudly — a silent fallback would reintroduce the exact bias this module
  exists to remove. When the corpus gains a case, add its entry here.
"""

from __future__ import annotations

from typing import Any, Dict, Union

from . import JevBenchmarkCase, get_case_by_id, get_corpus


# ── Per-case states (values implied by each case's reference rationale) ──

FAITHFUL_STATES: Dict[str, Dict[str, Any]] = {
    # noul_001: "readiness_score=0.92 > 0.85" → ref P=1.0
    "noul_001": {"readiness_score": 0.92},
    # noul_002: "license_expiry=2024-01-15 < now" → ref P=0.0
    "noul_002": {"valid_license": False, "license_expiry": "2024-01-15"},
    # noul_003: "drift_evidence shows transcript pattern match 0.87" → ref 0.85
    "noul_003": {
        "drift_type": "transcript_drift",
        "drift_evidence": {"transcript_pattern_match": 0.87},
    },
    # noul_004: "all guard_conditions satisfied per rule R-204" → ref 0.95
    "noul_004": {
        "guard_conditions": {"R-204": "satisfied", "all_satisfied": True},
        "entity_attributes": {"status": "active"},
    },
    # noul_005: "identity_match=0.72, below 0.85 threshold" → ref 0.72
    "noul_005": {
        "representation_comparison": {"identity_delta": 0.28},
        "identity_match": 0.72,
    },
    # noul_006: "no duplicates found in shrapnel facts" → ref 0.99
    "noul_006": {
        "asset_id": "asset-042",
        "canonical_asset_id": "asset-042",
        "duplicates": [],
    },
    # noul_007: "7 of 12 required evidence items present" → ref 0.65
    "noul_007": {"evidence_items": 7, "required_evidence": 12},
    # noul_008: "historical avg 18h, current 6h elapsed, no blockers" → ref 0.78
    "noul_008": {
        "current_state": "InProgress",
        "transition_history": {"avg_hours": 18, "elapsed_hours": 6, "blockers": []},
        "sla_deadline": "24h",
    },
    # choice_001: evidence points at transcript_drift (abrupt transcript shift)
    "choice_001": {
        "drift_magnitude": 0.62,
        "attribute_diffs": ["transcript_checksum_mismatch"],
        "temporal_pattern": "abrupt_transcript_shift",
    },
    # choice_002: small cosmetic-level delta → ref "minor"
    "choice_002": {
        "identity_delta": 0.12,
        "attribute_deltas": ["display_label"],
        "relationship_deltas": [],
    },
    # choice_003: 0.78 with one evidence gap → ref "nearly_ready"
    "choice_003": {
        "readiness_score": 0.78,
        "blocking_issues": ["evidence_gap_1"],
        "evidence_coverage": 0.8,
    },
    # choice_004: three corroborating sources → ref "grounded"
    "choice_004": {
        "grounding_evidence": ["source_a", "source_b", "source_c"],
        "source_reliability": 0.9,
        "corroboration_count": 3,
    },
    # choice_005: medium value/urgency/cost, low risk → ref "P2_medium"
    "choice_005": {
        "candidate_value": 0.6,
        "urgency": "medium",
        "resource_cost": "medium",
        "dependency_risk": "low",
    },
    # choice_006: guards passed, 75% history → ref "committed"
    "choice_006": {
        "transition_id": "t-1042",
        "pre_state": "pending",
        "guard_results": "passed",
        "historical_success_rate": 0.75,
    },
    # score_001: strong dimensions, no blockers → ref 0.82
    "score_001": {
        "readiness_dimensions": {"evidence": 0.85, "validity": 0.85, "blockers": 0.1},
        "blocking_criteria": [],
        "evidence_matrix": {"coverage": 0.85},
    },
    # score_002: good-but-not-faithful representation → ref 0.76
    "score_002": {
        "identity_match": 0.8,
        "attribute_fidelity": 0.75,
        "relationship_preservation": 0.8,
        "completeness": 0.8,
    },
    # score_003: decent evidence bundle → ref 0.68
    "score_003": {
        "evidence_items": 8,
        "source_diversity": 3,
        "recency": "recent",
        "corroboration": "high",
    },
    # score_004: mid-70s package → ref 0.73
    "score_004": {
        "concept_coverage": 0.75,
        "attribute_completeness": 0.75,
        "relationship_integrity": 0.8,
        "evidence_bundle": {"present": True},
    },
    # score_005: clean governance slate → ref 0.91
    "score_005": {
        "policy_violations": 0,
        "guard_compliance": 0.95,
        "audit_trail_completeness": "complete",
        "authorization_status": "valid",
    },
}


def faithful_state(case: Union[str, JevBenchmarkCase]) -> Dict[str, Any]:
    """Return the faithful state dict for a corpus case.

    Accepts a case id or a :class:`JevBenchmarkCase`. Raises ``KeyError`` for
    unknown ids and ``AssertionError`` if the stored entry does not cover every
    field in the case's ``required_state_fields`` (fail loudly — never fall
    back to synthetic ``'present'`` placeholders).
    """
    if isinstance(case, str):
        resolved = get_case_by_id(case)
        if resolved is None:
            raise KeyError(f"Unknown corpus case id: {case!r}")
        case = resolved
    if case.id not in FAITHFUL_STATES:
        raise KeyError(
            f"No faithful state entry for case {case.id!r} — add one to FAITHFUL_STATES"
        )
    state = dict(FAITHFUL_STATES[case.id])
    missing = [
        f for f in case.evidence_scope.required_state_fields if f not in state
    ]
    assert not missing, (
        f"Faithful state for {case.id!r} is missing required fields: {missing}"
    )
    return state


def assert_full_coverage() -> None:
    """Assert every corpus case has a complete faithful-state entry."""
    for case in get_corpus():
        faithful_state(case)
