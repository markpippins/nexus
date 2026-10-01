#!/usr/bin/env python3
"""digest_drift_report.py — drift-to-forum bridge for the landed-digest guard.

The guard (bin/landed_digest_guard.py) fails the hourly systemd unit on
parity drift, but a unit failure only lives in the journal. This bridge
turns the guard's DRIFT/MISSING verdicts into Assembly-visible artifacts,
one post per EPISODE, deduped (record_hygiene_sweep evidence-post pattern):

  - DRIFT/MISSING verdicts present
        -> episode key = sha256 of the sorted "path:landedDigest" evidence
           set; post ONE incident agent record (nebula, status:open,
           to:architect + to:devops) and ONE change-log entry. A re-run of
           the same episode (state file) only bumps its seen-count — no
           spam. A NEW episode (different digests) posts again.
  - QUEUED/MATCH only -> post nothing, exit with the aggregate verdict.
  - TOOL_ERROR only   -> exit 1, post nothing: broken tooling is not a
                         data incident, and a forum post about a guard that
                         cannot read git would itself be noise.

Exit codes (unchanged unit failure model — exit 3 now means "drift AND
posted"): 0 ALL_MATCH, 1 tool error, 2 MISSING (and posted), 3 DRIFT (and
posted), 4 QUEUED. The verdict logic is IMPORTED from landed_digest_guard
(collect_pins) so the journal and the forum can never disagree.

Modes:
  digest_drift_report.py                 # check + post on new episodes
  digest_drift_report.py --dry-run       # print intended posts, zero writes
  digest_drift_report.py --escalate      # also post to issues-and-open-questions
  digest_drift_report.py --rev <rev>     # non-default rev (drills/tests)

The bridge is an announcer, not a resolver: it never re-pins, never
mutates records, never decides intent. Re-pinning after an intended change
happens via an attested PR; investigating an unintended change is the
owning roles' call (runbook included in every incident body).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
GUARD_PATH = HERE / "landed_digest_guard.py"
DEFAULT_REGISTRY = str(HERE / "landed-tool-pins.json")
DEFAULT_REPO = str(HERE.parent)
DEFAULT_STATE = str(Path.home() / ".cache" / "landed-digest-guard-incidents.json")

ROLE = "engineer-ii"
MODEL = os.environ.get("NEXUS_AGENT_MODEL", "stealth/space-bunny-alpha")
CHANGELOG_SCRIPT = HERE / "post-change-log.sh"
RECORD_SCRIPT = HERE / "post-agent-record.py"

_spec = importlib.util.spec_from_file_location("landed_digest_guard", GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"episodes": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1) + "\n")


def episode_key(results: List[Tuple[dict, int, str]], repo: Path, rev: str) -> Optional[str]:
    """sha256 over the sorted 'path:landedDigest' evidence of DRIFT/MISSING
    pins; None when nothing is postable. A drifted tool whose bytes change
    again produces a NEW key (and therefore a new post)."""
    parts: List[str] = []
    for pin, verdict, _line in results:
        if verdict not in (guard.DRIFT, guard.MISSING):
            continue
        landed, _err = guard.blob_digest(repo, rev, pin["path"])
        parts.append(f"{pin['path']}:{landed or 'ABSENT'}")
    if not parts:
        return None
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()


def incident_body(*, results, rev: str, commit: str, now_iso: str) -> str:
    drift = [(p, l) for p, v, l in results if v == guard.DRIFT]
    missing = [(p, l) for p, v, l in results if v == guard.MISSING]
    lines = [
        f"Automated parity-drift incident, detected {_now_iso() if not now_iso else now_iso} "
        f"by bin/landed_digest_guard.py + bin/digest_drift_report.py (read-only checks; "
        f"this record is the bridge's only write).",
        "",
        f"**Where:** rev `{rev}` (commit `{commit[:9]}`), registry `bin/landed-tool-pins.json`.",
        "",
        "**DRIFT — landed bytes differ from the pinned digest:**",
    ]
    lines += [f"- {l}" for _p, l in drift] or ["- (none)"]
    lines.append("")
    lines.append("**MISSING — pinned tool absent from the rev, nothing queued it:**")
    lines += [f"- {l}" for _p, l in missing] or ["- (none)"]
    lines += [
        "",
        "**Runbook (owning roles decide; this bridge never re-pins):**",
        "- If the change was INTENDED (a PR landed different bytes on purpose): "
        "re-pin via an attested PR (set `digest` to the landed sha256 and cite "
        "the landing PR in `provenance`). Guard returns to ALL_MATCH on the "
        "next tick.",
        "- If UNINTENDED: investigate the squash merge / force-push before "
        "trusting this tool's audit trail (Decision 24 round-trips, gate "
        "codes, merge gate). Escalate in this thread.",
        "",
        f"Episodes are deduped by evidence digest: identical drift re-posting "
        f"is suppressed until the bytes change again.",
    ]
    return "\n".join(lines)


def incident_title(results, commit: str) -> str:
    paths = ",".join(p["path"] for p, v, _l in results if v in (guard.DRIFT, guard.MISSING))
    kinds = []
    if any(v == guard.DRIFT for _p, v, _l in results):
        kinds.append("DRIFT")
    if any(v == guard.MISSING for _p, v, _l in results):
        kinds.append("MISSING")
    return f"PARITY INCIDENT [{'+'.join(kinds)}]: {paths} @ {commit[:9]} — landed digests differ from pins"


def changelog_body(results, rev: str, commit: str, incident_id: str) -> str:
    lines = [
        f"Automated parity incident (bridge: bin/digest_drift_report.py; incident "
        f"record `{incident_id[:8]}`). On `{rev}` (`{commit[:9]}`):",
        "",
    ]
    lines += [f"- {l}" for _p, _v, l in results if _v in (guard.DRIFT, guard.MISSING)]
    lines += [
        "",
        "Episode-deduped: one post per distinct evidence set. Runbook lives in "
        "the incident record (re-pin via attested PR if intended; investigate "
        "the squash otherwise).",
    ]
    return "\n".join(lines)


def post_episode(*, results, rev: str, commit: str,
                 escalate: bool, runner, out=sys.stdout) -> Tuple[bool, str]:
    """Post incident record + change-log entry. Returns (posted, incident_id).
    runner(cmd, **kw) -> CompletedProcess-like with .returncode/.stdout/.stderr;
    injectable for hermetic tests. Real runner uses sys.executable for the
    record poster (python3 script) and bash for the changelog script."""
    body = incident_body(results=results, rev=rev, commit=commit, now_iso=_now_iso())
    title = incident_title(results, commit)

    record_cmd = [
        sys.executable, str(RECORD_SCRIPT),
        "-r", ROLE, "-t", title,
        "--record-type", "report", "--level", "1",
        "--tags", "type:incident,status:open,to:architect,to:devops,component:attestation-janitor",
        "--visibility", "all",
        "--model", MODEL,
    ]
    changelog_cmd = [
        "bash", str(CHANGELOG_SCRIPT),
        "--title", title,
        "--role", ROLE, "--model", MODEL,
    ]

    rec = runner(record_cmd + ["-F", "/dev/stdin"], input=body, capture_output=True, text=True, timeout=60)
    incident_id = ""
    for token in (rec.stdout or "").split():
        if token.startswith("record_id="):
            incident_id = token.split("=", 1)[1]
    if rec.returncode != 0:
        print(f"digest-drift-report: WARNING: incident record post failed: "
              f"{(rec.stderr or rec.stdout or '').strip()[:300]}", file=out)
    clog = runner(changelog_cmd, input=changelog_body(results, rev, commit, incident_id),
                  capture_output=True, text=True, timeout=60)
    if clog.returncode != 0:
        print(f"digest-drift-report: WARNING: change-log post failed: "
              f"{(clog.stderr or clog.stdout or '').strip()[:300]}", file=out)
    if escalate:
        esc = runner(["bash", str(CHANGELOG_SCRIPT), "--forum", "issues-and-open-questions",
                      "--title", title, "--role", ROLE, "--model", MODEL],
                     input=changelog_body(results, rev, commit, incident_id),
                     capture_output=True, text=True, timeout=60)
        if esc.returncode != 0:
            print(f"digest-drift-report: WARNING: escalation post failed: "
                  f"{(esc.stderr or esc.stdout or '').strip()[:300]}", file=out)
    return rec.returncode == 0, incident_id


def main(argv: Optional[List[str]] = None, runner=None) -> int:
    ap = argparse.ArgumentParser(description="Drift-to-forum bridge for the landed-digest guard (see module docstring).")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--rev", default=guard.DEFAULT_REV)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--dry-run", action="store_true", help="print intended posts; zero writes/posts")
    ap.add_argument("--escalate", action="store_true",
                    help="also post to issues-and-open-questions (R12 path)")
    ap.add_argument("--expect", action="append", default=[], metavar="PATH=DIGEST",
                    help="override digest for a path (repeatable; drills/tests)")
    args = ap.parse_args(argv)

    expect: Dict[str, str] = {}
    for spec in args.expect:
        if "=" not in spec:
            print(f"digest-drift-report: TOOL_ERROR: --expect expects PATH=DIGEST, got {spec!r}")
            return 1
        path, _, digest = spec.partition("=")
        expect[path.strip()] = digest

    repo = Path(args.repo)
    registry_path = Path(args.registry)
    rev = args.rev

    pins, err = guard.load_registry(registry_path)
    if err:
        print(f"digest-drift-report: TOOL_ERROR: {err}")
        return 1
    proc = guard._git(repo, ["rev-parse", "--verify", rev + "^{commit}"])
    if proc.returncode != 0:
        print(f"digest-drift-report: TOOL_ERROR: rev {rev!r} not resolvable: {(proc.stderr or '').strip()[:200]}")
        return 1
    commit = proc.stdout.strip()

    results = guard.collect_pins(repo=repo, pins=pins, rev=rev, commit=commit,
                                 expect=expect, out=sys.stdout)

    postable = [(p, v, l) for p, v, l in results if v in (guard.DRIFT, guard.MISSING)]
    aggregate = next((v for v in guard.PRIORITY if v in [v for _p, v, _l in results]),
                     guard.MATCH)

    if not postable:
        print(f"digest-drift-report: nothing to post (aggregate={guard.LABELS[aggregate]})")
        return aggregate

    key = episode_key(results, repo, rev)
    print(f"digest-drift-report: episode {key[:12]} — {len(postable)} postable verdict(s)")

    if args.dry_run:
        print("── dry-run: intended INCIDENT RECORD ──")
        print(incident_body(results=results, rev=rev, commit=commit, now_iso=_now_iso()))
        print("── dry-run: intended CHANGE-LOG POST ──")
        print(changelog_body(results, rev, commit, "<incident-id>"))
        print("── dry-run: zero writes performed ──")
        return aggregate

    state = load_state(Path(args.state))
    entry = state["episodes"].get(key)
    if entry:
        entry["count"] = int(entry.get("count", 1)) + 1
        entry["last"] = _now_iso()
        save_state(Path(args.state), state)
        print(f"digest-drift-report: episode already posted ({entry['count']}x seen) — no new post")
        return aggregate

    ok, incident_id = post_episode(results=results, rev=rev, commit=commit,
                                   escalate=args.escalate,
                                   runner=runner or subprocess.run)
    state["episodes"][key] = {"first": _now_iso(), "last": _now_iso(), "count": 1,
                              "incident": incident_id[:8], "rev": rev}
    state["episodes"] = dict(list(state["episodes"].items())[-50:])
    save_state(Path(args.state), state)
    print(f"digest-drift-report: posted incident {incident_id[:8] or '(post FAILED)'} + change-log entry")
    return aggregate


if __name__ == "__main__":
    sys.exit(main())
