#!/usr/bin/env python3
"""pgie-evidence/1 — shared evidence emitter + TAP guard for the
seeded-PostgreSQL integration class.

Single source of authority (Decision 2 doctrine: duplication is a forked
authority) for BOTH consumers:

  - bin/run-pg-integration-e2e.sh (local mirror, PR #607)
  - .github/workflows/broker-e2e.yml job `pg-integration-e2e` (CI)

Subcommands:

  guard LOG FLOOR LABEL RESULTS_TSV
      Applies the CI TAP contract in the canonical order — fail-first,
      skip-must-fail, then the pass floor — against LOG, appends a
      tab-separated result row to RESULTS_TSV, and exits 1 with a
      canonical message on violation (exit 0, silent, on pass).
      fail="unknown" encodes a missing TAP summary line (crash mid-suite).

  emit --verdict V --exit-code N --results-tsv TSV --start-ts TS
       --suite NAME --pg-port P --mongo-port P --kernel-port P
       --legacy-port P --out PATH
      Writes the pgie-evidence/1 artifact (atomic tmp+rename): per-suite
      counts/floors/verdicts + raw TAP log paths from the TSV, totals,
      duration, git head/branch/dirty, stack ports. The CALLER owns the
      verdict (local: pass on the DONE path, fail in die()/ERR trap;
      CI: job.status mapped to pass/fail) — the helper never guesses.

  mint-head --pr N [--repo OWNER/REPO]
      Decision 10 head-minting helper: resolves the PR's CURRENT head via
      `git ls-remote` (never a local checkout, never hand-typed) and prints
      the tag string `head:<sha7>` for use in an attestation agent record's
      tags. The merge gate (merge_pr.py gate 3) compares this tag against
      the PR's headRefOid at merge time, which subsumes the old timestamp
      predicate: any push after the attestation changes headRefOid and fails
      the binding. Copying a tag from another PR — the 2026-09-28
      evidence_provenance_error incident — is defeated because the tag is
      minted from the live remote, not from a prior record.

Artifact covers RUNS, not misuse: usage/preflight errors emit nothing.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

SCHEMA = "pgie-evidence/1"
PASS_RE = re.compile(r"^# pass (\d+)(?:\s|$)", re.M)
FAIL_RE = re.compile(r"^# fail (\d+)(?:\s|$)", re.M)
SKIP_RE = re.compile(r"^# (?:skip|skipped) (\d+)(?:\s|$)", re.M)


def _counts(log):
    def last(rx):
        hits = rx.findall(text)
        return int(hits[-1]) if hits else None
    with open(log, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return last(PASS_RE), last(FAIL_RE), last(SKIP_RE)


def cmd_guard(args):
    log, floor, label, tsv = args.log, args.floor, args.label, args.results_tsv
    passc, failc, skipc = _counts(log)

    def record(verdict):
        with open(tsv, "a", encoding="utf-8") as fh:
            fh.write("\t".join(str(x) for x in (
                label, passc if passc is not None else 0,
                failc if failc is not None else ("unknown" if verdict == "fail" else 0),
                skipc if skipc is not None else ("unknown" if verdict == "fail" else 0),
                floor, verdict, log)) + "\n")
    # Canonical order — fail-first, skip-must-fail, then the pass floor.
    # A MISSING summary line counts as a violation (same as the original
    # grep contract: a log without '# fail 0' cannot prove fail-0), with
    # its own message: it means the suite crashed before completing.
    if failc is None:
        record("fail")
        print(f"GUARD FAIL [{label}]: no '# fail' summary line — suite crashed "
              f"before completing (log: {log})", file=sys.stderr)
        sys.exit(1)
    if failc > 0:
        record("fail")
        print(f"GUARD FAIL [{label}]: test failures (log: {log})", file=sys.stderr)
        sys.exit(1)
    if skipc is None:
        record("fail")
        print(f"GUARD FAIL [{label}]: no '# skipped' summary line — suite crashed "
              f"before completing (log: {log})", file=sys.stderr)
        sys.exit(1)
    if skipc > 0:
        record("fail")
        print(f"GUARD FAIL [{label}]: tests SKIPPED — integration env degraded; "
              f"a skip must fail here (log: {log})", file=sys.stderr)
        sys.exit(1)
    if (passc or 0) < floor:
        record("fail")
        print(f"GUARD FAIL [{label}]: only {passc or 0} passed "
              f"(expected >= {floor})", file=sys.stderr)
        sys.exit(1)
    record("pass")


def _git(*args):
    try:
        return subprocess.run(("git",) + args, capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""


def cmd_emit(args):
    suites = []
    totals = {"pass": 0, "fail": 0, "skipped": 0}
    if os.path.exists(args.results_tsv):
        for line in open(args.results_tsv, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 7:
                continue
            label, passed, failed, skipped, floor, verdict, log = parts
            suites.append({
                "suite": label,
                "pass": int(passed),
                "fail": failed if failed == "unknown" else int(failed),
                "skipped": skipped if skipped == "unknown" else int(skipped),
                "pass_floor": int(floor),
                "verdict": verdict,
                "tap_log": log,
            })
            totals["pass"] += int(passed)
            if failed.isdigit():
                totals["fail"] += int(failed)
            if skipped.isdigit():
                totals["skipped"] += int(skipped)
    # A missing/invalid start timestamp (failure before the run really
    # began) yields duration 0, not epoch arithmetic.
    raw = str(args.start_ts).strip()
    start_ts = int(raw) if raw.isdigit() and int(raw) > 0 else 0
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    artifact = {
        "schema": SCHEMA,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "suite_selector": args.suite,
        "verdict": args.verdict,
        "exit_code": args.exit_code,
        "duration_seconds": max(end_ts - start_ts, 0) if start_ts else 0,
        "git": {"head": _git("rev-parse", "HEAD"),
                "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": _git("status", "--porcelain") != ""},
        "stack_ports": {"pg": args.pg_port, "mongo": args.mongo_port,
                        "kernel": args.kernel_port, "legacy": args.legacy_port},
        "suites": suites,
        "totals": totals,
    }
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, args.out)
    print(f"evidence artifact: {args.out}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "guard":
        rest = sys.argv[2:]
        if len(rest) != 4:
            sys.exit("usage: pgie-evidence.py guard LOG FLOOR LABEL RESULTS_TSV")
        cmd_guard(argparse.Namespace(
            log=rest[0], floor=int(rest[1]), label=rest[2], results_tsv=rest[3]))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "mint-head":
        p = argparse.ArgumentParser(prog="pgie-evidence.py mint-head")
        p.add_argument("--pr", type=int, required=True)
        p.add_argument("--repo", default=None,
                       help="OWNER/REPO (default: resolve from origin remote)")
        a = p.parse_args(sys.argv[2:])
        repo = a.repo
        if not repo:
            origin = _git("remote", "get-url", "origin")
            m = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?$/", origin or "")
            if not origin or not m:
                print("::error::could not resolve OWNER/REPO from origin; pass --repo", file=sys.stderr)
                sys.exit(2)
            repo = m.group(1)
        try:
            out = subprocess.run(
                ("git", "ls-remote", f"https://github.com/{repo}.git",
                 f"refs/pull/{a.pr}/head"),
                capture_output=True, text=True, timeout=20, check=True).stdout
        except Exception as exc:
            print(f"::error::ls-remote failed for {repo}#{a.pr}: {exc}", file=sys.stderr)
            sys.exit(2)
        sha = out.split()[0] if out.strip() else ""
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            print(f"::error::no valid head sha for {repo}#{a.pr}", file=sys.stderr)
            sys.exit(2)
        print(f"head:{sha[:7]}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "emit":
        p = argparse.ArgumentParser()
        p.add_argument("--verdict", required=True)
        p.add_argument("--exit-code", type=int, required=True)
        p.add_argument("--results-tsv", required=True)
        p.add_argument("--start-ts", required=True)
        p.add_argument("--suite", required=True)
        p.add_argument("--pg-port", type=int, required=True)
        p.add_argument("--mongo-port", type=int, required=True)
        p.add_argument("--kernel-port", type=int, required=True)
        p.add_argument("--legacy-port", type=int, required=True)
        p.add_argument("--out", required=True)
        cmd_emit(p.parse_args(sys.argv[2:]))
        return
    sys.exit("usage: pgie-evidence.py {guard|emit} ...")


if __name__ == "__main__":
    main()
