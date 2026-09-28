#!/usr/bin/env python3
"""Gestalt pressure source #5: executable contracts vs operational reality.

Counts, rather than judges, the gap between a service's contract of record and
the spec its consumers actually read. Three classes:

  typed-contract-ignored
      The service HAS a TypeSpec contract but its openapi.yaml still declares
      untyped bodies. A typed contract exists and is being ignored -- the
      contradiction class. Highest leverage: the contract is already written.

  unmodeled-boundary
      The service has no TypeSpec contract. Nothing to be inconsistent with
      yet, but the boundary is unmodelled.

  uncompiled-contract
      A TypeSpec contract exists but `npx tsp compile` has never run for it, so
      there is no compiled OpenAPI to point a consumer at. This is the pipeline
      gap: the contract is written but not built.

Two existing checks cover the *path* surface and cannot see any of this:
`check_drift.py` compares {(METHOD, path)} sets only, and the apidocs
byte-identical gate verifies the generator is deterministic -- which is what
locks the untyped body in place. Determinism is not correctness.

Runs non-blocking against a committed ratchet baseline, because the counts are
large today and a merge gate would hold every correction behind the first.
The baseline only moves down.

  (default)  report, fail only if any class grew above baseline
  --strict   fail on any non-zero class
  --json     machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BASELINE = HERE / "contract-pressure-baseline.json"

sys.path.insert(0, str(HERE))
import gen_openapi as gen  # noqa: E402


def ops(spec_path) -> set:
    try:
        doc = yaml.safe_load(open(spec_path, encoding="utf-8", errors="ignore")) or {}
    except Exception:
        return set()
    out = set()
    for path, ops_ in (doc.get("paths") or {}).items():
        for method in ops_:
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                out.add((method.upper(), path))
    return out


def untyped_bodies(spec_path) -> int:
    """How many success responses still resolve to the JsonBody placeholder."""
    try:
        doc = yaml.safe_load(open(spec_path, encoding="utf-8", errors="ignore")) or {}
    except Exception:
        return 0
    n = 0
    for ops_ in (doc.get("paths") or {}).values():
        if not isinstance(ops_, dict):
            continue
        for method, op in ops_.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            for resp in (op.get("responses") or {}).values():
                schema = (((resp or {}).get("content") or {})
                          .get("application/json") or {}).get("schema") or {}
                if schema.get("$ref", "").endswith("/JsonBody"):
                    n += 1
    return n


def scan() -> dict:
    classes = {"typed-contract-ignored": [], "unmodeled-boundary": [],
               "uncompiled-contract": []}
    detail = {}
    for key in gen.SERVICES:
        if key in gen.SKIPPED_KEYS:
            continue
        contract = gen.contract_of_record(key)  # single resolution rule
        spec = gen.spec_path(key)

        if contract is None:
            if os.path.isfile(spec):
                classes["unmodeled-boundary"].append(key)
            continue

        if contract["compiled"] is None:
            classes["uncompiled-contract"].append(key)
        if os.path.isfile(spec):
            bodies = untyped_bodies(spec)
            if bodies:
                classes["typed-contract-ignored"].append(key)
            detail[key] = {"contract": contract, "untyped_success_bodies": bodies}
    return {"classes": classes, "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = scan()
    classes = data["classes"]
    counts = {k: len(v) for k, v in classes.items()}
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}

    if args.json:
        # Pure JSON on stdout: a machine reader must not have to strip the
        # human ratchet report that follows.
        print(json.dumps({"counts": counts, "classes": classes,
                          "detail": data["detail"]}, indent=2))
        return 0
    else:
        print("Gestalt pressure #5 -- contract of record vs operational reality")
        for name, items in classes.items():
            print(f"\n  {name} ({len(items)}):")
            for k in items:
                extra = ""
                if name == "typed-contract-ignored":
                    extra = (f"  [{data['detail'][k]['untyped_success_bodies']} untyped bodies"
                             f" -> contract {data['detail'][k]['contract']['source']}]")
                print(f"    {k}{extra}")

    if args.strict:
        bad = sum(counts.values())
        if bad:
            print(f"\nFAIL: {bad} pressure item(s) under --strict", file=sys.stderr)
            return 1
        print("\nOK: no contract pressure")
        return 0

    grew = [n for n, c in counts.items() if c > baseline.get(n, 0)]
    if grew:
        for n in grew:
            print(f"\nFAIL: {n} rose above baseline "
                  f"({counts[n]}/{baseline.get(n, 0)}) -- pressure must not grow",
                  file=sys.stderr)
        return 1
    print("\nbaseline (ratchet floor, only moves down):")
    for n, c in counts.items():
        print(f"    {n:26} {c:>3} / {baseline.get(n, 0):>3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
