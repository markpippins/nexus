#!/usr/bin/env python3
"""lifecycle_status_normalize.py — one lifecycle status per record.

Ranking item 7 (architect ruling 89064e45). `tags` carries lifecycle state in
`status:*` tags, and a tag query is how every reader asks "is this live?".
Nothing enforces that a row has at most one: seven rows in the corpus carry two
(`status:open` + `status:needs-response`, `status:held` + `status:ruled`,
`status:active` + `status:superseded`, …), so one row can match both a "live"
query and a "terminal" query. That is the same class of ambiguity the
retirement dialect removed for attestations (Decision 24 Amendment 1): drop
every lifecycle tag, add exactly one.

This tool applies the ruling's precedence — the LATER stage of a lifecycle wins
over the earlier one — to rows that carry more than one `status:*` tag:

  closed > superseded > retired > verified > done > implementation-authorized
         > held > ruled > resolved > ratified > merged > open > needs-response

Each pair the corpus actually contains is documented in PAIR_REASONS. A status
tag outside the ladder is NOT guessed at: that row is refused
(`unknown_status_tag`) and enumerated, because inventing a rank would invent
state. A row is written only when the ladder orders its tags; every dropped tag
is named in the row's own receipt (`metadata['record.status_normalized']`,
canonical owner-prefixed key per ruling 8f44e3e4), so the removal is auditable
and reversible from the record itself.

Dry run is the default. `--apply` is bounded, verifies every write by read-back
(exactly one status tag and it is the chosen one, non-status tags unchanged,
body sha256 unchanged, receipt present), refuses a row that changed since the
scan, and posts a change-log audit entry. A rerun after apply plans nothing.

Usage:
  lifecycle_status_normalize.py                       # dry run + summary
  lifecycle_status_normalize.py --report PATH         # + per-row TSV
  lifecycle_status_normalize.py --apply --max-apply 50
  lifecycle_status_normalize.py --json

Exit codes: 0 ok; 1 a write failed verification; 2 tool/environment error.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

BIN_DIR = Path(__file__).resolve().parent
DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_MAX_APPLY = 50
CONVENTION = "ranking item 7 ruling 89064e45"

# Later stage first. A row carrying two tags keeps the earlier entry.
LIFECYCLE_ORDER: List[str] = [
    "status:closed",
    "status:superseded",
    "status:retired",
    "status:verified",
    "status:done",
    "status:implementation-authorized",
    "status:held",
    "status:ruled",
    "status:resolved",
    "status:ratified",
    "status:merged",
    "status:active",
    "status:open",
    "status:needs-response",
]
_RANK = {tag: i for i, tag in enumerate(LIFECYCLE_ORDER)}

# Why each combination present in the corpus resolves the way it does.
PAIR_REASONS: Dict[Tuple[str, str], str] = {
    ("status:active", "status:superseded"):
        "supersession is terminal and the row carries superseded-by; asserting active on it is the "
        "defect item 4's guard exists to prevent",
    ("status:held", "status:ruled"):
        "a ruling that the operator then HELD is held; the hold is the later state",
    ("status:resolved", "status:closed"):
        "closed is the terminal state after resolved",
    ("status:ratified", "status:implementation-authorized"):
        "the implementation authorization is granted after ratification, so it is the later state",
    ("status:merged", "status:verified"):
        "verification follows the merge, so it is the later state",
    ("status:done", "status:merged"):
        "done is the task lifecycle; merged describes the PR event that completed it",
    ("status:open", "status:needs-response"):
        "open is the lifecycle state; needs-response is a routing qualifier on it",
}

REASONS = {
    "normalize": "row carried several status:* tags; kept the later stage, dropped the rest",
    "unknown_status_tag": "a status tag is outside the precedence ladder — refused, not guessed",
}


def status_tags(tags: Sequence[str]) -> List[str]:
    return sorted(t for t in tags if t.startswith("status:"))


def authoritative(tags: Sequence[str]) -> Optional[str]:
    """The single status to keep, or None when the row is not multi-status or
    the ladder cannot order its tags (the caller refuses)."""
    present = status_tags(tags)
    if len(present) < 2:
        return None
    if any(t not in _RANK for t in present):
        return None
    return min(present, key=lambda t: _RANK[t])


def normalize_tags(tags: Sequence[str], chosen: str) -> List[str]:
    kept = [t for t in tags if not t.startswith("status:")]
    return kept + [chosen]


@dataclass
class Row:
    id: str
    role: str
    title: str
    content: str
    tags: List[str]
    metadata: Dict[str, Any]

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(
            {"tags": sorted(self.tags), "metadata": self.metadata},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class Plan:
    row: Row
    chosen: Optional[str]
    dropped: List[str] = field(default_factory=list)
    reason: str = "normalize"

    @property
    def actionable(self) -> bool:
        return self.reason == "normalize" and self.chosen is not None and bool(self.dropped)


def classify(row: Row) -> Optional[Plan]:
    present = status_tags(row.tags)
    if len(present) < 2:
        return None
    chosen = authoritative(row.tags)
    if chosen is None:
        return Plan(row=row, chosen=None, dropped=present, reason="unknown_status_tag")
    dropped = [t for t in present if t != chosen]
    return Plan(row=row, chosen=chosen, dropped=dropped, reason="normalize")


def build_plans(rows: Sequence[Row]) -> List[Plan]:
    plans = [classify(r) for r in rows]
    return [p for p in plans if p is not None]


def receipt(plan: Plan, at_iso: str) -> Dict[str, Any]:
    key = (plan.dropped[0], plan.chosen) if len(plan.dropped) == 1 else tuple(sorted([plan.chosen, *plan.dropped]))
    reason = PAIR_REASONS.get(key) or PAIR_REASONS.get((key[1], key[0])) or \
        "later stage of the lifecycle wins (precedence ladder)"
    return {
        "at": at_iso,
        "by": "engineer",
        "performed_by": "lifecycle_status_normalize",
        "chosen": plan.chosen,
        "dropped": plan.dropped,
        "reason": reason,
        "convention": CONVENTION,
    }


def new_metadata(plan: Plan, at_iso: str) -> Dict[str, Any]:
    meta = dict(plan.row.metadata or {})
    meta["record.status_normalized"] = receipt(plan, at_iso)
    return meta


def summarize(plans: Sequence[Plan]) -> Dict[str, Any]:
    actionable = [p for p in plans if p.actionable]
    refused = [p for p in plans if not p.actionable]
    by_pair: Dict[str, int] = {}
    for p in plans:
        key = f"{'+'.join(p.dropped)} -> {p.chosen or 'refused'}"
        by_pair[key] = by_pair.get(key, 0) + 1
    return {
        "multi_status_rows": len(plans),
        "rows_to_write": len(actionable),
        "rows_refused": len(refused),
        "by_pair": dict(sorted(by_pair.items())),
    }


def report_tsv(plans: Sequence[Plan]) -> str:
    lines = ["record_id\trole\ttags_before\tchosen\tdropped\tverdict\treason"]
    for p in plans:
        lines.append("\t".join([
            p.row.id, p.row.role, ",".join(status_tags(p.row.tags)),
            p.chosen or "", ",".join(p.dropped),
            "write" if p.actionable else "refuse", p.reason,
        ]))
    return "\n".join(lines) + "\n"


# ── I/O ──────────────────────────────────────────────────────────────────

def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg2 is required to read the corpus") from exc
    return psycopg2.connect(dsn)


def load_rows(dsn: str = DSN) -> List[Row]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id::text, coalesce(role,''), coalesce(title,''), coalesce(content,''),
                   coalesce(tags, '{}'::text[]), coalesce(metadata, '{}'::jsonb)
              from nebula.agent_records
             where (select count(*) from unnest(tags) t where t like 'status:%') > 1
             order by created_at asc
            """
        )
        return [Row(r[0], r[1], r[2], r[3], list(r[4] or []), dict(r[5] or {}))
                for r in cur.fetchall()]


