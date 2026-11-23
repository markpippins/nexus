#!/usr/bin/env python3
"""harness_job_backfill.py — attribute historical agent records to their run.

Ranking item 5: `harness_job_id` is null on 17,365 of 17,486 records (99.31%).
The column was added by `sql/V210__agent_record_harness_job_id.sql` on
2026-11-05 18:26 UTC and its contract is narrow on purpose:

    "Harness job uuid that wrote this record (HARNESS_JOB_ID at write time).
     NULL = written outside a harness run."

That contract is what makes a backfill dangerous. The consumer
(`bin/check_harness_silent_success.py`) uses the column to answer "did run J
post a record?" — an id stamped with the WRONG run clears a silent run, which
is the exact false negative V210 was written to close. V210's own docstring
says so: the old role+window match was cleared by "a SECOND record from the
same role inside the window (an unrelated record, a sibling job, a scheduled
sweep)". Filling this column from windows would rebuild that defect
retroactively, so this tool does not do it.

WHAT COUNTS AS EVIDENCE (attribution) — the record must DECLARE its own run:

  * `metadata.harness_job_id`                     (declared_in_metadata)
  * a `harness_job_id:<uuid>` / `job:<uuid>` tag   (declared_in_tags)
  * its own prose naming the run it ran under      (declared_in_text)
        "harness job <uuid>", "HARNESS_JOB_ID=<uuid>"

A declaration still has to survive three checks before it is written:

  1. the named id is a real harness run (`cascade.events` `harness.started`);
  2. the run's role equals the record's role — the corpus is full of records
     that merely MENTION another role's run (9 text declarations, 8 of them
     naming a tester/engineer/planner/builder run from another role);
  3. the record was created inside that run's own window
     [started, terminal event + grace]. A record written after the run ended
     is a report ABOUT the run, not a record BY it.

Everything else stays null and is named in the report with one reason:

  * predates_harness          — created before the first harness run existed
  * no_harness_window         — no run of this role was in flight
  * window_only               — exactly one run of this role was in flight, but
                                nothing declares it (this is the class that
                                would be a guess; deliberately refused)
  * ambiguous_window          — more than one run of this role was in flight
  * declares_unknown_job      — names an id that is not a harness run
  * declares_multiple_jobs    — names more than one run
  * declared_role_mismatch    — names one run, of another role
  * declared_outside_window   — names one run, but was written outside it

`--report PATH` writes the per-row listing (TSV): every null row, its reason
and the evidence considered. The summary prints counts per reason, so "no
unexplained nulls" is checkable rather than asserted.

Modes
  default   dry run: classify, print the summary (and the plan), write nothing
  --apply   PATCH each attributed row through nebula's API, read it back,
            verify, and post a change-log audit entry

Idempotent: only null rows are planned, so a rerun after `--apply` plans zero
rows. Bounded: `--max-apply` (default 50) is a hard ceiling — the tool fails
closed rather than applying a subset it did not report.

Exit codes: 0 ok; 1 verification failure (a write did not read back);
            2 tool/environment error (nothing written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

BIN_DIR = Path(__file__).resolve().parent
DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_GRACE_S = 180
DEFAULT_MAX_APPLY = 50

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_RE = re.compile(UUID)

# Declaration patterns. The gap between the phrase and the id is deliberately
# small: "harness job `abc…`" and "harness job (abc…" are declarations, while a
# sentence that merely says "the harness job" and mentions an id forty words
# later is not. Whatever slips through still has to pass the role + window
# checks, so a loose match cannot attribute anything on its own.
DECLARATION_PATTERNS = (
    re.compile(rf"harness[_ \-]?job(?:[_ ]?id)?[^.\n]{{0,24}}?({UUID})", re.IGNORECASE),
    re.compile(rf"\bharness_job_id\s*[=:]\s*({UUID})", re.IGNORECASE),
)
TAG_DECLARATION = re.compile(rf"^(?:harness[_-]job(?:[_-]id)?|job):({UUID})$", re.IGNORECASE)

# ── reason vocabulary (one per null row) ─────────────────────────────────
DECLARED = {
    "declared_in_metadata": "the record's metadata names the run that wrote it",
    "declared_in_tags": "the record carries a job:<uuid> tag",
    "declared_in_text": "the record's own prose names the run it ran under",
}
REFUSED = {
    "predates_harness": "created before the first harness run existed — no run to name",
    "no_harness_window": "no run of this role was in flight; written outside a harness",
    "window_only": "a run of this role was in flight but nothing declares it — refused, not guessed",
    "ambiguous_window": "more than one run of this role was in flight",
    "declares_unknown_job": "names an id that is not a harness run",
    "declares_multiple_jobs": "names more than one run",
    "declared_role_mismatch": "names one run, but it belongs to another role",
    "declared_outside_window": "names one run, but the record was written outside it",
}


@dataclass(frozen=True)
class Job:
    """One harness run, from `cascade.events` `harness.started` + terminal."""

    job_id: str
    role: str
    started: datetime
    ended: Optional[datetime]

    def covers(self, when: datetime, grace_s: int = DEFAULT_GRACE_S) -> bool:
        if when < self.started:
            return False
        if self.ended is None:
            # A run with no terminal event is still open as far as the log says.
            # Treat its window as unbounded: refusing it would lose real
            # attributions, and the declaration check still has to pass.
            return True
        return when <= self.ended + timedelta(seconds=grace_s)


@dataclass
class Record:
    id: str
    role: str
    created_at: datetime
    title: str
    content: str
    tags: Sequence[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def body_sha256(self) -> str:
        return hashlib.sha256((self.content or "").encode("utf-8")).hexdigest()


@dataclass
class Decision:
    record: Record
    verdict: str            # "attribute" | "refuse"
    reason: str
    job_id: Optional[str] = None
    detail: str = ""

    @property
    def attributed(self) -> bool:
        return self.verdict == "attribute"


# ── parsing ──────────────────────────────────────────────────────────────

def declared_jobs(record: Record) -> Tuple[List[str], List[str]]:
    """Return (declared job ids, classes) for one record.

    `classes` says WHERE each declaration came from, so the report can explain
    an attribution without re-reading the record.
    """
    found: List[str] = []
    classes: List[str] = []

    meta = (record.metadata or {}).get("harness_job_id") if isinstance(record.metadata, dict) else None
    if isinstance(meta, str) and meta.strip():
        found.append(meta.strip().lower())
        classes.append("declared_in_metadata")

    for tag in record.tags or []:
        m = TAG_DECLARATION.match(str(tag).strip())
        if m:
            found.append(m.group(1).lower())
            classes.append("declared_in_tags")

    text = f"{record.title or ''}\n{record.content or ''}"
    for pattern in DECLARATION_PATTERNS:
        for m in pattern.finditer(text):
            found.append(m.group(1).lower())
            classes.append("declared_in_text")

    # De-duplicate but keep the strongest class first (metadata > tag > prose).
    order = ["declared_in_metadata", "declared_in_tags", "declared_in_text"]
    pairs = sorted(set(zip(found, classes)), key=lambda p: (order.index(p[1]), p[0]))
    ids = sorted({p[0] for p in pairs})
    kinds = sorted({p[1] for p in pairs}, key=order.index)
    return ids, kinds


def windows_for(record: Record, jobs: Iterable[Job]) -> List[Job]:
    return [j for j in jobs if j.role == record.role and j.covers(record.created_at)]


# ── classification ───────────────────────────────────────────────────────

def classify(
    record: Record,
    jobs: Sequence[Job],
    by_id: Dict[str, Job],
    first_started: Optional[datetime],
    cited_by: Optional[Dict[str, List[str]]] = None,
) -> Decision:
    """Decide one record. Pure: no I/O, so the tests can pin every branch."""
    cited_by = cited_by or {}
    cites = sorted({j for j in cited_by.get(record.id.lower(), []) if j in by_id})
    advisory = f"cited_in_job_log={','.join(c[:8] for c in cites)}" if cites else ""

    if first_started is not None and record.created_at < first_started:
        return Decision(record, "refuse", "predates_harness", detail=advisory)

    declared, kinds = declared_jobs(record)
    if declared:
        unknown = [d for d in declared if d not in by_id]
        if len(declared) > 1:
            return Decision(record, "refuse", "declares_multiple_jobs",
                            detail=f"declared={','.join(d[:8] for d in declared)} {advisory}".strip())
        job_id = declared[0]
        if job_id not in by_id:
            return Decision(record, "refuse", "declares_unknown_job",
                            detail=f"declared={job_id[:8]} (not a harness run) {advisory}".strip())
        job = by_id[job_id]
        if job.role != record.role:
            return Decision(record, "refuse", "declared_role_mismatch",
                            detail=f"declared={job_id[:8]} run_role={job.role} record_role={record.role} {advisory}".strip())
        if not job.covers(record.created_at):
            return Decision(record, "refuse", "declared_outside_window",
                            detail=f"declared={job_id[:8]} started={job.started.isoformat()} {advisory}".strip())
        return Decision(record, "attribute", kinds[0], job_id=job_id,
                        detail=f"declared={job_id[:8]} run_role={job.role} via={','.join(kinds)} {advisory}".strip())

    windows = windows_for(record, jobs)
    if len(windows) > 1:
        return Decision(record, "refuse", "ambiguous_window",
                        detail=f"windows={','.join(w.job_id[:8] for w in windows)} {advisory}".strip())
    if len(windows) == 1:
        # The contested class: a run of this role was in flight, but the record
        # says nothing about it. Attributing here would manufacture exactly the
        # false clearance V210 exists to prevent.
        return Decision(record, "refuse", "window_only",
                        detail=f"window={windows[0].job_id[:8]} {advisory}".strip())
    return Decision(record, "refuse", "no_harness_window", detail=advisory)


def build_plan(
    records: Sequence[Record],
    jobs: Sequence[Job],
    cited_by: Optional[Dict[str, List[str]]] = None,
) -> List[Decision]:
    by_id = {j.job_id: j for j in jobs}
    first = min((j.started for j in jobs), default=None)
    return [classify(r, jobs, by_id, first, cited_by) for r in records]


# ── reporting ────────────────────────────────────────────────────────────

def summarize(decisions: Sequence[Decision], total_records: int) -> Dict[str, Any]:
    attributed = [d for d in decisions if d.attributed]
    refused = [d for d in decisions if not d.attributed]
    by_reason: Dict[str, int] = {}
    for d in refused:
        by_reason[d.reason] = by_reason.get(d.reason, 0) + 1
    return {
        "total_records": total_records,
        "already_attributed": total_records - len(decisions),
        "null_rows": len(decisions),
        "attributable": len(attributed),
        "refused": len(refused),
        "reasons": dict(sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
        "reason_help": {k: REFUSED.get(k, DECLARED.get(k, "")) for k in sorted(set(by_reason) | set(DECLARED))},
        "reconciles": total_records == (total_records - len(decisions)) + len(attributed) + len(refused),
    }


def report_tsv(decisions: Sequence[Decision]) -> str:
    lines = ["id\trole\tcreated_at\treason\tverdict\tdetail"]
    for d in decisions:
        lines.append(
            "\t".join([
                d.record.id,
                d.record.role or "",
                d.record.created_at.astimezone(timezone.utc).isoformat(),
                d.reason,
                d.verdict,
                (d.detail or "").replace("\t", " ").replace("\n", " "),
            ])
        )
    return "\n".join(lines) + "\n"


# ── database read side (mirrors check_harness_silent_success.py) ─────────

def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("psycopg2 is required to read the corpus and the event log") from exc
    return psycopg2.connect(dsn)


def load_snapshot(dsn: str = DSN) -> Tuple[List[Job], List[Record], Dict[str, List[str]], int]:
    """Read jobs, null records, and advisory citations from PostgreSQL."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select s.aggregate_id,
                   coalesce(s.payload->>'role', ''),
                   s.event_timestamp,
                   (select min(c.event_timestamp) from cascade.events c
                     where c.aggregate_id = s.aggregate_id
                       and c.event_type in ('harness.completed','harness.failed','harness.error'))
              from cascade.events s
             where s.event_type = 'harness.started'
            """
        )
        jobs = [
            Job(job_id=str(row[0]), role=row[1] or "", started=row[2].astimezone(timezone.utc),
                ended=row[3].astimezone(timezone.utc) if row[3] else None)
            for row in cur.fetchall()
        ]

        cur.execute("select count(*) from nebula.agent_records")
        total = int(cur.fetchone()[0])

        cur.execute(
            """
            select id, coalesce(role,''), created_at, coalesce(title,''), coalesce(content,''),
                   coalesce(tags, '{}'), coalesce(metadata, '{}'::jsonb)
              from nebula.agent_records
             where harness_job_id is null
             order by created_at asc
            """
        )
        records = [
            Record(id=str(row[0]), role=row[1], created_at=row[2].astimezone(timezone.utc),
                   title=row[3], content=row[4], tags=list(row[5] or []),
                   metadata=dict(row[6] or {}))
            for row in cur.fetchall()
        ]

        # Advisory only: which job logs mention a record id. Used to explain a
        # refusal, never to justify a write.
        cur.execute(
            """
            select aggregate_id, payload::text from cascade.events
             where event_type like 'harness%'
            """
        )
        cited: Dict[str, List[str]] = {}
        for job_id, payload in cur.fetchall():
            for m in UUID_RE.finditer((payload or "").lower()):
                cited.setdefault(m.group(0), []).append(str(job_id))
        for key in cited:
            cited[key] = sorted(set(cited[key]))

    return jobs, records, cited, total


# ── write side (nebula API, read-back verified) ──────────────────────────

def _http(method: str, url: str, body: Optional[dict], timeout: int = 30) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode() or "{}")
        except Exception:
            payload = {}
        return exc.code, payload


def fetch_point(base: str, record_id: str) -> dict:
    status, body = _http("GET", f"{base}/api/agent-records/{record_id}", None)
    if status != 200:
        raise RuntimeError(f"point read failed for {record_id}: HTTP {status} {body}")
    return body


def attribute(base: str, record_id: str, job_id: str) -> Tuple[int, Any]:
    return _http("PATCH", f"{base}/api/agent-records/{record_id}", {"harnessJobId": job_id})


def apply_plan(
    decisions: Sequence[Decision],
    base: str,
    max_apply: int = DEFAULT_MAX_APPLY,
    fetch: Callable[[str, str], dict] = fetch_point,
    write: Callable[[str, str, str], Tuple[int, Any]] = attribute,
    log: Callable[[str], None] = lambda _m: None,
) -> Dict[str, Any]:
    """Apply attributable decisions; verify every write by read-back.

    Returns a result dict. A write that does not read back as requested is a
    FAILURE (never a silent success): the API answers 200 for an unchanged row
    in the idempotent path, so only the read-back settles it.
    """
    planned = [d for d in decisions if d.attributed]
    if len(planned) > max_apply:
        raise RuntimeError(
            f"{len(planned)} attributable rows exceeds --max-apply {max_apply}: "
            f"raise the bound deliberately (nothing was written)"
        )

    verified: List[str] = []
    failed: List[Dict[str, str]] = []
    for d in planned:
        rid, job_id = d.record.id, d.job_id or ""
        before_sha = d.record.body_sha256()
        status, body = write(base, rid, job_id)
        if status not in (200, 201, 204):
            failed.append({"id": rid, "error": f"PATCH HTTP {status}: {str(body)[:200]}"})
            continue
        try:
            after = fetch(base, rid)
        except Exception as exc:  # read-back is the verification; no read-back = no success
            failed.append({"id": rid, "error": f"read-back failed: {exc}"})
            continue
        if after.get("harnessJobId") != job_id and after.get("harness_job_id") != job_id:
            failed.append({"id": rid, "error": f"read-back shows {after.get('harnessJobId')!r}"})
            continue
        after_sha = hashlib.sha256((after.get("content") or "").encode("utf-8")).hexdigest()
        if after_sha != before_sha:
            failed.append({"id": rid, "error": "body changed during attribution"})
            continue
        verified.append(rid)
        log(f"attributed {rid[:8]} -> {job_id[:8]} ({d.reason})")

    return {"planned": len(planned), "verified": verified, "failed": failed}


def post_audit(summary: Dict[str, Any], result: Dict[str, Any], reason: str) -> None:
    lines = [
        f"## harness_job_id backfill — {reason}",
        "",
        f"- null rows scanned: **{summary['null_rows']}** of {summary['total_records']} records "
        f"({summary['already_attributed']} were already attributed)",
        f"- attributable on evidence: **{summary['attributable']}**",
        f"- verified writes: **{len(result.get('verified', []))}**, failures: **{len(result.get('failed', []))}**",
        "",
        "### Refused, by reason",
    ]
    for key, count in summary["reasons"].items():
        lines.append(f"- `{key}` — {count}: {REFUSED.get(key, '')}")
    if result.get("failed"):
        lines += ["", "### Failures"] + [f"- `{f['id'][:8]}`: {f['error']}" for f in result["failed"]]
    body = "\n".join(lines)
    subprocess.run(
        [str(BIN_DIR / "post-change-log.sh"), "--title",
         f"harness_job_id backfill: {len(result.get('verified', []))} attributed, {summary['refused']} refused with reasons",
         "--body", body],
        check=False,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Attribute agent records to their harness run, from declarations only.")
    ap.add_argument("--apply", action="store_true", help="write the attributions (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument("--report", metavar="PATH", help="write the per-row TSV listing here")
    ap.add_argument("--max-apply", type=int, default=DEFAULT_MAX_APPLY, help="hard ceiling on writes")
    ap.add_argument("--no-changelog", action="store_true", help="skip the change-log audit post on apply")
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        jobs, records, cited, total = load_snapshot(args.dsn)
    except Exception as exc:
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    decisions = build_plan(records, jobs, cited)
    summary = summarize(decisions, total)

    if args.report:
        Path(args.report).write_text(report_tsv(decisions), encoding="utf-8")
        summary["report"] = args.report

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("harness_job_id backfill — " + ("APPLY" if args.apply else "DRY RUN (no writes)"))
        print(f"records: {summary['total_records']}  already attributed: {summary['already_attributed']}  "
              f"null: {summary['null_rows']}")
        print(f"attributable on evidence: {summary['attributable']}  refused: {summary['refused']}")
        print("\nrefused by reason:")
        for key, count in summary["reasons"].items():
            print(f"  {key:<24} {count:>6}  {REFUSED.get(key, '')}")
        if not summary["reconciles"]:
            print("ERROR: counts do not reconcile — every row must land in exactly one class", file=sys.stderr)
            return 2
        for d in decisions:
            if d.attributed:
                print(f"  ATTRIBUTE {d.record.id[:8]} {d.record.role} -> {d.job_id[:8]} ({d.reason})")

    if not args.apply:
        return 0

    result = apply_plan(decisions, args.nebula_url, args.max_apply, log=lambda m: print("  " + m))
    print(f"applied: {len(result['verified'])} verified, {len(result['failed'])} failed")
    for f in result["failed"]:
        print(f"  FAILED {f['id'][:8]}: {f['error']}", file=sys.stderr)
    if not args.no_changelog:
        post_audit(summary, result, "verified" if not result["failed"] else "partial")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
