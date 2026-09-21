"""Expression E5 bounded persistence and projection boundary.

E5 deliberately produces a deterministic persistence *plan* rather than
performing a database write. Resolution is the canonical owner of evaluated
receipts; the graph and Keychains outputs are regenerable projections. This
keeps the slice usable in tests and offline runtimes until the live Resolution
write API is pinned.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


E5_REVISION = "expression-e5-v0.1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _ref(item: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: item.get(key) for key in keys if item.get(key) is not None}


def build_e5_slice(
    bundle: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    read_set: dict[str, Any],
    source_run_id: str,
    retention_class: str = "bounded_review_fixture",
) -> dict[str, Any]:
    """Build one canonical-first E5 artifact without mutating its inputs.

    The returned object has three explicit layers:

    * ``canonical_receipts`` — compact Resolution-owned evaluation receipts;
    * ``graph_projection`` — regenerable nodes/edges, never authority;
    * ``keychain_context`` — references/digests needed for rewind/replay, not
      source content.

    No callback, database, graph, Mongo, or Keychains API is called here.
    """
    if evaluation.get("mutation_policy") != "forbidden":
        raise ValueError("E5 requires a forbidden mutation policy")
    if evaluation.get("authority_status") != "evaluation_only":
        raise ValueError("E5 accepts evaluation-only envelopes")
    if not source_run_id or not read_set:
        raise ValueError("source_run_id and read_set are required")
    if retention_class not in {"bounded_review_fixture", "operational_review"}:
        raise ValueError("unsupported retention class")

    source_fingerprint = bundle.get("source_fingerprint")
    source_refs = [
        _ref(observation["source"], "segment_id", "transcript_id", "text_hash", "start_turn", "end_turn")
        for observation in bundle.get("observations", [])
    ]
    source_refs = sorted(source_refs, key=lambda item: _canonical(item))

    receipts = []
    result_by_id = {item.get("proposition_id"): item for item in evaluation.get("results", [])}
    for proposition in sorted(bundle.get("proposition_candidates", []), key=lambda item: item.get("proposition_id", "")):
        result = result_by_id.get(proposition.get("proposition_id"), {})
        receipt = {
            "receipt_id": _digest({"source": source_fingerprint, "proposition": proposition.get("proposition_id"), "evaluation": evaluation.get("evaluation_fingerprint")})[:32],
            "source_namespace": "expression",
            "source_run_id": source_run_id,
            "target_id": proposition.get("proposition_id"),
            "evaluation_fingerprint": evaluation.get("evaluation_fingerprint"),
            "request_fingerprint": result.get("request_fingerprint"),
            "disposition": result.get("disposition"),
            "reason_code": result.get("reason_code"),
            "authority_status": "evaluation_only",
            "canonical_owner": "resolution",
            "recording_policy": "receipt_only",
        }
        receipts.append(receipt)

    nodes = [
        {"node_id": f"transcript:{bundle.get('transcript_id')}", "kind": "transcript", "authority_status": "projection"}
    ]
    for observation in bundle.get("observations", []):
        nodes.append({
            "node_id": f"observation:{observation['observation_id']}",
            "kind": observation["kind"],
            "authority_status": "projection",
            "source_fingerprint": observation.get("input_fingerprint"),
        })
    for proposition in bundle.get("proposition_candidates", []):
        nodes.append({
            "node_id": f"proposition:{proposition['proposition_id']}",
            "kind": "proposition_candidate",
            "authority_status": "projection",
        })
    edges = []
    for observation in bundle.get("observations", []):
        edges.append({"from": f"transcript:{bundle.get('transcript_id')}", "to": f"observation:{observation['observation_id']}", "relation": "contains"})
    for proposition in bundle.get("proposition_candidates", []):
        for observation_id in proposition.get("source_observation_ids", []):
            edges.append({"from": f"proposition:{proposition['proposition_id']}", "to": f"observation:{observation_id}", "relation": "supported_by"})
    projection_core = {"nodes": sorted(nodes, key=lambda item: item["node_id"]), "edges": sorted(edges, key=_canonical)}
    projection = {
        "projection_id": _digest({"source": source_fingerprint, "run": source_run_id, "core": projection_core})[:32],
        "generation": E5_REVISION,
        "source_run_id": source_run_id,
        "source_fingerprint": source_fingerprint,
        "node_count": len(projection_core["nodes"]),
        "edge_count": len(projection_core["edges"]),
        "authority_status": "projection",
        **projection_core,
    }

    context = {
        "schema_version": 1,
        "checkpoint_class": "evaluation",
        "source_run_id": source_run_id,
        "source_fingerprint": source_fingerprint,
        "read_set_fingerprint": evaluation.get("read_set_fingerprint"),
        "evaluator_revision": evaluation.get("evaluator_revision"),
        "ontology_revision": evaluation.get("ontology_revision"),
        "authority_owner": evaluation.get("authority_owner"),
        "source_refs": source_refs,
        "retention_class": retention_class,
        "source_content_stored": False,
        "authority_status": "context_only",
    }
    context["context_fingerprint"] = _digest(context)

    artifact = {
        "e5_revision": E5_REVISION,
        "source_fingerprint": source_fingerprint,
        "source_run_id": source_run_id,
        "canonical_receipts": receipts,
        "graph_projection": projection,
        "keychain_context": context,
        "rollback": {"status": "not_applicable", "rollback_of": None, "lineage": []},
        "write_policy": {"resolution": "canonical_receipt_only", "graph": "regenerable_projection", "keychains": "context_projection", "mutation": "forbidden"},
    }
    artifact["artifact_fingerprint"] = _digest(artifact)
    return artifact


def rollback_slice(artifact: dict[str, Any], *, rollback_id: str, reason: str) -> dict[str, Any]:
    """Create an append-only rollback marker; never delete the prior artifact."""
    if not rollback_id or not reason:
        raise ValueError("rollback_id and reason are required")
    result = deepcopy(artifact)
    result["rollback"] = {
        "status": "rolled_back",
        "rollback_of": artifact.get("artifact_fingerprint"),
        "rollback_id": rollback_id,
        "reason": reason,
        "lineage": [artifact.get("artifact_fingerprint")],
    }
    result["artifact_fingerprint"] = _digest(result)
    return result


def replay_slice(artifact: dict[str, Any], bundle: dict[str, Any], evaluation: dict[str, Any], *, read_set: dict[str, Any], source_run_id: str) -> dict[str, Any]:
    """Rebuild and compare an E5 artifact from the same pinned inputs."""
    current = build_e5_slice(bundle, evaluation, read_set=read_set, source_run_id=source_run_id, retention_class=artifact["keychain_context"]["retention_class"])
    return {
        "prior_fingerprint": artifact.get("artifact_fingerprint"),
        "current_fingerprint": current.get("artifact_fingerprint"),
        "match": artifact.get("artifact_fingerprint") == current.get("artifact_fingerprint"),
        "current": current,
    }
