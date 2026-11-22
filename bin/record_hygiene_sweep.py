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
  5. ASK-CLOSE (orthogonal rule): open `to:tester` ask rows are a lifecycle
     class of their own, and 42 of the 53 live ones carry NO status:open tag —
     so they get their own scan (tag=to:tester) rather than riding step 1.
     An ask row is serviced by a GATE PASS, so a PR that merged or closed
     never gates again and its ask is unreachable by the janitor's close-out
     forever (engineer proposal 849d61cb). This rule plans that class:
       resolve  pr:<N> tag first, else parse '#<N>' out of the title
                (several numbers = a multi-PR ask; ALL of them must be
                non-open, any open or unknown one fails closed)
       PR OPEN          -> HOLD-pr-open (the gate close-out owns it)
       PR UNKNOWN       -> HOLD-pr-unknown (cannot establish it is dead)
       no number        -> HOLD-no-pr (needs a human decision, not a guess)
       MERGED/CLOSED    -> SUPERSEDE under a live attestation for that PR
                           (gate contract: merge_pr.py's attestation-shape +
                           withdrawn + mention predicates), else RETIRE
     Every plan is validated read-only through supersede-record.sh --dry-run
     in BOTH modes, so the printed verdict is a checked one, not a guess.
     Title parsing is a fallback of last resort: it can only mis-attribute a
     row that names no pr: tag AND no real PR in its title.

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
  record_hygiene_sweep.py --no-ask-close   # skip rule 5

Exit codes: 0 ok (or dry-run classification); 1 some validations errored;
            2 hard error (nebula unreachable — nothing written).
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# ── rule 5 (ASK-CLOSE) vocabulary ────────────────────────────────────────
# Mirrors bin/attestation_janitor.py's request vocabulary: a row asks while it
# carries a request type and no terminal status. The janitor closes these on a
# gate pass; this rule closes the ones whose PR will never gate again.
ASK_INBOX_TAG = "to:tester"
ASK_TYPE_TAGS = frozenset({
    "type:request", "type:approval-request",
    "type:verification-request", "type:attestation-request",
})
ASK_CLOSED_STATUS_TAGS = frozenset({"status:superseded", "status:retired", "status:done"})
NON_OPEN_PR_STATES = frozenset({"MERGED", "CLOSED"})
PR_TAG_RE = re.compile(r"^pr:(\d+)$")
TITLE_PR_RE = re.compile(r"#([1-9]\d{1,4})\b")   # '#613'; never '#0008' (migration) or '@7f43c6e6d'
MAX_ASK_VALIDATIONS = 120


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


def _age_key(record: dict, now_s: float) -> float:
    """Sort key where newest sorts first and UNDATED sorts last (never freshest)."""
    age = _age_days(record.get("createdAt"), now_s)
    return float("inf") if age is None else age


def scan_by_tag(fetch: Callable[[str], Any], tag: str) -> List[dict]:
    """Every record carrying `tag`, following offset pagination."""
    out: List[dict] = []
    limit = PAGE_LIMIT
    for page in range(MAX_PAGES):
        url = f"/api/agent-records?tag={tag}&limit={limit}&offset={page * limit}"
        data = fetch(url)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise RuntimeError(f"unexpected payload for {url}")
        out.extend(items)
        if len(items) < limit:
            break
    return out


def scan_open_records(fetch: Callable[[str], Any]) -> List[dict]:
    """All records tagged status:open."""
    return scan_by_tag(fetch, "status:open")


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


# ── rule 5: ask rows whose PR is merged or closed ───────────────────────

def ask_rows(records: List[dict]) -> List[dict]:
    """Rows that are still ASKING the tester (request-shaped, no terminal status)."""
    out = []
    for r in records:
        tags = {str(t or "").lower() for t in (r.get("tags") or [])}
        if ASK_INBOX_TAG not in tags:
            continue
        if not (tags & ASK_TYPE_TAGS) or (tags & ASK_CLOSED_STATUS_TAGS):
            continue
        out.append(r)
    return out


def pr_numbers(record: dict) -> "tuple[List[int], str]":
    """(PR numbers this row asks about, source).

    pr:<N> tags win outright (they are the intent, not the prose). Only a row
    with no pr: tag falls back to '#<N>' numbers in the title, which is how
    most historical asks name their PR.
    """
    tags = [str(t or "").lower() for t in (record.get("tags") or [])]
    tagged = sorted({int(m.group(1)) for t in tags for m in [PR_TAG_RE.match(t)] if m})
    if tagged:
        return tagged, "tag"
    title_nums = sorted({int(n) for n in TITLE_PR_RE.findall(record.get("title") or "")})
    return title_nums, ("title" if title_nums else "none")


