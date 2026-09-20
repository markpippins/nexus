#!/usr/bin/env python3
"""Generate JVM golden parity vectors from the Python MEEP reference.

Run from the repository root:

    python3 jvm/spring/nexus-core/nexus-core-meep/generate-golden-fixtures.py

Writes meep-golden-vectors.json into the JVM module's test resources. The
Java side (MeepGoldenParityTest) must reproduce every value byte-for-byte.

Determinism: all timestamps are pinned to GOLDEN_CLOCK via
lower_with_timestamp / fixed clock lambdas, so regeneration is stable.
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "python"))

from meep.ast_parser import parse
from meep.ast_features import extract_features
from meep.irl_classifier import classify
from meep.ir_resolver import resolve
from meep.spec_compiler import compile_selection
from meep.lowering_pass import lower, lower_with_timestamp
from meep.scheduler import schedule
from meep.models import CEREvent, CERLog

GOLDEN_CLOCK = "2026-09-20T00:00:00Z"

PROMPTS = [
    "build a service",          # CONSTRUCTION
    "run the deployment",       # EXECUTION
    "why did this happen",      # REFLECTION
    "merge the two branches",   # RECONCILIATION
    "fix the bug in ServiceBroker",  # REVISION
    "what if we tried X",       # COUNTERFACTUAL
    "audit the compliance",     # AUDIT
    "summarize the log",        # COMPRESSION
    "sanitize the user input",  # CONSTRAINT_INJECTION
    "hello world",              # DEFAULT
]


def canonical(value) -> str:
    """The exact byte shape Python hashes: json.dumps(value, sort_keys=True)."""
    return json.dumps(value, sort_keys=True)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def pipeline_prompt(prompt: str):
    features = extract_features(parse(prompt))
    irl = classify(prompt, ast_features=features)
    sel = resolve(irl)
    wg = compile_selection(sel, prompt)
    eg = lower_with_timestamp(wg, GOLDEN_CLOCK)
    log = schedule(eg, clock=lambda: GOLDEN_CLOCK)
    return irl, sel, wg, eg, log


def main() -> None:
    vectors = {
        "_meta": {
            "generator": "generate-golden-fixtures.py",
            "reference": "nexus/python/meep (Python 3.12)",
            "golden_clock": GOLDEN_CLOCK,
            "note": "Java must reproduce every digest byte-for-byte via CanonicalJson.sha256",
        },
        "canonical_json_bytes": [],
        "classifier": [],
        "graphs": [],
        "cer_chains": [],
        "replay": [],
    }

    # -- canonical JSON byte cases (incl. ensure_ascii, separators, unicode) --
    byte_cases = [
        {"value": {"b": 2, "a": 1}, "expected": canonical({"b": 2, "a": 1})},
        {"value": {"nested": {"z": [1, 2, {"k": "v"}], "y": True, "x": None}},
         "expected": canonical({"nested": {"z": [1, 2, {"k": "v"}], "y": True, "x": None}})},
        {"value": {"emoji": "café ☕ — naïve"},
         "expected": canonical({"emoji": "café ☕ — naïve"})},
        {"value": {"path": "C:\\Users\\test\nnewline\ttab"},
         "expected": canonical({"path": "C:\\Users\\test\nnewline\ttab"})},
        {"value": {}, "expected": "{}"},
        {"value": [], "expected": "[]"},
        {"value": {"n": 3.5, "i": 7}, "expected": canonical({"n": 3.5, "i": 7})},
    ]
    for case in byte_cases:
        vectors["canonical_json_bytes"].append({
            "label": case["expected"][:40],
            "value": case["value"],
            "expected_json": case["expected"],
            "expected_sha256": hashlib.sha256(case["expected"].encode("utf-8")).hexdigest(),
        })

    # -- per-archetype classifier + graph + chain vectors --
    for prompt in PROMPTS:
        irl, sel, wg, eg, log = pipeline_prompt(prompt)

        vectors["classifier"].append({
            "prompt": prompt,
            "archetype": sel.archetype,
            "confidence": round(sel.confidence, 12),
            "probabilities": {k: round(v, 12) for k, v in irl.probabilities.items()},
        })

        vectors["graphs"].append({
            "prompt": prompt,
            "nodes": [{"id": n.id, "label": n.label, "handler": n.handler} for n in eg.nodes],
            "topological_order": list(eg.topological_order),
            "frozen_at": eg.frozen_at,
            "schema_version": eg.schema_version,
            "expected_content_hash": eg.content_hash(),
        })

        events = []
        chain_head = "genesis"
        for event in log.events:
            event_content = {
                "event_id": event.event_id, "timestamp": event.timestamp,
                "execution_id": event.execution_id, "node_id": event.node_id,
                "event_type": event.event_type, "payload": event.payload,
                "prev_event_hash": event.prev_event_hash,
            }
            assert event.prev_event_hash == chain_head, "internal chain drift"
            chain_head = digest(event_content)
            events.append({**event_content, "expected_hash_of_event": chain_head})

        vectors["cer_chains"].append({
            "prompt": prompt,
            "execution_id": log.events[0].execution_id if log.events else "",
            "events": events,
            "expected_tail_hash": log.tail_hash,
        })

    # -- replay semantics vectors (mixed outcomes, partial windows) --
    mixed = CERLog()
    rows = [("s0", "n0", "NODE_START"), ("c0", "n0", "NODE_COMPLETE"),
            ("s1", "n1", "NODE_START"), ("f1", "n1", "NODE_FAIL"),
            ("s2", "n2", "NODE_START"), ("k2", "n2", "NODE_SKIP")]
    for eid, nid, etype in rows:
        mixed.append(CEREvent(event_id=eid, timestamp=GOLDEN_CLOCK, execution_id="ex-mixed",
                              node_id=nid, event_type=etype))
    vectors["replay"].append({
        "label": "mixed_complete_fail_skip",
        "events": [{"event_id": e.event_id, "node_id": e.node_id, "event_type": e.event_type}
                   for e in mixed.events],
        "expected_state": {
            "node_states": {"n0": "COMPLETED", "n1": "FAILED", "n2": "SKIPPED"},
            "completed_nodes": ["n0"], "failed_nodes": ["n1"], "event_count": 6,
            "is_complete": True,
        },
        "partial_until_5": {"event_count": 5, "n2_state": "RUNNING", "is_complete": False},
    })

    out_path = os.path.join(HERE, "src", "test", "resources", "meep-golden-vectors.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(vectors, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {out_path}")
    print(f"  canonical byte cases: {len(vectors['canonical_json_bytes'])}")
    print(f"  graph vectors:        {len(vectors['graphs'])}")
    print(f"  CER chain vectors:    {len(vectors['cer_chains'])}")


if __name__ == "__main__":
    main()
