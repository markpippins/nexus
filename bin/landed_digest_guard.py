#!/usr/bin/env python3
"""landed_digest_guard.py — parity-drift tripwire for critical landed tools.

Generalization of the single-pin supersede-record.sh guard (PR #683 epoch):
one read-only verifier and one registry (bin/landed-tool-pins.json) now cover
every critical tool. Motivation unchanged: no CI job checks the POST-squash
state — CI builds fresh PR checkouts, not the landed result — so a squash-time
mutation of a landed tool would silently undermine its audit trail. The guard
runs hourly (systemd user timer landed-digest-guard.timer), reads only git
(cat-file / rev-parse / show), and writes nothing.

Registry schema (bin/landed-tool-pins.json):
  {"version": 1, "pins": [
    {"path": "bin/merge_pr.py",
     "digest": "<sha256 of the pinned landed bytes>",   # or null = not yet landed
     "provenance": "how/when the pin was captured",
     "queued_prs": [
       {"pr": 675, "head": "1c81a30e", "digest": "<sha256>",
        "provenance": "variant expected to land"}]}]}

Per-pin verdicts (exit-code oracle):
  MATCH    0  landed digest == pin
  DRIFT    3  landed digest != pin with no queued match, OR a queued head no
              longer carries its registered digest (force-push-after-capture
              detection)
  MISSING  2  tool absent from the rev, never landed, nothing queued it
              (covers both never-landed pins and a pinned tool deleted
              from the rev — registry and reality disagree either way)
  QUEUED   4  tool absent but a queued head still carries the expected digest
              (waiting to land), OR the landed digest equals a queued digest
              (the queued PR just landed; pin advance pending — expected state)

Aggregate exit = highest-priority verdict over all pins:
  DRIFT > MISSING > QUEUED > MATCH        (3 > 2 > 4 > 0)

Usage:
  landed_digest_guard.py                                  # check origin/main, all pins
  landed_digest_guard.py --rev 1c81a30e                   # check a PR head
  landed_digest_guard.py --only bin/merge_pr.py,bin/gate_codes.py
  landed_digest_guard.py --expect bin/merge_pr.py=<hex>   # override (tests/drills)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_REPO = str(Path(__file__).resolve().parent.parent)
DEFAULT_REGISTRY = str(Path(__file__).resolve().parent / "landed-tool-pins.json")
DEFAULT_REV = "origin/main"

MATCH, MISSING, DRIFT, QUEUED = 0, 2, 3, 4
PRIORITY = [DRIFT, MISSING, QUEUED, MATCH]  # aggregate = first present
LABELS = {MATCH: "MATCH", MISSING: "MISSING", DRIFT: "DRIFT", QUEUED: "QUEUED"}


def _git(repo: Path, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )


def load_registry(registry_path: Path) -> Tuple[List[dict], Optional[str]]:
    """Parse the registry; returns (pins, error). Never guesses on garbage."""
    try:
        data = json.loads(registry_path.read_text())
    except OSError as exc:
        return [], f"cannot read registry {registry_path}: {exc}"
    except ValueError as exc:
        return [], f"malformed JSON in {registry_path}: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("pins"), list) or not data["pins"]:
        return [], f"registry {registry_path} lacks a non-empty 'pins' array"
    for pin in data["pins"]:
        if not isinstance(pin, dict) or not isinstance(pin.get("path"), str) or not pin["path"]:
            return [], f"registry {registry_path} has a pin without a valid 'path'"
    return data["pins"], None


def blob_digest(repo: Path, rev: str, path: str) -> Tuple[Optional[str], Optional[str]]:
    """sha256 of file content at rev:path; (None, None) when absent;
    (None, err) on git failure."""
    exists = _git(repo, ["cat-file", "-e", f"{rev}:{path}"])
    if exists.returncode != 0:
        return None, None  # absent — presence is a verdict, not an error
    proc = _git(repo, ["show", f"{rev}:{path}"])
    if proc.returncode != 0:
        return None, f"git show {rev}:{path} failed: {(proc.stderr or '').strip()[:200]}"
    return hashlib.sha256(proc.stdout.encode("utf-8", errors="surrogateescape")).hexdigest(), None


def check_pin(pin: dict, *, repo: Path, rev: str, commit: str,
              expect_digest: Optional[str], out) -> Tuple[int, str]:
    """One pin -> (verdict, one-line evidence)."""
    path = pin["path"]

    # ── digest source: --expect override or queued-head live verification ─
    queued_digest: Optional[str] = None
    queued_pr: Optional[dict] = None
    if expect_digest is not None:
        queued_digest, queued_pr = expect_digest, None
    else:
        for q in pin.get("queued_prs") or []:
            if not (isinstance(q.get("head"), str) and isinstance(q.get("digest"), str)):
                continue
            qdigest, qerr = blob_digest(repo, q["head"], path)
            if qerr:
                return DRIFT, f"{path}: queued PR #{q.get('pr')} head {q['head'][:9]} unreadable: {qerr}"
            if qdigest is None:
                return DRIFT, (f"{path}: queued PR #{q.get('pr')} head {q['head'][:9]} no longer carries "
                               f"{path} (force-push after capture?)")
            if qdigest != q["digest"]:
                return DRIFT, (f"{path}: queued PR #{q.get('pr')} head {q['head'][:9]} digest {qdigest} != "
                               f"registered {q['digest']} (force-push after capture?)")
            queued_digest, queued_pr = qdigest, q
            break

    # ── landed side ──────────────────────────────────────────────────────
    landed, err = blob_digest(repo, rev, path)
    if err:
        return DRIFT, f"{path}: {err}"
    if landed is None:
        if queued_digest is not None:
            pr_note = f" (queued PR #{queued_pr['pr']} head verified)" if queued_pr else " (--expect override)"
            return QUEUED, f"{path}: absent from {rev} ({commit[:9]}) but expected digest queued{pr_note}"
        return MISSING, f"{path}: absent from {rev} ({commit[:9]}); never landed, nothing queued it"

    pin_digest = (expect_digest or pin.get("digest") or "").strip().lower()
    if not pin_digest:
        return DRIFT, f"{path}: landed but pin digest is null and no queued/override digest available"
    if landed == pin_digest:
        return MATCH, f"{path}: sha256={landed} == pin ({commit[:9]})"
    if queued_digest is not None and landed == queued_digest:
        pr_note = f"queued PR #{queued_pr['pr']}" if queued_pr else "--expect override"
        return QUEUED, (f"{path}: landed digest {landed} == {pr_note} digest; pin advance pending "
                        f"(expected post-landing state; re-pin via attested PR)")
    return DRIFT, (f"{path}: landed sha256={landed} != pin {pin_digest} — parity drift; "
                   f"investigate the squash merge before trusting this tool's audit trail")


def run_guard(*, repo: Path, registry_path: Path, rev: str,
              only: List[str], expect: Dict[str, str], out=sys.stdout) -> int:
    pins, err = load_registry(registry_path)
    if err:
        print(f"landed-digest-guard: TOOL_ERROR: {err}", file=out)
        return 1

    if only:
        wanted = {p.strip() for p in only if p.strip()}
        missing = wanted - {p["path"] for p in pins}
        if missing:
            print(f"landed-digest-guard: TOOL_ERROR: --only paths not in registry: {sorted(missing)}", file=out)
            return 1
        pins = [p for p in pins if p["path"] in wanted]
    for p in pins:
        if p["path"] in expect and not expect[p["path"]].strip().lower().isalnum():
            print(f"landed-digest-guard: TOOL_ERROR: --expect digest for {p['path']} is not hex-like", file=out)
            return 1

    proc = _git(repo, ["rev-parse", "--verify", rev + "^{commit}"])
    if proc.returncode != 0:
        print(f"landed-digest-guard: TOOL_ERROR: rev {rev!r} not resolvable in {repo}: {(proc.stderr or '').strip()[:200]}", file=out)
        return 1
    commit = proc.stdout.strip()

    verdicts: List[int] = []
    for pin in pins:
        v, line = check_pin(pin, repo=repo, rev=rev, commit=commit,
                            expect_digest=expect.get(pin["path"]), out=out)
        verdicts.append(v)
        print(f"  [{LABELS[v]:<7}] {line}", file=out)

    aggregate = next((v for v in PRIORITY if v in verdicts), MATCH)
    label = LABELS[aggregate]
    if aggregate == MATCH:
        print(f"landed-digest-guard: ALL_MATCH: {len(pins)} pin(s) verified against {rev} ({commit[:9]}) — parity verified", file=out)
        return 0
    if aggregate == QUEUED:
        print(f"landed-digest-guard: QUEUED_PENDING: {len(pins)} pin(s) against {rev} ({commit[:9]}); at least one tool queued/pending pin-advance — informational", file=out)
        return 4
    if aggregate == MISSING:
        print(f"landed-digest-guard: MISSING: a pinned tool is absent from {rev} ({commit[:9]}) and nothing queued it — check the registry", file=out)
        return 2
    print(f"landed-digest-guard: PARITY_DRIFT: on {rev} ({commit[:9]}) — investigate before trusting affected tools' audit trails", file=out)
    return 3


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Parity-drift tripwire for critical landed tools (see module docstring).")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="git checkout to interrogate")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY, help=f"pin registry (default: {DEFAULT_REGISTRY})")
    ap.add_argument("--rev", default=DEFAULT_REV, help=f"git rev to check (default: {DEFAULT_REV})")
    ap.add_argument("--only", default="", help="comma-separated subset of registry paths to check")
    ap.add_argument("--expect", action="append", default=[], metavar="PATH=DIGEST",
                    help="override digest for a path (repeatable; tests/drills)")
    args = ap.parse_args(argv)

    expect: Dict[str, str] = {}
    for spec in args.expect:
        if "=" not in spec:
            print(f"landed-digest-guard: TOOL_ERROR: --expect expects PATH=DIGEST, got {spec!r}", file=sys.stdout)
            return 1
        path, _, digest = spec.partition("=")
        expect[path.strip()] = digest

    return run_guard(
        repo=Path(args.repo),
        registry_path=Path(args.registry),
        rev=args.rev,
        only=[p for p in args.only.split(",") if p.strip()],
        expect=expect,
    )


if __name__ == "__main__":
    sys.exit(main())