def ask_plan(
    record: dict,
    pr_state: Callable[[int], str],
    attestations_for: Callable[[int], "tuple[Optional[dict], str]"],
    now_s: float,
) -> Dict[str, Any]:
    """Decide what should happen to one ask row, without doing it.

    SUPERSEDE / RETIRE require EVERY PR the row names to be merged or closed;
    one open or unresolvable number holds the row. That way a mis-parsed title
    can only ever cause a hold, never a close.
    """
    prs, source = pr_numbers(record)
    plan: Dict[str, Any] = {
        "id": record.get("id"), "title": record.get("title") or "",
        "verdict": "", "prs": prs, "source": source, "states": {},
        "attestation": None, "reason": "", "dry_run": None,
    }
    if not prs:
        plan["verdict"] = "HOLD-no-pr"
        plan["reason"] = "no pr: tag and no '#<N>' in the title"
        return plan
    states = {p: (pr_state(p) or "UNKNOWN") for p in prs}
    plan["states"] = states
    open_prs = sorted(p for p, s in states.items() if s == "OPEN")
    unknown = sorted(p for p, s in states.items() if s not in NON_OPEN_PR_STATES and s != "OPEN")
    if open_prs:
        plan["verdict"] = "HOLD-pr-open"
        plan["reason"] = ("still open, so the gate close-out owns it: "
                          + ", ".join(f"#{p}" for p in open_prs))
        return plan
    if unknown:
        plan["verdict"] = "HOLD-pr-unknown"
        plan["reason"] = "state unknown: " + ", ".join(f"#{p}={states[p]}" for p in unknown)
        return plan

    best, why = None, "no-attestation"
    for p in prs:
        cand, reason = attestations_for(p)
        if cand is None:
            if why == "no-attestation":
                why = reason
            continue
        if best is None or _age_key(cand, now_s) < _age_key(best, now_s):
            best = cand
    plan["attestation"] = best
    prs_s = ", ".join(f"#{p}" for p in prs)
    if best:
        plan["verdict"] = "SUPERSEDE"
        plan["reason"] = f"{prs_s} merged/closed; serviced by attestation {str(best.get('id'))[:8]}"
    else:
        plan["verdict"] = "RETIRE"
        plan["reason"] = f"{prs_s} merged/closed with no live attestation ({why})"
    return plan


def dry_run_ask_close(plan: Dict[str, Any], supersede_path: Path, runner: Callable) -> Dict[str, Any]:
    """Validate one ask-close plan read-only (supersede-record.sh --dry-run).

    Supersede when a live attestation exists (the pointer must name it),
    retire otherwise. Exit code is the oracle, exactly as in dry_run_retire.
    """
    att = plan.get("attestation")
    note = f"record-hygiene sweep (ask-close): {plan.get('reason')}"
    if att:
        cmd = ["bash", str(supersede_path), "supersede", "--old", str(plan["id"]),
               "--new", str(att.get("id")), "--reason", note]
    else:
        cmd = ["bash", str(supersede_path), "retire", "--old", str(plan["id"]), "--note", note]
    proc = runner(cmd + ["--role", ROLE, "--model", MODEL, "--dry-run"],
                  capture_output=True, text=True, timeout=60)
    return {
        "exit": proc.returncode,
        "oracle": {0: "ACTIONABLE", 2: "SELF-RESOLVED"}.get(proc.returncode, "TOOL-ERROR"),
        "stderr": (proc.stderr or "").strip()[:300],
    }


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


