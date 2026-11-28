#!/usr/bin/env python3
"""deploy-pin-drift-alarm.py — HEAD/digest drift alarm for the live deploy tree,
plus the watchdog that shouts when this alarm itself stops running.

Both live units run out of /home/codex/deploy/lilac-shadow:

  * conduit-kernel.service        (drop-in: WorkingDirectory/PYTHONPATH ...
                                   /deploy/lilac-shadow/python/conduit)
  * nexus-execution-worker.service (drop-in: .../deploy/lilac-shadow/python)

That checkout is a separate clone pinned DETACHED at 318d9ea8 plus one
deploy-lineage commit carrying the receipt.issued emission for #8261650
(branch deploy/lilac-pin-318d9ea8-receipt-issued). No script under bin/,
docs/ or .github/ pulls or resets it, so drift there is always an operator
action — but silence is the failure mode: a hand-edit or a stray checkout
changes what the live kernel imports with no record anywhere.

This alarm makes drift loud. Each run it compares the tree's HEAD and the
sha256 of every tracked file under python/ (what the units actually load)
against a baseline, and on a NEW episode posts once to the
issues-and-open-questions forum. A persisting drift does not spam: the
episode key is a digest of the evidence set and is recorded in the state
file. An intentional change is accepted explicitly with --accept, which
rewrites the baseline (never automatic — that is the whole point).

Scope is HEAD + tracked files under python/. The units are Python services
whose PYTHONPATH points into that tree; the rest of the checkout is not
loaded by them.

Liveness (--watchdog): a drift alarm that has stopped running looks exactly
like a clean tree from the outside, so every completed check (BASELINE, OK,
DRIFT) writes ~/.cache/deploy-pin-drift-alarm/heartbeat.json. The watchdog
reads only that file: if the newest heartbeat is older than --max-age
(default 2700s = 45 min = three missed quarter-hourly runs) it posts once per
stall episode and exits 2. A missing heartbeat with no baseline is a fresh
install, not a stall, so it stays quiet; a missing heartbeat *after* a
baseline has been accepted is a stall.

The watchdog cannot catch its own death: it is a second timer, offset to
*:7/15. If the systemd user manager itself stops, nothing posts —
heartbeat.json's mtime is then the only evidence, and running
`bin/deploy-pin-drift-alarm.py --watchdog` by hand (or from cron) reports
STALLED.

Usage:
  deploy-pin-drift-alarm.py --post       # check, post on a new episode (the timer)
  deploy-pin-drift-alarm.py --dry-run    # report only, no writes, no posts
  deploy-pin-drift-alarm.py --accept     # rebaseline after an intended change
  deploy-pin-drift-alarm.py --watchdog --post   # shout if no check completed within --max-age
  deploy-pin-drift-alarm.py --json       # machine-readable verdict

**The channel is not a default.** `--post` is what lets a run put a DRIFT or STALLED
episode in the issues forum; a run that does not name it reports to stdout and the exit
code only. Same guard as bin/vanadium_runner_watchdog.py (2026-11-28): a run whose purpose
is a check or a drill must not be able to invent an outage-shaped thread, and this alarm is
one `bin/deploy-pin-drift-alarm.py` invocation away from doing exactly that. The two timers
pass `--post`; tests and hand-checks do not.

Exit codes: 0 baseline/current (first run or match), 1 tooling error,
2 drift or stall detected (posted, or the episode was already posted, or no --post
channel was named — the finding is real either way, so the unit stays red).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REPO = os.environ.get("DEPLOY_PIN_REPO", "/home/codex/deploy/lilac-shadow")
DEFAULT_STATE = os.environ.get(
    "DEPLOY_PIN_STATE", str(Path.home() / ".cache" / "deploy-pin-drift-alarm" / "state.json")
)
# Liveness marker written by every completed check; read only by --watchdog.
DEFAULT_HEARTBEAT = os.environ.get(
    "DEPLOY_PIN_HEARTBEAT", str(Path.home() / ".cache" / "deploy-pin-drift-alarm" / "heartbeat.json")
)
# Stall dedupe lives in its own file so it can never clobber the baseline.
DEFAULT_WATCHDOG_STATE = os.environ.get(
    "DEPLOY_PIN_WATCHDOG_STATE",
    str(Path.home() / ".cache" / "deploy-pin-drift-alarm" / "watchdog.json"),
)
# 2700s = 45 min: three missed quarter-hourly runs before shouting.
DEFAULT_MAX_AGE = int(os.environ.get("DEPLOY_PIN_MAX_AGE_SECONDS", "2700"))
# The paths whose contents the two units import from.
WATCHED_PATHS = ("python",)

FORUM = os.environ.get("DEPLOY_PIN_ALARM_FORUM", "issues-and-open-questions")
ROLE = os.environ.get("DEPLOY_PIN_ALARM_ROLE", "engineer-ii")
MODEL = os.environ.get("NEXUS_AGENT_MODEL", "opencode/big-pickle")
ASSEMBLY_URL = os.environ.get("DEPLOY_PIN_ALARM_ASSEMBLY_URL", "http://localhost:3107")


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _tracked_files(repo: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", repo, "ls-files", "-z", "--", *WATCHED_PATHS],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {out.stderr.decode()[:400]}")
    return sorted(p for p in out.stdout.decode().split("\0") if p)


def snapshot(repo: str) -> dict:
    """HEAD identity + sha256 of every tracked watched file as it is on disk.

    Digests come from the working tree, not from git objects: the service
    imports files from disk, so an uncommitted edit is exactly the thing that
    must be visible here.
    """
    head = _git(repo, "rev-parse", "HEAD").strip()
    # Detached HEAD is the normal state of the pin, and symbolic-ref exits 1
    # for it — that is information, not an error.
    ref_proc = subprocess.run(
        ["git", "-C", repo, "symbolic-ref", "-q", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    ref = ref_proc.stdout.strip() or "DETACHED"
    digests: dict[str, str] = {}
    for rel in _tracked_files(repo):
        path = Path(repo) / rel
        if not path.is_file():
            digests[rel] = "absent"
            continue
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digests[rel] = "sha256:" + h.hexdigest()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "head": head,
        "head_ref": ref,
        "file_count": len(digests),
        "digests": digests,
    }


def compare(baseline: dict, current: dict) -> dict:
    base, cur = baseline.get("digests", {}), current.get("digests", {})
    changed = sorted(p for p in base.keys() & cur.keys() if base[p] != cur[p])
    added = sorted(cur.keys() - base.keys())
    removed = sorted(base.keys() - cur.keys())
    head_changed = baseline.get("head") != current.get("head")
    return {
        "head_changed": head_changed,
        "head_before": baseline.get("head"),
        "head_after": current.get("head"),
        "ref_before": baseline.get("head_ref"),
        "ref_after": current.get("head_ref"),
        "changed": changed,
        "added": added,
        "removed": removed,
        "clean": not (head_changed or changed or added or removed),
    }


def episode_key(drift: dict) -> str:
    evidence = {
        "head": drift["head_after"],
        "changed": drift["changed"],
        "added": drift["added"],
        "removed": drift["removed"],
    }
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _role_id(role: str) -> str:
    with urllib.request.urlopen(f"{ASSEMBLY_URL}/api/users", timeout=10) as r:
        users = json.load(r)
    for user in users:
        if (user.get("name") or "").lower() == role.lower():
            return user.get("id") or ""
    return ""


def _render(drift: dict, current: dict, repo: str) -> tuple[str, str]:
    head_before = (drift["head_before"] or "?")[:10]
    head_after = (drift["head_after"] or "?")[:10]
    lines = [
        "**The live deploy tree changed while no one declared it.**",
        "",
        f"* tree: `{repo}`",
        f"* HEAD: `{head_before}` -> `{head_after}` "
        f"(`{drift['ref_before']}` -> `{drift['ref_after']}`)"
        if drift["head_changed"]
        else f"* HEAD: `{head_after}` unchanged",
        f"* watched: tracked files under `python/` ({current.get('file_count', 0)} files)",
        f"* observed: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "| file | change |",
        "| --- | --- |",
    ]
    lines += [f"| `{p}` | modified |" for p in drift["changed"]]
    lines += [f"| `{p}` | added |" for p in drift["added"]]
    lines += [f"| `{p}` | removed |" for p in drift["removed"]]
    if not (drift["changed"] or drift["added"] or drift["removed"]):
        lines.append("| (none) | HEAD moved only |")
    lines += [
        "",
        "Both `conduit-kernel.service` and `nexus-execution-worker.service` import from "
        "this tree, so the running kernel may now differ from the reviewed pin "
        "(`318d9ea8` + `deploy/lilac-pin-318d9ea8-receipt-issued`).",
        "",
        "**What to do**",
        "",
        "1. Identify who touched the tree and why (journal, shell history); this is "
        "never a scheduled job — nothing under `bin/`, `docs/` or `.github/` pulls or "
        "resets it.",
        "2. If the change was intended, land it as a real commit on the deploy lineage "
        "and push it, record the new pin in the unit drop-in comment, then rebaseline: "
        "`bin/deploy-pin-drift-alarm.py --accept`.",
        "3. If it was not intended, restore the tree and confirm "
        "`bin/deploy-pin-drift-alarm.py` returns to `OK`.",
        "4. The alarm is an announcer, not a resolver: it never reverts, re-pins or "
        "restarts anything.",
        "",
        "_Posted once per drift episode; a persisting drift does not re-post._",
    ]
    title = (
        f"DEPLOY PIN DRIFT: {repo.rsplit('/', 1)[-1]} HEAD {head_before}->{head_after} "
        f"({len(drift['changed']) + len(drift['added']) + len(drift['removed'])} file(s))"
    )
    if not drift["head_changed"]:
        title = (
            f"DEPLOY PIN DRIFT: {repo.rsplit('/', 1)[-1]} "
            f"{len(drift['changed']) + len(drift['added']) + len(drift['removed'])} file(s) "
            "changed in place"
        )
    return title, "\n".join(lines)


def _render_stall(
    *, heartbeat_path: Path, last_check: str, age: int, max_age: int, hb: dict, repo: str
) -> tuple[str, str]:
    minutes = int(age // 60)
    threshold_min = int(max_age // 60)
    last_verdict = hb.get("verdict") or "?"
    last_head = (hb.get("head") or "?")[:10]
    lines = [
        "**The deploy pin drift alarm has stopped running.**",
        "",
        f"* heartbeat: `{heartbeat_path}`",
        f"* last completed check: `{last_check}` ({minutes} min ago)",
        f"* threshold: {threshold_min} min ({max_age}s — three missed quarter-hourly runs)",
        f"* last verdict: `{last_verdict}` at head `{last_head}`",
        f"* tree: `{repo}`",
        f"* observed: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "Since that check nothing has compared the live tree's HEAD or file digests "
        "against the baseline, so the pin's state is **unknown — not clean**. A silent "
        "alarm and a clean tree look identical from the outside, which is exactly why "
        "this watchdog exists. This run did NOT inspect the tree itself.",
        "",
        "**What to do**",
        "",
        "1. Find out why the schedule stopped:",
        "   `systemctl --user status deploy-pin-drift-alarm.timer`",
        "   `systemctl --user list-timers 'deploy-pin-*'`",
        "   `journalctl --user -u deploy-pin-drift-alarm.service -n 50`",
        "2. Common causes: the timer was disabled or stopped by hand; the service keeps "
        "failing (exit 1 = tooling error: git, the state file, or Assembly "
        "unreachable); the deploy checkout moved or was removed; no systemd user "
        "manager running for this account.",
        "3. Fix the cause, then run the check by hand to learn the real state: "
        "`bin/deploy-pin-drift-alarm.py` (`--accept` ONLY for an intended tree change — "
        "it rewrites the baseline and hides the drift).",
        "4. The next completed check writes a fresh heartbeat and ends this episode; "
        "the watchdog posts once per stall, not once per silent run.",
    ]
    title = (
        f"DEPLOY PIN ALARM STALLED: no check since {last_check} "
        f"({minutes} min > {threshold_min} min) on {repo.rsplit('/', 1)[-1]}"
    )
    return title, "\n".join(lines)


def post_episode(title: str, body: str) -> str:
    uid = _role_id(ROLE)
    if not uid:
        raise RuntimeError(f"could not resolve role {ROLE!r} at {ASSEMBLY_URL}/api/users")
    payload = json.dumps(
        {"title": title, "body": body, "postedById": uid, "role": ROLE, "model": MODEL}
    ).encode()
    req = urllib.request.Request(
        f"{ASSEMBLY_URL}/api/forums/{FORUM}/threads",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        created = json.load(r)
    thread_id = created.get("id") or ""
    if not thread_id:
        raise RuntimeError(f"post returned no thread id: {json.dumps(created)[:200]}")
    return thread_id


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:  # corrupt state must not look like "no baseline"
        raise RuntimeError(f"state file {path} is unreadable: {exc}")


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def write_heartbeat(path: Path, repo: str, current: dict, verdict: str) -> None:
    """Liveness marker for --watchdog: when a check last completed, not what it saw."""
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "head": current["head"],
        "head_ref": current["head_ref"],
        "file_count": current["file_count"],
        "verdict": verdict,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _heartbeat_or_warn(path: Path, repo: str, current: dict, verdict: str) -> None:
    # A heartbeat we cannot write is itself an outage the watchdog will report,
    # so this must not take the check's real verdict down with it.
    try:
        write_heartbeat(path, repo, current, verdict)
    except OSError as exc:
        print(f"WARN: could not write heartbeat {path}: {exc}", file=sys.stderr)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_watchdog(args) -> int:
    """Shout when the quarter-hourly check has gone silent."""
    heartbeat_path = Path(args.heartbeat)
    watchdog_state_path = Path(args.watchdog_state)
    now = datetime.now(timezone.utc)
    verdict: dict = {
        "verdict": "OK",
        "heartbeat": str(heartbeat_path),
        "max_age_seconds": args.max_age,
    }

    hb: dict | None
    try:
        hb = json.loads(heartbeat_path.read_text())
    except FileNotFoundError:
        hb = None
    except Exception as exc:
        print(f"ERROR: heartbeat {heartbeat_path} is unreadable: {exc}", file=sys.stderr)
        return 1

    if hb is None:
        # Nothing has ever run. A fresh install is not a stall, so fall back to
        # the accepted baseline's timestamp instead of inventing evidence.
        try:
            baseline = load_state(Path(args.state)).get("baseline") or {}
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        last_check = baseline.get("captured_at")
        if not last_check:
            print("NO-HEARTBEAT: no completed check and no baseline recorded; nothing to judge")
            if args.json:
                print(json.dumps({**verdict, "verdict": "NO-EVIDENCE"}, sort_keys=True))
            return 0
        source = "baseline"
    else:
        last_check = hb.get("checked_at")
        if not last_check:
            print(f"ERROR: heartbeat {heartbeat_path} has no checked_at", file=sys.stderr)
            return 1
        source = "heartbeat"

    try:
        age = int((now - _parse_ts(last_check)).total_seconds())
    except (TypeError, ValueError) as exc:
        print(f"ERROR: cannot parse last check {last_check!r}: {exc}", file=sys.stderr)
        return 1

    verdict.update({"last_check": last_check, "age_seconds": age, "evidence": source})
    if hb is not None:
        verdict.update({"last_verdict": hb.get("verdict"), "last_head": hb.get("head")})

    if age <= args.max_age:
        print(
            f"OK watchdog: last completed check {last_check} ({age}s ago) is inside "
            f"{args.max_age}s ({source})"
        )
        if args.json:
            print(json.dumps(verdict, sort_keys=True))
        return 0

    episode = hashlib.sha256(
        json.dumps({"last": last_check, "max_age": args.max_age}, sort_keys=True).encode()
    ).hexdigest()
    try:
        wstate = load_state(watchdog_state_path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    already = (wstate.get("posted") or {}).get("episode") == episode
    verdict.update({"verdict": "STALLED", "episode": episode, "already_posted": already})
    print(
        f"STALLED: no completed check since {last_check} ({age}s ago > {args.max_age}s) "
        f"via {source} episode={episode[:12]}{' (already posted)' if already else ''}"
    )

    if args.dry_run:
        verdict["posted"] = None
        print("DRY-RUN: no post, no state write")
    elif already:
        verdict["posted"] = wstate["posted"].get("thread_id")
    elif not args.post:
        # No channel named: report-only. The stall is still real (exit 2 keeps the
        # unit red), but it is not announced, and the episode is not consumed —
        # the next --post run posts it.
        verdict["posted"] = None
        print("STALLED: NOT posted — no --post channel named (report-only run; the "
              "watchdog timer passes --post)")
    else:
        title, body = _render_stall(
            heartbeat_path=heartbeat_path,
            last_check=last_check,
            age=age,
            max_age=args.max_age,
            hb=hb or {},
            repo=args.repo,
        )
        try:
            thread_id = post_episode(title, body)
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            # A stalled alarm we cannot announce is still a stalled alarm.
            print(f"ERROR: stall detected but posting failed: {exc}", file=sys.stderr)
            verdict["posted"] = None
            print(json.dumps(verdict, sort_keys=True))
            return 2
        wstate["posted"] = {
            "episode": episode,
            "thread_id": thread_id,
            "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        wstate["last_stall"] = {
            "last_check": last_check,
            "age_seconds": age,
        }
        save_state(watchdog_state_path, wstate)
        verdict["posted"] = thread_id
        print(f"POSTED thread={thread_id} forum={FORUM}")

    if args.json:
        print(json.dumps(verdict, sort_keys=True))
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--heartbeat", default=DEFAULT_HEARTBEAT)
    ap.add_argument("--watchdog-state", default=DEFAULT_WATCHDOG_STATE)
    ap.add_argument(
        "--watchdog",
        action="store_true",
        help="shout when the alarm itself has stopped running (heartbeat age)",
    )
    ap.add_argument(
        "--max-age",
        type=int,
        default=DEFAULT_MAX_AGE,
        help="seconds a heartbeat may age before the watchdog shouts (default 2700 = 45 min)",
    )
    ap.add_argument("--accept", action="store_true", help="rebaseline to the current tree")
    ap.add_argument(
        "--post",
        action="store_true",
        help="allow this run to post to the issues forum (the timers pass it; tests and "
             "hand-checks must not — without it a DRIFT/STALLED finding is reported to "
             "stdout and the exit code only)",
    )
    ap.add_argument("--dry-run", action="store_true", help="no writes, no posts")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.watchdog:
        return run_watchdog(args)

    repo, state_path = args.repo, Path(args.state)
    if not Path(repo, ".git").exists():
        print(f"ERROR: {repo} is not a git checkout", file=sys.stderr)
        return 1

    try:
        current = snapshot(repo)
        state = load_state(state_path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    baseline = state.get("baseline")
    verdict: dict = {
        "verdict": "OK",
        "repo": repo,
        "head": current["head"],
        "head_ref": current["head_ref"],
        "file_count": current["file_count"],
    }

    if baseline is None or args.accept:
        if not args.dry_run:
            state["baseline"] = current
            state["accepted_at"] = current["captured_at"]
            save_state(state_path, state)
            _heartbeat_or_warn(Path(args.heartbeat), repo, current, "BASELINE")
        verdict["verdict"] = "BASELINE"
        action = "would record" if args.dry_run else "recorded"
        print(
            f"BASELINE {action}: head={current['head'][:10]} ref={current['head_ref']} "
            f"files={current['file_count']} state={state_path}"
        )
        if args.json:
            print(json.dumps(verdict, sort_keys=True))
        return 0

    drift = compare(baseline, current)
    if drift["clean"]:
        if not args.dry_run:
            _heartbeat_or_warn(Path(args.heartbeat), repo, current, "OK")
        print(
            f"OK head={current['head'][:10]} ref={current['head_ref']} "
            f"files={current['file_count']} match the baseline of "
            f"{baseline.get('captured_at')}"
        )
        if args.json:
            print(json.dumps(verdict, sort_keys=True))
        return 0

    key = episode_key(drift)
    already = (state.get("posted") or {}).get("episode") == key
    verdict.update(
        {
            "verdict": "DRIFT",
            "episode": key,
            "already_posted": already,
            "head_before": drift["head_before"],
            "head_after": drift["head_after"],
            "changed": drift["changed"],
            "added": drift["added"],
            "removed": drift["removed"],
        }
    )

    # Liveness first: the check ran, whatever we manage to say about it.
    if not args.dry_run:
        _heartbeat_or_warn(Path(args.heartbeat), repo, current, "DRIFT")

    title, body = _render(drift, current, repo)
    print(f"DRIFT head {drift['head_before'][:10] if drift['head_before'] else '?'} -> "
          f"{drift['head_after'][:10]} changed={len(drift['changed'])} "
          f"added={len(drift['added'])} removed={len(drift['removed'])} "
          f"episode={key[:12]}{' (already posted)' if already else ''}")
    for p in drift["changed"]:
        print(f"  M {p}")
    for p in drift["added"]:
        print(f"  A {p}")
    for p in drift["removed"]:
        print(f"  D {p}")

    if args.dry_run:
        verdict["posted"] = None
        print("DRY-RUN: no post, no state write")
    elif already:
        verdict["posted"] = state["posted"].get("thread_id")
    elif not args.post:
        # Report-only, same contract as the stall path: a check or a drill must not
        # be able to put an outage-shaped thread in the issues forum by default
        # (the 2026-11-28 implicit-channel guard; bin/vanadium_runner_watchdog.py).
        verdict["posted"] = None
        print("DRIFT: NOT posted — no --post channel named (report-only run; the alarm "
              "timer passes --post). The episode is not consumed: the next --post run "
              "posts it.")
    else:
        try:
            thread_id = post_episode(title, body)
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            # A drift we cannot announce is still a drift: say so loudly and
            # fail, but never fake a post.
            print(f"ERROR: drift detected but posting failed: {exc}", file=sys.stderr)
            verdict["posted"] = None
            print(json.dumps(verdict, sort_keys=True))
            return 2
        state["posted"] = {
            "episode": key,
            "thread_id": thread_id,
            "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_state(state_path, state)
        verdict["posted"] = thread_id
        print(f"POSTED thread={thread_id} forum={FORUM}")

    if args.json:
        print(json.dumps(verdict, sort_keys=True))
    return 2


if __name__ == "__main__":
    sys.exit(main())