RETRY_STATUSES = (429, 502, 503, 504)
MAX_ATTEMPTS = 5


def _http(method: str, url: str, body: Optional[dict], timeout: int = 30) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            payload: Any = {}
            try:
                payload = json.loads(exc.read().decode() or "{}")
            except Exception:
                pass
            if exc.code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                import time
                time.sleep(min(float((exc.headers or {}).get("Retry-After", 2) or 2), 30))
                continue
            return exc.code, payload
    return 0, {"error": "retries exhausted"}


def fetch_point(base: str, rid: str) -> dict:
    status, body = _http("GET", f"{base}/api/agent-records/{rid}", None)
    if status != 200:
        raise RuntimeError(f"point read failed: HTTP {status}")
    return body


def patch_row(base: str, rid: str, patch: dict) -> Tuple[int, Any]:
    return _http("PATCH", f"{base}/api/agent-records/{rid}", patch)


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def apply_plans(plans: Sequence[Plan], base: str, at_iso: str,
                max_apply: int = DEFAULT_MAX_APPLY,
                fetch: Callable[[str, str], dict] = fetch_point,
                write: Callable[[str, str, dict], Tuple[int, Any]] = patch_row,
                log: Callable[[str], None] = lambda _m: None) -> Dict[str, Any]:
    to_write = [p for p in plans if p.actionable]
    if len(to_write) > max_apply:
        raise RuntimeError(f"{len(to_write)} rows exceed --max-apply {max_apply} "
                           f"(nothing was written)")

    verified: List[str] = []
    failed: List[Dict[str, str]] = []
    for plan in to_write:
        rid = plan.row.id
        try:
            fresh = fetch(base, rid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": rid, "error": f"pre-write read failed: {exc}"})
            continue
        current = Row(rid, fresh.get("role", ""), fresh.get("title", ""),
                      fresh.get("content", ""), list(fresh.get("tags") or []),
                      dict(fresh.get("metadata") or {}))
        if current.digest() != plan.row.digest():
            failed.append({"id": rid, "error": "stale: row changed since scan"})
            continue

        tags_after = normalize_tags(plan.row.tags, plan.chosen)
        metadata_after = new_metadata(plan, at_iso)
        status, body = write(base, rid, {"tags": tags_after, "metadata": metadata_after})
        if status != 200:
            failed.append({"id": rid, "error": f"PATCH HTTP {status}: {str(body)[:160]}"})
            continue
        try:
            after = fetch(base, rid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": rid, "error": f"read-back failed: {exc}"})
            continue

        back_tags = list(after.get("tags") or [])
        problems: List[str] = []
        if status_tags(back_tags) != [plan.chosen]:
            problems.append(f"status tags are {status_tags(back_tags)}, expected [{plan.chosen}]")
        if sorted(t for t in back_tags if not t.startswith("status:")) != \
                sorted(t for t in plan.row.tags if not t.startswith("status:")):
            problems.append("non-status tags changed")
        if "record.status_normalized" not in (after.get("metadata") or {}):
            problems.append("receipt missing")
        if _sha(after.get("content", "")) != _sha(plan.row.content):
            problems.append("body changed")
        if problems:
            failed.append({"id": rid, "error": "; ".join(problems)})
            continue
        verified.append(rid)
        log(f"normalized {rid[:8]}: kept {plan.chosen}, dropped {', '.join(plan.dropped)}")

    return {"planned": len(to_write), "verified": verified, "failed": failed}


def post_audit(summary: Dict[str, Any], result: Dict[str, Any]) -> None:
    lines = [
        "## lifecycle status normalization (ranking item 7)",
        "",
        f"- multi-status rows: **{summary['multi_status_rows']}**",
        f"- rows written: **{len(result.get('verified', []))}**, failures: **{len(result.get('failed', []))}**",
        f"- rows refused: **{summary['rows_refused']}**",
        "",
        "### Resolutions",
    ]
    for pair, count in summary["by_pair"].items():
        lines.append(f"- `{pair}` — {count}")
    subprocess.run([str(BIN_DIR / "post-change-log.sh"),
                    "--title", f"lifecycle status: {len(result.get('verified', []))} multi-status "
                               f"rows normalized to one status, {summary['rows_refused']} refused",
                    "--body", "\n".join(lines)], check=False)


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows) -> int:
    ap = argparse.ArgumentParser(description="Collapse multi-status records to one lifecycle status.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", metavar="PATH")
    ap.add_argument("--max-apply", type=int, default=DEFAULT_MAX_APPLY)
    ap.add_argument("--no-changelog", action="store_true")
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        rows = load(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    plans = build_plans(rows)
    summary = summarize(plans)
    if args.report:
        Path(args.report).write_text(report_tsv(plans), encoding="utf-8")
        summary["report"] = args.report

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("lifecycle status normalize — " + ("APPLY" if args.apply else "DRY RUN (no writes)"))
        print(f"multi-status rows: {summary['multi_status_rows']}  to write: "
              f"{summary['rows_to_write']}  refused: {summary['rows_refused']}")
        for pair, count in summary["by_pair"].items():
            print(f"  {pair:<60} {count}")

    if not args.apply:
        return 0

    from datetime import datetime, timezone
    at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = apply_plans(plans, args.nebula_url, at_iso, args.max_apply,
                         log=lambda m: print("  " + m))
    print(f"applied: {len(result['verified'])} verified, {len(result['failed'])} failed")
    for f in result["failed"]:
        print(f"  FAILED {f['id'][:8]}: {f['error']}", file=sys.stderr)
    if not args.no_changelog:
        post_audit(summary, result)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
