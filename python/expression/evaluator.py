"""Expression-to-SOLScript/Resolution evaluation-only adapter.

This adapter prepares and evaluates requests through an injected pure callback.
It never persists, admits, ratifies, mutates entities, invokes transitions, or
writes the knowledge graph. The callback is the seam for a live SOLScript /
Resolution implementation; the envelope is the stable E4 contract around it.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any

from .boundary import assert_boundary

OUTCOMES = {
    "asserted",
    "rejected",
    "disputed",
    "insufficient_evidence",
    "pending",
    "unevaluable",
    "refused",
    "uncertain",
    "advisory",
    "stale",
}

# Archived ResolutionInterpreter dispositions map into this stable wire vocabulary.
DISPOSITION_MAP = {
    "Asserted": "asserted",
    "Disputed": "disputed",
    "Rejected": "rejected",
    "Pending": "pending",
    "Proposed": "advisory",
    "Stale": "stale",
    "Retracted": "refused",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_set_fingerprint(read_set: dict[str, Any] | None) -> str | None:
    if read_set is None:
        return None
    supplied = read_set.get("fingerprint")
    return str(supplied) if supplied else _digest(read_set)


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
    observation_ids = {observation.get("observation_id") for observation in bundle.get("observations", [])}
    missing = [item for item in proposition.get("source_observation_ids", []) if item not in observation_ids]
    if missing:
        raise ValueError(f"proposition references missing observations: {missing}")
    if bundle.get("authority_status") != "non_authoritative":
        raise ValueError("Expression bundles must remain non_authoritative")
    assert_boundary(bundle)
    if not evaluator_revision or not ontology_revision or not authority_owner:
        raise ValueError("evaluator, ontology, and authority identities are required")

    request = {
        "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
        "source_fingerprint": bundle.get("source_fingerprint"),
        "transcript_id": bundle.get("transcript_id"),
        "proposition": copy.deepcopy(proposition),
        "source_observation_ids": list(proposition.get("source_observation_ids", [])),
        "read_set": copy.deepcopy(read_set),
        "read_set_fingerprint": _read_set_fingerprint(read_set),
        "evaluator_revision": evaluator_revision,
        "ontology_revision": ontology_revision,
        "authority_owner": authority_owner,
        "authority_status": "evaluation_only",
        "mutation_policy": "forbidden",
    }
    request["request_fingerprint"] = _digest(request)
    return request


def _normalize_callback_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize live/interpreter dispositions without granting authority."""
    result = copy.deepcopy(result)
    raw = result.get("disposition")
    disposition = DISPOSITION_MAP.get(str(raw), str(raw).lower() if raw is not None else "")
    if disposition not in OUTCOMES:
        return {
            "disposition": "refused",
            "reason_code": "invalid_evaluator_disposition",
            "reason": f"Unsupported evaluator disposition: {raw!r}",
        }
    result["disposition"] = disposition
    if disposition in {"asserted", "rejected", "disputed"}:
        result.setdefault("authority_status", "advisory")
    else:
        result.setdefault("authority_status", "evaluation_only")
    return result


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

    Missing capabilities are explicit `pending`/`unevaluable` outcomes. A
    callback result is advisory only. Unknown dispositions fail closed to
    `refused`; callback mutation of its request copy cannot mutate the caller's
    bundle or read-set.
    """
    results: list[dict[str, Any]] = []
    for proposition in bundle.get("proposition_candidates", []):
        try:
            request = build_evaluation_request(
                bundle, proposition, read_set=read_set,
                evaluator_revision=evaluator_revision,
                ontology_revision=ontology_revision,
                authority_owner=authority_owner,
            )
        except ValueError as exc:
            results.append({"proposition_id": proposition.get("proposition_id"), "disposition": "refused", "reason_code": "invalid_expression_bundle", "reason": str(exc), "request_fingerprint": None, "authority_status": "evaluation_only"})
            continue

        if read_set is None:
            result = {"disposition": "unevaluable", "reason_code": "missing_read_set", "reason": "A pinned read-set is required before evaluation."}
        elif evaluator is None:
            result = {"disposition": "pending", "reason_code": "evaluator_unavailable", "reason": "No SOLScript/Resolution evaluator was supplied."}
        else:
            result = _normalize_callback_result(evaluator(copy.deepcopy(proposition), copy.deepcopy(request)))

        result = _normalize_callback_result(result)
        result.update({"proposition_id": proposition.get("proposition_id"), "request_fingerprint": request["request_fingerprint"]})
        results.append(result)

    envelope = {
        "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
        "source_fingerprint": bundle.get("source_fingerprint"),
        "evaluator_revision": evaluator_revision,
        "ontology_revision": ontology_revision,
        "authority_owner": authority_owner,
        "read_set_fingerprint": _read_set_fingerprint(read_set),
        "authority_status": "evaluation_only",
        "mutation_policy": "forbidden",
        "results": sorted(results, key=lambda item: item["proposition_id"] or ""),
    }
    envelope["evaluation_fingerprint"] = _digest(envelope)
    return envelope


def replay_evaluation(bundle: dict[str, Any], prior: dict[str, Any], *, read_set: dict[str, Any] | None, evaluator_revision: str, ontology_revision: str, authority_owner: str, evaluator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Re-evaluate and classify whether the deterministic result agrees."""
    current = evaluate_bundle(bundle, read_set=read_set, evaluator_revision=evaluator_revision, ontology_revision=ontology_revision, authority_owner=authority_owner, evaluator=evaluator)
    return {"prior_fingerprint": prior.get("evaluation_fingerprint"), "current_fingerprint": current["evaluation_fingerprint"], "match": prior.get("evaluation_fingerprint") == current["evaluation_fingerprint"], "current": current}