def _still_asking(record_id: str, fetch: Callable[[str], Any]) -> bool:
    """TOCTOU guard for ask rows: still request-shaped, still no terminal status.

    Deliberately NOT _still_open: 42 of the 53 live ask rows carry no
    status:open tag, so a status:open test would silently discard almost every
    row the rule exists to close.
    """
    try:
        rec = fetch(f"/api/agent-records/{record_id}")
    except Exception:
        return True  # probe failed -> keep (fail-safe toward reporting)
    if not isinstance(rec, dict):
        return False
    tags = {str(t or "").lower() for t in (rec.get("tags") or [])}
    return (ASK_INBOX_TAG in tags and bool(tags & ASK_TYPE_TAGS)
            and not (tags & ASK_CLOSED_STATUS_TAGS))


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
    pr_state: Optional[Callable[[int], str]] = None,
    attestations_for: Optional[Callable[[int], "tuple[Optional[dict], str]"]] = None,
    ask_close: bool = True,
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

    results: Dict[str, Dict[str, Any]] = {}
    tool_errors = 0
    ask_new: List[Dict[str, Any]] = []

    # ── rule 5: plan every open ask row (read-only in both modes) ───────
    ask_plans: List[Dict[str, Any]] = []
    if ask_close and pr_state is not None and attestations_for is not None:
        try:
            ask_plans = [ask_plan(r, pr_state, attestations_for, now_s)
                         for r in ask_rows(scan_by_tag(fetch, ASK_INBOX_TAG))]
        except Exception as exc:  # a secondary rule never aborts the sweep
            print(f"record-hygiene-sweep: WARNING — ask scan failed: {exc}", file=out)
            tool_errors += 1
        closable = [p for p in ask_plans if p["verdict"] in ("SUPERSEDE", "RETIRE")]
        # validated in check-only mode too: the printed verdict is a checked one
        for p in closable[:MAX_ASK_VALIDATIONS]:
            p["dry_run"] = dry_run_ask_close(p, supersede_path, runner)
            if p["dry_run"]["oracle"] == "TOOL-ERROR":
                tool_errors += 1

    # ── validation (dry-run oracle) — only under --apply ────────────────
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

        # rule 5 evidence: only NEW, oracle-confirmed, still-open ask rows
        ask_new = [p for p in ask_plans
                   if p.get("dry_run") and p["dry_run"]["oracle"] == "ACTIONABLE"
                   and "ask:" + str(p["id"]) not in state["candidates"]
                   and _still_asking(str(p["id"]), fetch)]
        if ask_new:
            now_iso = _now_iso()
            for p in ask_new:
                entry = state["candidates"].setdefault("ask:" + str(p["id"]), {"first": now_iso})
                entry.update({"last": now_iso, "count": int(entry.get("count", 0)) + 1})
            ok = post_change_log(_ask_report_title(len(ask_new)), _ask_report_body(ask_new))
            if not ok:
                print("record-hygiene-sweep: WARNING — change-log post failed (ask rule)", file=out)
                tool_errors += 1
        else:
            print("record-hygiene-sweep: ask rule — no new oracle-confirmed ask rows (dedup)", file=out)

        state["runs"].append({
            "at": _now_iso(), "stale": len(stale), "validated": len(results),
            "new": len(new_ids), "tool_errors": tool_errors,
            "ask_rows": len(ask_plans), "ask_new": len(ask_new),
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

    # ── rule 5 summary: one verdict per ask row ─────────────────────────
    if ask_plans:
        counts: Dict[str, int] = {}
        for p in ask_plans:
            counts[p["verdict"]] = counts.get(p["verdict"], 0) + 1
        print(
            f"record-hygiene-sweep: {len(ask_plans)} open {ASK_INBOX_TAG} ask row(s) — "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
            file=out,
        )
        for p in ask_plans:
            prs = ",".join(f"#{n}" for n in p["prs"]) or "-"
            state_s = "/".join(sorted(set(p["states"].values()))) or "-"
            att = f" -> {str(p['attestation'].get('id'))[:8]}" if p["attestation"] else ""
            oracle = f" [dry-run: {p['dry_run']['oracle']}]" if p.get("dry_run") else ""
            print(
                f"  {p['verdict']:<16} {str(p['id'])[:8]} {prs:<12} {state_s:<7}"
                f"{att}{oracle} src={p['source']:<5} \"{p['title'][:58]}\"",
                file=out,
            )
    return 1 if tool_errors else 0


def _ask_report_title(new_count: int) -> str:
    return (f"record-hygiene sweep (ask-close): {new_count} open {ASK_INBOX_TAG} ask row(s) "
            "on merged/closed PRs proposed for supersede/retire — owning role decides")


def _ask_report_body(plans: List[Dict[str, Any]]) -> str:
    """Evidence for rule 5: what each ask row resolved to and why."""
    lines = [
        "Automated sweep by bin/record_hygiene_sweep.py "
        f"({_now_iso()}), rule ASK-CLOSE. READ-ONLY over records: every plan "
        "below was validated with supersede-record.sh --dry-run (zero writes); "
        "an OWNING ROLE must execute it (Decision 24, cfcc9d65).\n",
        "These rows ask the tester about a PR that is already MERGED or CLOSED, "
        "so no future gate pass can ever service them and the janitor's "
        "gate-driven close-out cannot reach them by construction.",
        "\n**Plans** (one line per row):",
    ]
    for p in plans:
        prs = ", ".join(f"#{n}" for n in p["prs"])
        if p["verdict"] == "SUPERSEDE":
            new_id = str(p["attestation"].get("id"))
            cmd = (f'supersede-record.sh supersede --old {p["id"]} --new {new_id} '
                   f'--reason "<outcome>"')
        else:
            cmd = f'supersede-record.sh retire --old {p["id"]} --note "<outcome>"'
        lines.append(
            f"- `{str(p['id'])[:8]}` {p['verdict']} — {prs} "
            f"({p['reason']}) — src={p['source']}; "
            f"\"{p['title'][:70].replace(chr(10), ' ')}\" → `bin/{cmd}`"
        )
    lines.append(
        "\nSupersede is used only where a live (non-withdrawn) attestation for "
        "that PR exists — the gate's own shape predicates, not title text. "
        "Withdrawn-only or absent attestations retire instead."
    )
    lines.append("\n*Re-run with `--dry-run` removed to execute; per-record outcome notes belong to the owning role.*")
    return "\n".join(lines)


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


_merge_pr_module: Any = None


def _merge_pr() -> Any:
    """Load bin/merge_pr.py by path (once) for the gate's attestation predicates.

    Deliberately the GATE module, not attestation_janitor.py: this sweep runs
    weekly from a checkout of main, so it must not depend on unmerged janitor
    work. What counts as a real attestation therefore cannot drift from what
    the merge gate accepts.
    """
    global _merge_pr_module
    if _merge_pr_module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("merge_pr", BIN_DIR / "merge_pr.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["merge_pr"] = mod
        spec.loader.exec_module(mod)
        _merge_pr_module = mod
    return _merge_pr_module


def make_pr_state() -> Callable[[int], str]:
    """PR number -> OPEN / MERGED / CLOSED from one cached gh listing.

    A number gh does not report becomes NOT-FOUND (never "assume dead"); an
    unavailable gh becomes UNKNOWN for every row. Both fail closed.
    """
    cache: Dict[int, str] = {}
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", "3000",
             "--json", "number,state"],
            capture_output=True, text=True, timeout=180, cwd=str(BIN_DIR.parent),
        )
        for row in json.loads(proc.stdout or "[]"):
            if isinstance(row, dict) and row.get("number") is not None:
                cache[int(row["number"])] = str(row.get("state") or "UNKNOWN").upper()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        cache = {}

    def pr_state(number: int) -> str:
        if not cache:
            return "UNKNOWN"
        return cache.get(int(number), "NOT-FOUND")

    pr_state.cache = cache  # type: ignore[attr-defined]
    return pr_state


def make_attestations_for(
    fetch: Callable[[str], Any],
) -> Callable[[int], "tuple[Optional[dict], str]"]:
    """PR number -> (newest live attestation naming it, why none qualifies).

    Shape and withdrawal tests come from the gate (merge_pr.py), and the row
    must actually mention the PR, so a tester row about a DIFFERENT PR can
    never become the successor pointer. Withdrawn-only returns no attestation
    (a tombstone is not a service) and the ask row retires instead.
    """
    mp = _merge_pr()
    cache: Dict[int, "tuple[Optional[dict], str]"] = {}

    def attestations_for(pr: int) -> "tuple[Optional[dict], str]":
        if pr in cache:
            return cache[pr]
        seen: Dict[str, dict] = {}
        for tag in (f"pr:{pr}", f"gate:{pr}"):
            for r in scan_by_tag(fetch, tag):
                if isinstance(r, dict) and r.get("id"):
                    seen[str(r["id"])] = r
        shaped = [r for r in seen.values() if mp.is_attestation_record(r)]
        live = [r for r in shaped
                if not mp.is_withdrawn(r) and mp.attestation_mentions_pr(r, pr)]
        if live:
            live.sort(key=lambda r: _age_key(r, time.time()))
            cache[pr] = (live[0], "serviced")
        else:
            cache[pr] = (None, "only-withdrawn" if shaped else "no-attestation")
        return cache[pr]

    return attestations_for


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
    ap.add_argument("--no-ask-close", action="store_true",
                    help="skip rule ASK-CLOSE (open to:tester rows on merged/closed PRs)")
    args = ap.parse_args(argv)

    base = args.nebula_url.rstrip("/")

    def fetch(url_suffix: str) -> Any:
        with urllib.request.urlopen(base + url_suffix, timeout=15) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} for {url_suffix}")
            return json.loads(resp.read().decode("utf-8"))

    pr_state = make_pr_state()
    if not pr_state.cache:  # type: ignore[attr-defined]
        print("record-hygiene-sweep: WARNING — gh PR state unavailable; "
              "every ask row will HOLD (fail-closed)", file=sys.stderr)

    return run_sweep(
        apply=args.apply,
        min_age_days=max(0.0, args.min_age_days),
        max_candidates=max(1, args.max_candidates),
        nebula_base=base,
        supersede_path=Path(args.supersede_path),
        state_path=Path(args.state),
        fetch=fetch,
        runner=subprocess.run,
        pr_state=pr_state,
        attestations_for=make_attestations_for(fetch),
        ask_close=not args.no_ask_close,
    )


if __name__ == "__main__":
    sys.exit(main())
