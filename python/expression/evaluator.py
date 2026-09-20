"""Expression-to-SOLScript/Resolution adapter seam.

The adapter prepares governed evaluation requests and normalizes evaluator
results. It never persists, admits, ratifies, mutates entities, or writes the
knowledge graph. A real SOLScript interpreter can be supplied later through
the evaluator callback once proposition semantics are mapped to concepts and
rules.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from .boundary import assert_boundary

DISPOSITIONS = {
    "asserted",
    "rejected",
    "disputed",
    "insufficient_evidence",
    "unevaluable",
    "stale",
    "refused",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def build_evaluation_request(
    bundle: dict[str, Any],
    proposition: dict[str, Any],
    *,
    read_set: dict[str, Any] | None,
    evaluator_revision: str,
    ontology_revision: str,
    authority_owner: str,
) -> dict[str, Any]:
    """Build the pinned request passed to a SOLScript/Resolution evaluator."""
    observation_ids = set()
    for observation in bundle.get("observations", []):
        observation_ids.add(observation.get("observation_id"))
    missing = [
        item for item in proposition.get("source_observation_ids", [])
        if item not in observation_ids
    ]
    if missing:
        raise ValueError(f"proposition references missing observations: {missing}")
    if bundle.get("authority_status") != "non_authoritative":
        raise ValueError("Expression bundles must remain non_authoritative")
    assert_boundary(bundle)

    request = {
        "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
        "source_fingerprint": bundle.get("source_fingerprint"),
        "transcript_id": bundle.get("transcript_id"),
        "proposition": proposition,
        "source_observation_ids": proposition.get("source_observation_ids", []),
        "read_set": read_set,
        "evaluator_revision": evaluator_revision,
        "ontology_revision": ontology_revision,
        "authority_owner": authority_owner,
        "authority_status": "evaluation_only",
    }
    request["request_fingerprint"] = _digest(request)
    return request


def evaluate_bundle(
    bundle: dict[str, Any],
    *,
    read_set: dict[str, Any] | None,
    evaluator_revision: str,
    ontology_revision: str,
    authority_owner: str,
    evaluator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate candidate propositions through an injected pure evaluator.

    Without an evaluator or read-set, results are explicit `unevaluable`
    outcomes. This makes capability loss visible instead of treating missing
    dependencies as rejection. The callback must return a disposition and
    reason; unknown dispositions are converted to `refused`.
    """
    results: list[dict[str, Any]] = []
    for proposition in bundle.get("proposition_candidates", []):
        try:
            request = build_evaluation_request(
                bundle,
                proposition,
                read_set=read_set,
                evaluator_revision=evaluator_revision,
                ontology_revision=ontology_revision,
                authority_owner=authority_owner,
            )
        except ValueError as exc:
            results.append(
                {
                    "proposition_id": proposition.get("proposition_id"),
                    "disposition": "refused",
                    "reason_code": "invalid_expression_bundle",
                    "reason": str(exc),
                    "request_fingerprint": None,
                }
            )
            continue

        if read_set is None:
            result = {
                "disposition": "unevaluable",
                "reason_code": "missing_read_set",
                "reason": "A pinned read-set is required before evaluation.",
            }
        elif evaluator is None:
            result = {
                "disposition": "unevaluable",
                "reason_code": "evaluator_unavailable",
                "reason": "No SOLScript/Resolution evaluator was supplied.",
            }
        else:
            result = dict(evaluator(proposition, request))

        disposition = result.get("disposition", "refused")
        if disposition not in DISPOSITIONS:
            disposition = "refused"
            result["reason_code"] = "invalid_evaluator_disposition"
        result.update(
            {
                "proposition_id": proposition.get("proposition_id"),
                "disposition": disposition,
                "request_fingerprint": request["request_fingerprint"],
            }
        )
        results.append(result)

    envelope = {
        "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
        "source_fingerprint": bundle.get("source_fingerprint"),
        "evaluator_revision": evaluator_revision,
        "ontology_revision": ontology_revision,
        "authority_owner": authority_owner,
        "authority_status": "evaluation_only",
        "results": sorted(results, key=lambda item: item["proposition_id"] or ""),
    }
    envelope["evaluation_fingerprint"] = _digest(envelope)
    return envelope


def replay_evaluation(
    bundle: dict[str, Any],
    prior: dict[str, Any],
    *,
    read_set: dict[str, Any] | None,
    evaluator_revision: str,
    ontology_revision: str,
    authority_owner: str,
    evaluator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-evaluate and classify whether the deterministic result agrees."""
    current = evaluate_bundle(
        bundle,
        read_set=read_set,
        evaluator_revision=evaluator_revision,
        ontology_revision=ontology_revision,
        authority_owner=authority_owner,
        evaluator=evaluator,
    )
    return {
        "prior_fingerprint": prior.get("evaluation_fingerprint"),
        "current_fingerprint": current["evaluation_fingerprint"],
        "match": prior.get("evaluation_fingerprint") == current["evaluation_fingerprint"],
        "current": current,
    }
