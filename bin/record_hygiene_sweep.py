#!/usr/bin/env python3
"""record_hygiene_sweep.py — periodic stale-status:open record hygiene sweep.

Motivation: agent records carrying `status:open` are lifecycle asks. When the
ask is answered (a proposal ratified, a report read, a request superseded) the
record keeps saying OPEN forever unless its owning role closes it out. The
Decision 24 supersession convention (architect record cfcc9d65) plus
bin/supersede-record.sh give every role the tool to close records properly
(retire without successor, supersede with verbatim archive + pointer); the
2026-09-30 dogfood run (R1 7072d44f, R2 0a40fe78) proved both modes on live
records. What was missing is the noticing layer: WHICH records are stale.

One sweep (READ-ONLY over records — it never mutates anything):
  1. SCAN  GET {nebula}/api/agent-records?tag=status:open (paginated) and keep
     records older than --min-age-days (default 14), minus the exclusion tags
     below (live incidents, in-flight work, archives are not hygiene fodder).
  2. CLASSIFY deterministically (advisory only — an owning role always decides):
       OBSERVATION      analysis / inspection / engineering_log -> RETIRE-PROPOSE
                        (a report's act is complete once read)
       REQUEST          assessment with type:proposal or type:request -> RETIRE-PROPOSE
                        (an ask that is still open after N days needs its owner
                        to either act or retire it with the outcome note)
       I4-EXEMPT        recordType prompt/response -> FLAG only (supersede/retire
                        refuses these by policy; listed as guard evidence)
       MANUAL-REVIEW    everything else (report, architecture_note, decision,
                        unknown, or AGE-UNKNOWN) -> listed, no proposal (may
                        still govern; wrong tool to auto-retire a doctrine)
  3. VALIDATE every RETIRE-PROPOSE by invoking bin/supersede-record.sh in
     --dry-run mode (exit-code oracle; the dry-run performs NO writes):
       exit 0 -> actionable candidate (policy gates would pass today)
       exit 2 -> SELF-RESOLVED (already superseded/retired — the convention
                 already closed it; listed as evidence, no action)
       exit 1 -> TOOL-ERROR (API hiccup; the candidate stays unverifiable)
  4. REPORT one evidence post to the change-log per run that has NEW
     candidates (state-file dedup: the same stale record is not re-posted
     every tick; its `last`/`count` just advance). Candidates are RE-CHECKED
     (point lookup) inside the posting guard so a record that went terminal
     between scan and post is dropped, not nagged.

Modes: default is a pure classification print (no state write, no forum
post). --apply runs the full validation + evidence post. NOTE: --apply never
mutates agent records — the proposals it posts are EXECUTED by an owning role
with bin/supersede-record.sh (without --dry-run), never by this sweep.

Scheduling: systemd user timer record-hygiene-sweep.timer (weekly; unit files
in systemd-user/ follow the attestation-janitor deploy doctrine — dedicated
checkout, ExecStartPre self-heal to origin/main, unit refuses to run stale).
The timer deliberately runs WITH --apply (evidence posting) because the
sweep's writes are its own audit trail, not the records.

Usage:
  record_hygiene_sweep.py                 # classify + print, zero writes
  record_hygiene_sweep.py --apply         # + dry-run validation, state, evidence post
  record_hygiene_sweep.py --apply --min-age-days 30 --max-candidates 40

Exit codes: 0 ok (or dry-run classification); 1 some validations errored;
            2 hard error (nebula unreachable — nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BIN_DIR = Path(__file__).resolve().parent
SUPERSEDE = BIN_DIR / "supersede-record.sh"
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_STATE = str(Path.home() / ".cache" / "record-hygiene-sweep.json")

ROLE = "record-hygiene-sweep"
MODEL = "automated/record-hygiene-sweep"
PAGE_LIMIT = 500
MAX_PAGES = 40          # 20k records — a hard stop, not an expectation
DEFAULT_MIN_AGE_DAYS = 14
DEFAULT_MAX_CANDIDATES = 25

EXCLUDE_TAGS = {
    "type:archive",       # already terminal by construction
    "type:incident",      # incidents close via their own workflow, not hygiene
    "status:in_progress", # claimed and moving
    "status:claimed",
}
OBSERVATION_TYPES = {"analysis", "inspection", "engineering_log"}
REQUEST_TYPES = {"assessment"}
REQUEST_HINT_TAGS = ("type:proposal", "type:request", "type:question")
I4_TYPES = {"prompt", "response"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(createdAt: Any, now_s: float) -> Optional[float]:
    """Record age in days from an epoch-ms number or an ISO string; None if
    unusable (missing timestamps are REPORTED, never guessed)."""
    if isinstance(createdAt, (int, float)) and createdAt > 0:
        return max(0.0, (now_s - createdAt / 1000.0) / 86400.0)
    if isinstance(createdAt, str):
        try:
            ts = datetime.fromisoformat(createdAt.replace("Z", "+00:00"))
            return max(0.0, (now_s - ts.timestamp()) / 86400.0)
        except ValueError:
            return None
    return None


def scan_open_records(fetch: Callable[[str], Any]) -> List[dict]:
    """All records tagged status:open, following offset pagination."""
    out: List[dict] = []
    for page in range(MAX_PAGES):
        url = f"/api/agent-records?tag=status:open&limit={PAGE_LIMIT}&offset={page * PAGE_LIMIT}"
        data = fetch(url)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RuntimeError(f"unexpected payload for {url}")
        out.extend(items)
        if len(items) < PAGE_LIMIT:
            break
    return out


def stale_open_records(records: List[dict], min_age_days: float, now_s: float) -> List[dict]:
    """Old status:open records minus the exclusion tags."""
    kept = []
    for r in records:
        tags = set(r.get("tags") or [])
        if tags & EXCLUDE_TAGS:
            continue
        age = _age_days(r.get("createdAt"), now_s)
        if age is None or age >= min_age_days:
            kept.append(r)
    return kept


def classify(record: dict, age_days: Optional[float]) -> str:
    """Advisory bucket. See module docstring for the policy."""
    rtype = record.get("recordType") or ""
    if rtype in I4_TYPES:
        return "I4-EXEMPT"
    if age_days is None:
        return "MANUAL-REVIEW"
    if rtype in OBSERVATION_TYPES:
        return "RETIRE-PROPOSE"
    if rtype in REQUEST_TYPES and any(t in (record.get("tags") or []) for t in REQUEST_HINT_TAGS):
        return "RETIRE-PROPOSE"
    return "MANUAL-REVIEW"


def dry_run_retire(
    record_id: str,
    supersede_path: Path,
    min_age_days: float,
    runner: Callable,
) -> Dict[str, Any]:
    """Validate a retire proposal via supersede-record.sh --dry-run.

    The dry-run writes NOTHING (script contract); its exit code is the oracle:
    0 actionable, 2 policy-refused (self-resolved), 1 tool error.
    """
    note = (
        f"record-hygiene sweep: status:open older than {min_age_days:g}d; "
        "act is complete or ask resolved — retire with this note, or supersede "
        "explicitly if a successor exists"
    )
    proc = runner([
        "bash", str(supersede_path), "retire",
        "--old", record_id,
        "--note", note,
        "--role", ROLE, "--model", MODEL,
        "--dry-run",
    ], capture_output=True, text=True, timeout=60)
    return {
        "exit": proc.returncode,
        "stderr": (proc.stderr or "").strip()[:300],
    }


def load_state(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"candidates": {}, "runs": []}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1) + "\n")


def post_change_log(title: str, body: str) -> bool:
    try:
        proc = subprocess.run(
            ["/usr/bin/env", "bash", str(BIN_DIR / "post-change-log.sh"),
             "--title", title, "--body", body],
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _still_open(record_id: str, fetch: Callable[[str], Any]) -> bool:
    """TOCTOU guard: re-read the record right before reporting it."""
    try:
        rec = fetch(f"/api/agent-records/{record_id}")
    except Exception:
        return True  # probe failed -> keep (fail-safe toward reporting)
    return isinstance(rec, dict) and "status:open" in (rec.get("tags") or [])


def run_sweep(
    *,
    apply: bool,
    min_age_days: float,
    max_candidates: int,
    nebula_base: str,
    supersede_path: Path,
    state_path: Path,
    fetch: Callable[[str], Any],
    runner: Callable,
    out: Any = sys.stdout,
) -> int:
    now_s = time.time()
    try:
        records = scan_open_records(fetch)
    except Exception as exc:
        print(f"record-hygiene-sweep: scan failed: {exc}", file=out)
        return 2

    stale = stale_open_records(records, min_age_days, now_s)
    by_role: Dict[str, int] = {}
    for r in stale:
        by_role[r.get("role") or "?"] = by_role.get(r.get("role") or "?", 0) + 1
    print(
        f"record-hygiene-sweep: {len(records)} status:open record(s), "
        f"{len(stale)} stale (>={min_age_days:g}d, exclusions applied) "
        f"across {len(by_role)} role(s) (apply={apply})",
        file=out,
    )

    buckets: Dict[str, List[dict]] = {"RETIRE-PROPOSE": [], "MANUAL-REVIEW": [], "I4-EXEMPT": []}
    for r in stale:
        buckets.setdefault(classify(r, _age_days(r.get("createdAt"), now_s)), []).append(r)

    # ── validation (dry-run oracle) — only under --apply ────────────────
    results: Dict[str, Dict[str, Any]] = {}
    tool_errors = 0
    if apply:
        state = load_state(state_path)
        cands = buckets["RETIRE-PROPOSE"][:max_candidates]
        overflow = len(buckets["RETIRE-PROPOSE"]) - len(cands)
        for r in cands:
            rid = r["id"]
            res = dry_run_retire(rid, supersede_path, min_age_days, runner)
            verdict = {0: "ACTIONABLE", 2: "SELF-RESOLVED"}.get(res["exit"], "TOOL-ERROR")
            if verdict == "TOOL-ERROR":
                tool_errors += 1
            results[rid] = {"verdict": verdict, **res}
        new_ids = [r["id"] for r in cands
                   if results[r["id"]]["verdict"] == "ACTIONABLE" and r["id"] not in state["candidates"]]
        # TOCTOU guard: drop candidates that went terminal between scan and post
        new_ids = [i for i in new_ids if _still_open(i, fetch)]

        if new_ids:
            now_iso = _now_iso()
            for i in new_ids:
                entry = state["candidates"].setdefault(i, {"first": now_iso})
                entry.update({"last": now_iso, "count": int(entry.get("count", 0)) + 1})
            ok = post_change_log(
                _report_title(len(new_ids), min_age_days),
                _report_body(new_ids, buckets, results, by_role, stale,
                             records, min_age_days, overflow),
            )
            if not ok:
                print("record-hygiene-sweep: WARNING — change-log post failed", file=out)
                tool_errors += 1
        else:
            print("record-hygiene-sweep: no new actionable candidates — no evidence post (dedup)", file=out)
        state["runs"].append({
            "at": _now_iso(), "stale": len(stale), "validated": len(results),
            "new": len(new_ids), "tool_errors": tool_errors,
        })
        state["runs"] = state["runs"][-50:]
        save_state(state_path, state)

    # ── human-readable summary ──────────────────────────────────────────
    for r in stale:
        rid, verdict = r["id"][:8], results.get(r["id"], {}).get("verdict")
        age = _age_days(r.get("createdAt"), now_s)
        age_s = f"{age:.0f}d" if age is not None else "AGE-UNKNOWN"
        bucket = classify(r, age)
        line = f"  {bucket:<14} {rid} {r.get('role') or '?'}:{r.get('recordType')} {age_s} \"{(r.get('title') or '')[:70]}\""
        if verdict:
            line += f" [dry-run: {verdict}]"
        print(line, file=out)
    return 1 if tool_errors else 0


def _report_title(new_count: int, min_age_days: float) -> str:
    return (f"record-hygiene sweep: {new_count} stale status:open record(s) "
            f"(>={min_age_days:g}d) proposed for retire/supersede — owning roles decide")


def _report_body(new_ids, buckets, results, by_role, stale, records,
                 min_age_days, overflow) -> str:
    lines = [
        "Automated sweep by bin/record_hygiene_sweep.py "
        f"({_now_iso()}). READ-ONLY over records: candidates below were "
        "validated with supersede-record.sh --dry-run (zero writes); an "
        "OWNING ROLE must run the real retire/supersede (Decision 24, "
        "cfcc9d65). Deduped by state file: each candidate reported once.\n",
        f"**Census:** {len(records)} status:open total, {len(stale)} stale "
        f"(>={min_age_days:g}d) — by role: " +
        ", ".join(f"{k} {v}" for k, v in sorted(by_role.items())) + ".",
    ]
    if overflow > 0:
        lines.append(f"Validation cap hit: {overflow} additional candidates NOT dry-run-validated this run.")
    lines.append("\n**New actionable candidates** (proposed command per record):")
    for i in new_ids:
        rec = next((r for r in stale if r["id"] == i), {})
        title = (rec.get("title") or "")[:80].replace("\n", " ")
        lines.append(
            f"- `{i[:8]}` ({rec.get('role')}:{rec.get('recordType')}) \"{title}\" → "
            f"`bin/supersede-record.sh retire --old {i} --note \"<outcome>\"` "
            "(or supersede with a successor if one exists)"
        )
    resolved = [rid for rid, v in results.items() if v["verdict"] == "SELF-RESOLVED"]
    if resolved:
        lines.append(f"\n**Self-resolved during validation** (dry-run refused, exit 2 — convention already closed them): " +
                     ", ".join(f"`{i[:8]}`" for i in resolved) + ".")
    if buckets["I4-EXEMPT"]:
        lines.append(f"\n**I4-exempt stale records** (prompt/response — supersede/retire refuses by policy; "
                     "listed as guard evidence, no action proposed): " +
                     ", ".join(f"`{r['id'][:8]}`" for r in buckets["I4-EXEMPT"]) + ".")
    manual = buckets["MANUAL-REVIEW"]
    if manual:
        lines.append(f"\n**Manual review** ({len(manual)} — may still govern; not auto-proposed): " +
                     ", ".join(f"`{r['id'][:8]}`" for r in manual[:20]) +
                     ("…" if len(manual) > 20 else "") + ".")
    lines.append("\n*Re-run with `--dry-run` removed to execute; per-record outcome notes belong to the owning role.*")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Stale status:open record hygiene sweep (see module docstring).")
    ap.add_argument("--apply", action="store_true",
                    help="validate via dry-run oracle, update state, post evidence (default: classify only)")
    ap.add_argument("--min-age-days", type=float, default=DEFAULT_MIN_AGE_DAYS,
                    help=f"stale threshold (default {DEFAULT_MIN_AGE_DAYS})")
    ap.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES,
                    help=f"max dry-run validations per run (default {DEFAULT_MAX_CANDIDATES})")
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--supersede-path", default=str(SUPERSEDE))
    ap.add_argument("--state", default=DEFAULT_STATE)
    args = ap.parse_args(argv)

    base = args.nebula_url.rstrip("/")

    def fetch(url_suffix: str) -> Any:
        with urllib.request.urlopen(base + url_suffix, timeout=15) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} for {url_suffix}")
            return json.loads(resp.read().decode("utf-8"))

    return run_sweep(
        apply=args.apply,
        min_age_days=max(0.0, args.min_age_days),
        max_candidates=max(1, args.max_candidates),
        nebula_base=base,
        supersede_path=Path(args.supersede_path),
        state_path=Path(args.state),
        fetch=fetch,
        runner=subprocess.run,
    )


if __name__ == "__main__":
    sys.exit(main())
