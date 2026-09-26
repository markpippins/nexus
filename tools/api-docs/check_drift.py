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
    - resolution-srv — registry-driven dynamic routes (GET /api/<table> from
                       src/tables.ts); route extraction cannot enumerate the
                       concrete table list, so the surface is documented by its
                       TypeSpec contract (typespec/v1/resolution-srv) instead
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
EXCLUDED = {"semantics-srv", "pty-srv", "terrain-srv", "resolution-srv"}

# Moleculer ports pinned to an INCUMBENT service's committed openapi.yaml.
# The port carries NO spec of its own: one contract, two implementations, one
# gate. Drift here means the alias map moved, not that a spec is stale — so
# these keys are never regenerated (see --update below).
MOLECULER_MIRRORS = {
    "moleculer/voyager": "typescript/voyager-srv",
    "moleculer/cascade": "typescript/cascade-srv",
    "moleculer/kernel": "typescript/kernel-srv",
    "moleculer/draft": "typescript/draft-srv",
    "moleculer/knowledge": "typescript/knowledge-srv",
    "moleculer/role-memory": "typescript/role-memory-srv",
    "moleculer/semantics": "typescript/semantics-srv",
    "moleculer/tackle": "typescript/tackle-srv",
    "moleculer/prompt-sync": "typescript/tackle-prompt-sync-srv",
    "moleculer/execution": "typescript/execution-srv",
    "moleculer/harness": "typescript/harness-srv",
}


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
            full = os.path.join(ROOT, base, name)
            if os.path.isdir(full) and name.endswith("-srv") and name not in EXCLUDED:
                services[f"{base}/{name}"] = full
    # JVM port modules (Spring) — same drift discipline as the *-srv services.
    for key, rel in er.JVM_SERVICES.items():
        full = os.path.join(ROOT, rel)
        if os.path.isdir(full):
            services[key] = full
    # Moleculer apps (moleculer-web gateways).
    for key, rel in er.MOLECULER_SERVICES.items():
        full = os.path.join(ROOT, rel)
        if os.path.isdir(full):
            services[key] = full
    return services


def extract_surface(key, svc_dir):
    """Route inventory for a service — Express/FastAPI, Moleculer, or Spring."""
    if key in er.MOLECULER_SERVICES:
        return er.process_moleculer_service(svc_dir, key)
    if key.startswith("jvm/"):
        return er.process_spring_service(svc_dir, key)
    return er.process_service(svc_dir, key.split("/")[-1])


def contract_dir(key, svc_dir):
    """Where the spec this service is judged against lives.

    For a moleculer port that is the incumbent service's dir (the port is a
    second implementation of an existing contract); for everything else it is
    the service's own dir.
    """
    mirror = MOLECULER_MIRRORS.get(key)
    if mirror:
        return os.path.join(ROOT, mirror)
    return svc_dir


def verify_all(services):
    """Compute the per-service drift report: {key: {status, ...}}."""
    report = {}
    for key, svc_dir in sorted(services.items()):
        contract = MOLECULER_MIRRORS.get(key)
        spec_path = os.path.join(contract_dir(key, svc_dir), "openapi.yaml")
        if not os.path.exists(spec_path):
            report[key] = {
                "status": "missing",
                "detail": "no committed openapi.yaml" + (f" at contract {contract}" if contract else ""),
                "contract": contract or key,
            }
            continue
        endpoints = extract_surface(key, svc_dir)
        source_surface = {(e["method"], to_openapi_form(e["path"])) for e in endpoints}
        try:
            committed = committed_surface(spec_path)
        except Exception as e:
            report[key] = {"status": "unparseable", "detail": str(e), "contract": contract or key}
            continue
        missing = sorted(source_surface - committed)  # in source, absent from committed spec
        extra = sorted(committed - source_surface)    # in committed spec, absent from source
        if missing or extra:
            report[key] = {
                "status": "drift",
                "missing": missing,
                "extra": extra,
                "contract": contract or key,
            }
        else:
            report[key] = {
                "status": "ok",
                "endpoints": len(source_surface),
                "contract": contract or key,
            }
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
            inventory[key] = extract_surface(key, svc_dir)
        import tempfile

        tmp = os.path.join(tempfile.gettempdir(), "api_inventory_drift.json")
        with open(tmp, "w") as f:
            json.dump(inventory, f)
        # Mirrored moleculer ports are judged against the INCUMBENT's spec:
        # regenerating that spec from the incumbent's routes cannot fix an
        # alias-map drift, it would only rewrite an unchanged spec. Report and
        # skip instead, so --update never masks a port/contract divergence.
        mirrored = {k: v for k, v in problems.items() if k in MOLECULER_MIRRORS}
        for k, v in sorted(mirrored.items()):
            print(f"  ! {k}: cannot regenerate — judged against {v.get('contract')} "
                  f"(fix the gateway alias map, not the spec)")
        targets = [k.split("/")[-1] for k in problems if k not in MOLECULER_MIRRORS]
        if targets:
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
                suffix = f" (contract {v['contract']})" if v.get("contract") and v["contract"] != key else ""
                print(f"OK   {key}: {v['endpoints']} endpoints{suffix}")
            elif v["status"] == "missing":
                print(f"FAIL {key}: no committed openapi.yaml")
            elif v["status"] == "unparseable":
                print(f"FAIL {key}: openapi.yaml does not parse: {v['detail']}")
            else:
                where = f" vs contract {v['contract']}" if v.get("contract") != key else ""
                print(f"FAIL {key}: drift{where}")
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
