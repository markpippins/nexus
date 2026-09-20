#!/usr/bin/env python3
"""Generate JVM golden parity vectors from the Python WRP reference.

Run from the repository root:

    python3 jvm/spring/nexus-core/nexus-core-wrp/generate-golden-fixtures.py

Writes wrp-golden-vectors.json into the JVM module's test resources. The
Java side (WrpGoldenParityTest) must reproduce every value byte-for-byte.

Vectors cover the zero-dep kernel substrate:
  - CAL addressing (make/parse/content_hash, unicode + explicit-version)
  - CCNF canonical JSON byte shapes (Go serializer rules)
  - entity_key derivation (normalize + derive emission path, per-domain
    scopes, error cases)
  - state machine transitions (legal/illegal, receipt mapping)
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
def _find_repo_root():
    """Walk upward from this file to locate the repo root (owns python/).

    Depth-agnostic so the generator runs identically from a worktree, a CI
    checkout, or any future directory reshuffle.
    """
    probe = HERE
    for _ in range(10):
        if os.path.isdir(os.path.join(probe, "python")):
            return probe
        probe = os.path.dirname(probe)
    raise RuntimeError(
        "repo root with python/ not found above " + HERE
    )


REPO_ROOT = _find_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "python"))

from nexus_core.wrp.addressing import content_hash, make_address, parse_address
from nexus_core.wrp.identity import canonical_json, derive_identity, domain_to_scope, emit_identity, normalize_intent
from nexus_core.wrp.states import RECEIPT_TO_WRP_STATE, WRP_ADJACENCY_MATRIX, is_valid_transition


def main() -> None:
    vectors = {
        "_meta": {
            "generator": "generate-golden-fixtures.py",
            "reference": "nexus/python/nexus_core/wrp (Python 3.12, zero-dep substrate)",
            "note": "Java must reproduce every value byte-for-byte; Go binary + Rust verifier remain CCNF conformance authority",
        },
        "addressing": [],
        "canonical_json_bytes": [],
        "identity": [],
        "identity_errors": [],
        "states": {"matrix": {k: sorted(v) for k, v in WRP_ADJACENCY_MATRIX.items()},
                   "receipt_map": RECEIPT_TO_WRP_STATE},
    }

    # -- CAL addressing vectors (mirror wr-conf-011's golden set + unicode) --
    locations = [
        ("dev", "my-pipeline", "t0", "transform"),
        ("prod", "graph-2", "traj-1", "node-9"),
        ("dev", "pipeline-with-ünïcode", "t1", "nöde"),
        ("", "", "", ""),
    ]
    for realm, graph, traj, node in locations:
        addr = make_address(realm, graph, traj, node)
        vectors["addressing"].append({
            "realm": realm, "graph": graph, "trajectory": traj, "node_id": node,
            "expected_address": addr,
            "expected_version": addr.split("/")[-1],
            "expected_content_hash": content_hash(f"{realm}/{graph}/{traj}/{node}"),
            "explicit_version_address": make_address(realm, graph, traj, node, "v9"),
            "parsed": parse_address(addr),
            "parsed_non_cal": parse_address(f"http://{realm}/{graph}"),
        })

    # -- CCNF canonical JSON byte cases (Go serializer rules) --
    byte_cases = [
        {"value": None, "expected": "null"},
        {"value": True, "expected": "true"},
        {"value": 7, "expected": "7"},
        {"value": {"b": 2, "a": 1}, "expected": '{"a":1,"b":2}'},
        {"value": {"k": "a\u0001b"}, "expected": '{"k":"a\\u0001b"}'},
        {"value": {"s": "quote\" back\\slash\nnewline\ttab\rcr"},
         "expected": '{"s":"quote\\" back\\\\slash\\nnewline\\ttab\\rcr"}'},
        {"value": {"list": [1, "two", None, True], "nested": {"z": 1, "y": {"deep": []}}},
         "expected": '{"list":[1,"two",null,true],"nested":{"y":{"deep":[]},"z":1}}'},
        {"value": {"float": 2.5, "integral": 3.0},
         "expected": '{"float":2.5,"integral":3}'},
    ]
    for case in byte_cases:
        vectors["canonical_json_bytes"].append({
            "value": case["value"],
            "expected_json": case["expected"],
            "expected_sha256": hashlib.sha256(case["expected"].encode("utf-8")).hexdigest(),
        })

    # -- identity vectors: emission path (normalize + derive), per-domain --
    docs = [
        {"event_id": "wr-0001",
         "actor": {"type": "system", "id": "conduit"},
         "intent": {"action": "execute", "target_type": "workrequest",
                    "target_id": "workrequest:wr-0001"},
         "domain": "execution"},
        {"event_id": "wr-spec",
         "actor": {"type": "dba", "id": "luna"},
         "intent": {"action": "create", "target_type": "plan", "target_id": "plan:1"},
         "domain": "specification"},
        {"event_id": "wr-sys",
         "actor": {"type": "system", "id": "tackle"},
         "intent": {"action": "validate", "target_type": "schema", "target_id": "schema:x"},
         "domain": "system"},
        {"event_id": "wr-other",
         "actor": {"type": "human", "id": "bp"},
         "intent": {"action": "emit", "target_type": "event", "target_id": "event:9"},
         "domain": "custom-domain"},
        # actor absent → null in canonical JSON
        {"event_id": "wr-noactor",
         "intent": {"action": "delete", "target_type": "row", "target_id": "row:1"},
         "domain": "execution"},
    ]
    for doc in docs:
        key, etype, scope = emit_identity(dict(doc))
        vectors["identity"].append({
            "doc": doc,
            "expected_entity_key": key,
            "expected_event_type": etype,
            "expected_scope": scope,
            "expected_domain_scope": domain_to_scope(doc.get("domain", "")),
        })

    # -- error vectors (Go INTENT_NORMALIZATION_FAILURE contract) --
    error_cases = [
        {"label": "free_text_intent", "intent": "just do the thing"},
        {"label": "unknown_action", "intent": {"action": "vibes", "target_type": "x", "target_id": "y"}},
        {"label": "empty_action", "intent": {"action": "", "target_type": "x", "target_id": "y"}},
    ]
    for case in error_cases:
        try:
            normalize_intent(case["intent"])
            raise AssertionError(f"expected ValueError for {case['label']}")
        except ValueError as exc:
            vectors["identity_errors"].append({
                "label": case["label"],
                "intent": case["intent"],
                "expected_error": str(exc),
            })

    # -- state transition vectors --
    transitions = []
    for from_state, allowed in sorted(WRP_ADJACENCY_MATRIX.items()):
        for to_state in sorted(allowed):
            transitions.append({"from": from_state, "to": to_state, "valid": True})
    for from_state, to_state in [("CREATED", "FAILED"), ("COMPLETED", "FAILED"),
                                 ("INTAKE", "EXECUTING"), ("ARCHIVED", "INTAKE")]:
        transitions.append({"from": from_state, "to": to_state, "valid": is_valid_transition(from_state, to_state)})
    vectors["state_transitions"] = transitions

    out_path = os.path.join(HERE, "src", "test", "resources", "wrp-golden-vectors.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(vectors, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {out_path}")
    print(f"  addressing vectors:     {len(vectors['addressing'])}")
    print(f"  canonical byte cases:   {len(vectors['canonical_json_bytes'])}")
    print(f"  identity vectors:       {len(vectors['identity'])}")
    print(f"  identity error vectors: {len(vectors['identity_errors'])}")
    print(f"  state transitions:      {len(vectors['state_transitions'])}")


if __name__ == "__main__":
    main()
