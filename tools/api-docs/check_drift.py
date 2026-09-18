#!/usr/bin/env python3
"""Drift check: does each committed *-srv openapi.yaml match the live source routes?

Runs the route extractor over the current source and compares the resulting
(method, path) inventory against each committed `openapi.yaml` (both sides
normalized to OpenAPI `{param}` form).

Exit codes:
    0  — all specs current
    1  — drift detected (or a spec is unparseable / missing for an expected service)

Excluded services:
    - semantics-srv  — its openapi.yaml is derived from the TABLES registry by
                       its own generator (scripts/generate-openapi.ts), not from
                       route extraction
    - pty-srv        — RETIRED (M3); WebSocket-only, intentionally no openapi.yaml (tree retained until M3 close-out)
    - terrain-srv    — retired

Usage:
    python tools/api-docs/check_drift.py                # verify
    python tools/api-docs/check_drift.py --update       # regenerate ONLY drifted specs
    python tools/api-docs/check_drift.py --quiet        # only print problems
    python tools/api-docs/check_drift.py --json         # machine-readable report
"""
import argparse
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

sys.path.insert(0, HERE)
import extract_routes as er  # noqa: E402
import gen_openapi as go     # noqa: E402

# Services whose openapi.yaml is NOT produced by the generic extractor pipeline.
EXCLUDED = {"semantics-srv", "pty-srv", "terrain-srv"}


def to_openapi_form(p):
    """Normalize an extractor path to OpenAPI {param} form.

    Express sources yield `:param` paths; FastAPI decorators already use
    `{param}`. Committed openapi.yaml paths are always `{param}`, so both
    sides are compared in that form.
    """
    import re

    return re.sub(r":([A-Za-z_][\w]*)", r"{\1}", p)


def committed_surface(spec_path):
    """Return {(METHOD, path)} from a committed openapi.yaml (paths already {param})."""
    with open(spec_path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    paths = spec.get("paths") or {}
    surface = set()
    for opath, ops in paths.items():
        for method, op in ops.items():
            if not isinstance(op, dict) or not op:
                continue
            if method.lower() not in ("get", "post", "put", "patch", "delete", "options", "head"):
                continue
            surface.add((method.upper(), opath))
    return surface


def find_services():
    services = {}
    for base in ("typescript", "python"):
        d = os.path.join(ROOT, base)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            if os.path.isdir(full) and name.endswith("-srv") and name not in EXCLUDED:
                services[f"{base}/{name}"] = full
    return services


def verify_all(services):
    """Compute the per-service drift report: {key: {status, ...}}."""
    report = {}
    for key, svc_dir in sorted(services.items()):
        spec_path = os.path.join(svc_dir, "openapi.yaml")
        if not os.path.exists(spec_path):
            report[key] = {"status": "missing", "detail": "no committed openapi.yaml"}
            continue
        endpoints = er.process_service(svc_dir, key.split("/")[-1])
        source_surface = {(e["method"], to_openapi_form(e["path"])) for e in endpoints}
        try:
            committed = committed_surface(spec_path)
        except Exception as e:
            report[key] = {"status": "unparseable", "detail": str(e)}
            continue
        missing = sorted(source_surface - committed)  # in source, absent from committed spec
        extra = sorted(committed - source_surface)    # in committed spec, absent from source
        if missing or extra:
            report[key] = {"status": "drift", "missing": missing, "extra": extra}
        else:
            report[key] = {"status": "ok", "endpoints": len(source_surface)}
    return report


def main():
    ap = argparse.ArgumentParser(description="Verify *-srv openapi.yaml matches source routes.")
    ap.add_argument("--update", action="store_true", help="regenerate drifted specs instead of failing")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    args = ap.parse_args()

    services = find_services()
    report = verify_all(services)
    problems = {k: v for k, v in report.items() if v["status"] != "ok"}

    if args.update and problems:
        print("Regenerating specs for drifted services...")
        # regenerate ONLY the problematic services (vision's live spec is never
        # touched unless it drifted, so a down vision-srv can't cause spurious
        # rewrites of its committed FastAPI-native spec)
        inventory = {}
        for key, svc_dir in services.items():
            inventory[key] = er.process_service(svc_dir, key.split("/")[-1])
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "api_inventory_drift.json")
        with open(tmp, "w") as f:
            json.dump(inventory, f)
        targets = [k.split("/")[-1] for k in problems]
        go.main(["--inventory", tmp, "--root", ROOT, "--only", ",".join(targets)])
        # re-verify to confirm the refresh landed
        report = verify_all(services)
        problems = {k: v for k, v in report.items() if v["status"] != "ok"}
        if problems:
            print("\nDrift remains after regeneration:")
            for k, v in sorted(problems.items()):
                print(f"  - {k}: {v.get('detail', 'drift')}")

    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))

    if not args.quiet:
        for key, v in sorted(report.items()):
            if v["status"] == "ok":
                print(f"OK   {key}: {v['endpoints']} endpoints")
            elif v["status"] == "missing":
                print(f"FAIL {key}: no committed openapi.yaml")
            elif v["status"] == "unparseable":
                print(f"FAIL {key}: openapi.yaml does not parse: {v['detail']}")
            else:
                print(f"FAIL {key}: drift")
                for m in v["missing"]:
                    print(f"       in source, not in spec: {m[0]} {m[1]}")
                for m in v["extra"]:
                    print(f"       in spec, not in source: {m[0]} {m[1]}")

    if problems:
        print("\nDrift detected. Regenerate with: python tools/api-docs/check_drift.py --update")
        return 1
    if not args.quiet:
        print("\nAll committed specs are current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
