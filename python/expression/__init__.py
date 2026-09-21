"""Expression v0.1 deterministic transcript observation POC."""

from .compatibility import adapt_observation, adapt_records
from .contract import (
    canonicalize_bundle,
    canonicalize_tag_bundle,
    contract_fingerprint,
    contract_manifest,
)
from .tag_adapter import adapt_tag_record, adapt_tag_records, attach_projected_tags
from .evaluator import (
    build_evaluation_request,
    evaluate_bundle,
    replay_evaluation,
)
from .pipeline import (
    build_candidate_links,
    build_expression_bundle,
    build_proposition_candidates,
    extract_explicit_observations,
    segment_transcript,
)
from .taxonomy import (
    EXPLICIT_KINDS,
    OBSERVATION_KIND_EXPECTATIONS,
    expected_observation_contract,
)

__all__ = [
    "EXPLICIT_KINDS",
    "OBSERVATION_KIND_EXPECTATIONS",
    "adapt_observation",
    "canonicalize_bundle",
    "canonicalize_tag_bundle",
    "contract_fingerprint",
    "contract_manifest",
    "adapt_records",
    "adapt_tag_record",
    "adapt_tag_records",
    "attach_projected_tags",
    "build_evaluation_request",
    "build_expression_bundle",
    "build_candidate_links",
    "evaluate_bundle",
    "expected_observation_contract",
    "replay_evaluation",
    "build_proposition_candidates",
    "extract_explicit_observations",
    "segment_transcript",
]
