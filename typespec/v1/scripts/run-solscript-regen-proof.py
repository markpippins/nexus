#!/usr/bin/env python3
"""run-solscript-regen-proof.py — P2 regeneration proof for the SOLScript
TypeSpec contract.

Runs the full chain of custody end-to-end and asserts parity with the
reference implementation:

  1. Emit the .tsp contract to OpenAPI JSON (npx tsp, in this worktree).
  2. Regenerate Python contract stubs via solscript_codegen.py into staging
     (a disposable sandbox — staging/ is never authoritative).
  3. Import the generated stubs and the reference dataclasses
     (python/SOLScript/solscript/{models,adapters/contract,events}.py)
     and assert FIELD-SET parity per model and VALUE-SET parity per enum.

Exit 0 = PARITY PROVEN. Any mismatch exits 1 with a per-model diff table.

Usage:
  python3 typespec/v1/scripts/run-solscript-regen-proof.py \
      [--reference /home/codex/dev/nexus/python/SOLScript] [--skip-emit]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import fields as dc_fields
from enum import Enum
from pathlib import Path

V1_DIR = Path(__file__).resolve().parent.parent
STAGING = V1_DIR / "staging" / "solscript-regen"
OPENAPI_JSON = STAGING / "openapi-json" / "schema" / "openapi.json"
CODEGEN = V1_DIR / "scripts" / "solscript_codegen.py"
STUBS = STAGING / "solscript_contracts.py"

# Reference models compared field-for-field (generated name == reference name).
MODEL_PAIRS = [
    "AttributeBinding", "ConceptAttribute", "RelationshipBinding", "Expression",
    "Rule", "ConceptRelationship", "ConceptStateTransition", "Concept",
    "Entity", "RepresentationIdentity", "RepresentationComparison",
    "Representation", "FrameDimension", "FrameDimensionValue",
    "PropositionFrameValue", "FrameDimensionMeaning", "Proposition",
    "FunctionBinding",
    # adapters/contract.py
    "ContractConcept", "ContractAttribute", "ContractRelationship",
    "ContractSubject", "ContractShrapnelFact", "ContractRevision",
    "ContractEvidence",
    # events.py
    "ReadSetManifest", "KeychainEvent",
]

# Generated enum -> (reference module attr, reference enum name)
ENUM_PAIRS = [
    ("ExpressionKind", "models", "ExpressionKind"),
    ("SolOperator", "models", "Operator"),
    ("Quantifier", "models", "Quantifier"),
    ("RuleType", "models", "RuleType"),
    ("Severity", "models", "Severity"),
    ("Disposition", "models", "Disposition"),
]

# Projection-only models with no reference counterpart (informational).
PROJECTION_ONLY = [
    "EvaluationOptions", "EvaluationResult", "RuleCheckResult",
    "GuardCheckResult", "TransitionResult", "SolQuerySpec", "SolQueryFilter",
    "KeychainEventEnvelope", "TransitionReadSet", "SolInterpreterOperation",
    "JsonValue",
]

# Reference fields deliberately EXCLUDED from the wire contract, with the
# reason. The proof FAILS if a new unexplained gap appears, and also fails
# if an exclusion here no longer matches the reference (keeps this table
# honest as the reference evolves).
KNOWN_WIRE_EXCLUSIONS = {
    "FunctionBinding": {
        "python_func": "Callable binding (models.py python_func: Callable) — executable code, not wire data; the TS port binds implementations instead.",
    },
}


def load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def field_names(cls) -> set:
    return {f.name for f in dc_fields(cls)}


def enum_values(cls) -> set:
    return {m.value for m in cls if isinstance(m, Enum)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reference", type=Path,
                    default=Path("/home/codex/dev/nexus/python/SOLScript"))
    ap.add_argument("--skip-emit", action="store_true",
                    help="reuse the existing staged openapi.json")
    args = ap.parse_args()

    # 1. Emit
    if not args.skip_emit:
        print("[1/3] emitting OpenAPI projection (npx tsp)...")
        r = subprocess.run(
            ["npx", "tsp", "compile", "solscript/python/main.tsp",
             "--emit", "@typespec/openapi3",
             "--option", "@typespec/openapi3.file-type=json",
             "--output-dir", str(STAGING / "openapi-json")],
            cwd=V1_DIR, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:]); print(r.stderr[-2000:])
            print("EMIT FAILED"); return 1
    if not OPENAPI_JSON.exists():
        print(f"missing {OPENAPI_JSON}; run without --skip-emit"); return 1
    doc = json.loads(OPENAPI_JSON.read_text())
    paths = doc.get("paths", {})
    schemas = doc.get("components", {}).get("schemas", {})
    print(f"    paths={list(paths)} (must be route-free) schemas={len(schemas)}")

    # 2. Regenerate stubs
    print("[2/3] regenerating python stubs (solscript_codegen)...")
    r = subprocess.run(
        [sys.executable, str(CODEGEN), str(OPENAPI_JSON), "--out", str(STUBS)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:]); print("CODEGEN FAILED"); return 1
    gen = load_module_from_path("solscript_contracts", STUBS)
    print(f"    {r.stdout.strip()}")

    # 3. Parity vs reference
    print("[3/3] parity check vs reference implementation...")
    ref_models = load_module_from_path("ref_models", args.reference / "solscript" / "models.py")
    ref_contract = load_module_from_path("ref_contract", args.reference / "solscript" / "adapters" / "contract.py")
    ref_events = load_module_from_path("ref_events", args.reference / "solscript" / "events.py")
    ref_modules = {"models": ref_models, "contract": ref_contract, "events": ref_events}

    failures = []
    rows = []
    for name in MODEL_PAIRS:
        g = getattr(gen, name, None)
        if g is None:
            failures.append(f"{name}: MISSING from generated stubs"); continue
        rmod = next((m for m in ("models", "contract", "events") if hasattr(ref_modules[m], name)), None)
        ref = getattr(ref_modules[rmod], name)
        gf, rf = field_names(g), field_names(ref)
        excluded = KNOWN_WIRE_EXCLUSIONS.get(name, {})
        effective_rf = rf - set(excluded)
        # Guard the exclusion table itself: an exclusion naming a field the
        # reference no longer has is stale and must be fixed.
        stale_exclusions = set(excluded) - rf
        if stale_exclusions:
            failures.append(f"{name}: stale exclusions (reference has no such field): {sorted(stale_exclusions)}")
        if gf == effective_rf:
            note = f"(-{len(excluded)} wire-excluded)" if excluded else ""
            rows.append((name, "OK", len(rf), note))
        else:
            only_ref = sorted(effective_rf - gf); only_gen = sorted(gf - effective_rf)
            rows.append((name, "MISMATCH", len(rf), len(gf)))
            if only_ref: failures.append(f"{name}: only in reference: {only_ref}")
            if only_gen: failures.append(f"{name}: only in generated: {only_gen}")

    for gname, modname, rname in ENUM_PAIRS:
        g = getattr(gen, gname, None)
        ref = getattr(ref_modules[modname], rname, None)
        if g is None or ref is None:
            failures.append(f"enum {gname}/{rname}: missing (gen={g is not None}, ref={ref is not None})"); continue
        gv, rv = enum_values(g), enum_values(ref)
        if gv == rv:
            rows.append((f"{gname}~{rname}", "OK", len(rv), ""))
        else:
            rows.append((f"{gname}~{rname}", "MISMATCH", len(rv), len(gv)))
            failures.append(f"enum {gname}~{rname}: ref-only={sorted(rv - gv)} gen-only={sorted(gv - rv)}")

    print()
    print(f"{'model/enum':38} {'status':10} {'ref':>4} {'gen':>4}")
    print("-" * 62)
    for name, status, a, b in rows:
        print(f"{name:38} {status:10} {a:>4} {b:>4}")
    print(f"\nprojection-only surfaces (no reference counterpart): {len(PROJECTION_ONLY)}")
    print("  " + ", ".join(sorted(PROJECTION_ONLY)))

    if failures:
        print(f"\nPARITY FAILED — {len(failures)} mismatch(es):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"\nPARITY PROVEN — {len(rows)} surfaces match the reference field-for-field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
