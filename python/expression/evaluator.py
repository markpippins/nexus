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
    source_link_gate: dict[str, Any] | None = None,
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
        "source_link_gate": copy.deepcopy(source_link_gate),
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


def _is_resolved_source_link_gate(gate: dict[str, Any]) -> bool:
    """Validate the resolver's positive evidence, not just its eligible flag."""
    if gate.get("eligible") is not True:
        return False
    relation = gate.get("relation_resolution")
    links = gate.get("source_link_resolutions")
    if not gate.get("relation_name") or not isinstance(relation, dict) or relation.get("status") != "resolved":
        return False
    if not isinstance(links, list):
        return False
    if "source_observation_ids" not in gate or "source_link_observation_ids" not in gate:
        return False
    if gate.get("unresolved_source_link_ids") is None or gate.get("unresolved_source_link_ids"):
        return False
    if any(not item for item in gate["source_observation_ids"]):
        return False
    if any(not item for item in gate["source_link_observation_ids"]):
        return False
    if any(not isinstance(link, dict) or link.get("status") != "resolved" for link in links):
        return False
    resolved_link_ids = {link.get("observation_id") for link in links}
    if resolved_link_ids != set(gate["source_link_observation_ids"]):
        return False
    return True


def evaluate_bundle(
    bundle: dict[str, Any],
    *,
    read_set: dict[str, Any] | None,
    evaluator_revision: str,
    ontology_revision: str,
    authority_owner: str,
    evaluator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    source_link_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate candidate propositions through an injected pure evaluator.

    Missing capabilities are explicit `pending`/`unevaluable` outcomes. A
    callback result is advisory only. Unknown dispositions fail closed to
    `refused`; callback mutation of its request copy cannot mutate the caller's
    bundle or read-set.
    """
    results: list[dict[str, Any]] = []
    gates = {
        item.get("proposition_id"): item
        for item in (source_link_report or {}).get("proposition_gates", [])
        if isinstance(item, dict) and item.get("proposition_id") is not None
    }
    for proposition in bundle.get("proposition_candidates", []):
        gate = gates.get(proposition.get("proposition_id"))
        if source_link_report is not None and not gate:
            results.append({
                "proposition_id": proposition.get("proposition_id"),
                "disposition": "refused",
                "reason_code": "source_link_gate_missing",
                "reason": "No source-link gate was supplied for this proposition.",
                "request_fingerprint": None,
                "authority_status": "evaluation_only",
                "source_link_gate": None,
            })
            continue
        if gate is not None and not _is_resolved_source_link_gate(gate):
            results.append({
                "proposition_id": proposition.get("proposition_id"),
                "disposition": "refused",
                "reason_code": "source_link_unresolved",
                "reason": "; ".join(gate.get("reasons", [])) or "Source-link gate did not prove every link and relation resolved.",
                "request_fingerprint": None,
                "authority_status": "evaluation_only",
                "source_link_gate": copy.deepcopy(gate),
            })
            continue
        try:
            request = build_evaluation_request(
                bundle, proposition, read_set=read_set,
                evaluator_revision=evaluator_revision,
                ontology_revision=ontology_revision,
                authority_owner=authority_owner,
                source_link_gate=gate,
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
        if gate is not None:
            result["source_link_gate"] = copy.deepcopy(gate)
        results.append(result)

    envelope = {
        "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
        "source_fingerprint": bundle.get("source_fingerprint"),
        "evaluator_revision": evaluator_revision,
        "ontology_revision": ontology_revision,
        "authority_owner": authority_owner,
        "read_set_fingerprint": _read_set_fingerprint(read_set),
        "source_link_report": copy.deepcopy(source_link_report),
        "authority_status": "evaluation_only",
        "mutation_policy": "forbidden",
        "results": sorted(results, key=lambda item: item["proposition_id"] or ""),
    }
    envelope["evaluation_fingerprint"] = _digest(envelope)
    return envelope


def replay_evaluation(bundle: dict[str, Any], prior: dict[str, Any], *, read_set: dict[str, Any] | None, evaluator_revision: str, ontology_revision: str, authority_owner: str, evaluator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None, source_link_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-evaluate and classify whether the deterministic result agrees."""
    current = evaluate_bundle(bundle, read_set=read_set, evaluator_revision=evaluator_revision, ontology_revision=ontology_revision, authority_owner=authority_owner, evaluator=evaluator, source_link_report=source_link_report)
    return {"prior_fingerprint": prior.get("evaluation_fingerprint"), "current_fingerprint": current["evaluation_fingerprint"], "match": prior.get("evaluation_fingerprint") == current["evaluation_fingerprint"], "current": current}
