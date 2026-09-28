#!/usr/bin/env python3
"""Run the CI-safe tier of bin/tests/ — the gate that ff3264f0 asked for.

Finding ff3264f0: no workflow ran bin/tests/ as a set. Seven workflows
mention the directory but each names individual files under narrow `paths:`
filters, so green CI did not mean the guards held. This runner is the single
place that decides what "the bin/tests suite" means, and the workflow calls
it, so CI and a developer at a terminal run exactly the same thing.

Tiers live in bin/tests/CI_MANIFEST.json and were derived by measurement,
not assumption — see that file's $comment. Only `gated` runs here; the other
tiers are reported as excluded, with their reasons, so the exclusion is
visible in the log rather than silent.

    bin/run-bin-tests.py                  # run the gated tier (default)
    bin/run-bin-tests.py --list           # show the manifest, run nothing
    bin/run-bin-tests.py --tier service_dependent
    bin/run-bin-tests.py --per-file       # one pytest per file, exact attribution

Exit status is 0 only if every gated file passed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = REPO / "bin" / "tests" / "CI_MANIFEST.json"

TIERS = ("gated", "service_dependent", "dsn_gated", "known_red")


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except FileNotFoundError:
        sys.exit(
            f"missing manifest: {MANIFEST}\n"
            "It is the single source of truth for what CI runs; without it "
            "there is no gate. Restore it rather than bypassing this runner."
        )
    except json.JSONDecodeError as exc:
        sys.exit(f"corrupt manifest {MANIFEST}: {exc}")


def files_in(manifest: dict, tier: str) -> list[str]:
    if tier == "gated":
        return list(manifest.get("gated", []))
    return [e["file"] for e in manifest.get(tier, [])]


def report_excluded(manifest: dict, running: str) -> None:
    for tier in TIERS:
        if tier == running:
            continue
        entries = manifest.get(tier, [])
        if not entries:
            continue
        print(f"\n--- not run: {len(entries)} file(s) in tier '{tier}' ---")
        for e in entries:
            reason = " ".join(e.get("reason", "").split())
            print(f"  {e['file']}\n      {reason}")


def run_one(path: str, timeout: int) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"bin/tests/{path}", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tier", choices=TIERS, default="gated",
                    help="which tier to run (default: gated — the CI-safe set)")
    ap.add_argument("--per-file", action="store_true",
                    help="one pytest process per file (exact per-file "
                         "attribution, slower). Default is a single "
                         "invocation over the whole tier.")
    ap.add_argument("--list", action="store_true",
                    help="print the manifest and exit without running")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-file timeout in seconds (--per-file only)")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.list:
        for line in manifest.get("$comment", []):
            print(f"# {line}")
        for tier in TIERS:
            print(f"\n[{tier}] {len(files_in(manifest, tier))} file(s)")
            for f in files_in(manifest, tier):
                print(f"  {f}")
        return 0

    files = files_in(manifest, args.tier)
    if not files:
        sys.exit(f"tier '{args.tier}' is empty in {MANIFEST.name}")

    print(f"bin/tests gate — tier '{args.tier}', {len(files)} file(s)")

    if args.per_file:
        failed = []
        for f in files:
            ok, out = run_one(f, args.timeout)
            print(f"  {'PASS' if ok else 'FAIL'}  {f}", flush=True)
            if not ok:
                failed.append((f, out))
        for f, out in failed:
            print(f"\n===== {f} =====\n{out}")
    else:
        targets = [f"bin/tests/{f}" for f in files]
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *targets, "-q",
             "-p", "no:cacheprovider", "--continue-on-collection-errors"],
            cwd=REPO, text=True, stdin=subprocess.DEVNULL,
        )
        failed = []

    if args.tier != "gated":
        print(f"\ntier '{args.tier}' is not the CI gate; "
              "only 'gated' is expected to be green by construction.")
    else:
        report_excluded(manifest, args.tier)

    if not args.per_file and proc.returncode != 0:
        return proc.returncode
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
