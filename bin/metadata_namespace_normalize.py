#!/usr/bin/env python3
"""metadata_namespace_normalize.py — normalize tool-owned agent-record metadata keys.

Ranking item 6. `nebula.agent_records.metadata` is free-form jsonb, and the
corpus shows what that costs: **524 distinct top-level keys across 5,123
records**, with tool-owned state written under bare English words that any
record author may also use.

  * `retired` (269 rows) is a lifecycle object written by the retirement paths
    (attestationPatch / supersede-record.sh / attestation_residue_sweep.py) —
    but "retired" is also a word a record's own metadata can use for anything.
  * `snapshot_purpose` + `source_table`/`source_id`/`fk_column`/`fk_value`
    (4,419 rows) are one block written by the pre-FK evidence migration
    (`sql/V043`), keyed on names like `source_id` that belong to no one.
  * `_migrated_from`/`_dirname`/`_size_bytes` (208) come from the audit
    importer; `hollow_content_repair`/`repaired_from`/`restored_content_chars`
    from the hollow-record repair; `corrections[]`/`residue_closed` from the
    correction convention that the record projections document.

The canonical form is `<owner>.<key>` (architect ruling on item 6): the owner
is a closed set of system components, so a consumer can tell a tool contract
from a record's own prose by looking at the key. This tool MOVES a legacy
tool-owned key to its canonical name — same value, other keys untouched — and
leaves everything else exactly where it is, enumerated with a reason:

  map               fill canonical from legacy (nothing lost, nothing merged)
  duplicate_value   canonical already holds the same value; drop the legacy key
  conflict          canonical already holds a DIFFERENT value — refuse the row
  ambiguous_owner   a generic key whose owning block is absent (e.g. a
                    lone `source_id` with no `snapshot.purpose`) — refuse
  free_form         a key no system component owns (record-authored prose,
                    e.g. `pr`, `branch`, `verdict`) — untouched

Free-form keys are deliberately NOT renamed: assigning them an owner would be
inventing a contract that no producer signed. That is the item-6 remainder, and
`--report` lists every occurrence with its reason.

Writes go through `PATCH /api/agent-records/:id` with the whole metadata object
and are verified by read-back: the canonical key present with the same value,
the legacy key gone, every bystanding key byte-identical, and title/content
unchanged. A stale read (metadata changed since the scan) is refused rather than
clobbered. A rerun after `--apply` plans nothing: only legacy keys are planned.

Usage:
  metadata_namespace_normalize.py                    # dry run + summary
  metadata_namespace_normalize.py --report PATH      # + per-(record,key) TSV
  metadata_namespace_normalize.py --apply --max-apply 6000
  metadata_namespace_normalize.py --json

Exit codes: 0 ok; 1 a write failed verification; 2 tool/environment error
(nothing written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

BIN_DIR = Path(__file__).resolve().parent
DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_MAX_APPLY = 50

# ── the canonical registry ───────────────────────────────────────────────
# owner.key -> legacy key. Owner is a system component, not a role: the key
# says WHO owns the contract, so a consumer never has to guess.
DIRECT: Dict[str, str] = {
    "record.retired": "retired",
    "correction.claims": "corrections",
    "correction.residue_closed": "residue_closed",
    "snapshot.purpose": "snapshot_purpose",
    "migration.origin_path": "_migrated_from",
    "migration.dirname": "_dirname",
    "migration.size_bytes": "_size_bytes",
    "repair.hollow_content": "hollow_content_repair",
    "repair.hollow_content_audit": "hollow_content_audit",
    "repair.repaired_from": "repaired_from",
    "repair.restored_chars": "restored_content_chars",
}

# Generic keys whose owner is only knowable from the block they travel with:
# `source_id` belongs to the snapshot block ONLY on a row that also carries
# `snapshot_purpose`. Without the anchor it could be anyone's, so it is left
# alone (ambiguous_owner) rather than claimed.
CONDITIONAL: Dict[str, Tuple[str, str]] = {
    "snapshot.source_table": ("source_table", "snapshot_purpose"),
    "snapshot.source_id": ("source_id", "snapshot_purpose"),
    "snapshot.fk_column": ("fk_column", "snapshot_purpose"),
    "snapshot.fk_value": ("fk_value", "snapshot_purpose"),
}

LEGACY_TO_CANONICAL: Dict[str, str] = {legacy: canon for canon, legacy in DIRECT.items()}
LEGACY_TO_CANONICAL.update({legacy: canon for canon, (legacy, _anchor) in CONDITIONAL.items()})

CANONICAL_PREFIXES = ("record.", "correction.", "snapshot.", "migration.", "repair.", "harness.")

REASONS = {
    "map": "legacy key moved to its canonical name (value preserved)",
    "duplicate_value": "canonical already holds the same value; legacy key dropped",
    "conflict": "canonical holds a different value — row refused, nothing written",
    "ambiguous_owner": "generic key without its owning block — left untouched",
    "free_form": "no system component owns this key (record-authored prose) — untouched",
}


def is_canonical(key: str) -> bool:
    return key.startswith(CANONICAL_PREFIXES)


@dataclass
class Row:
    id: str
    role: str
    metadata: Dict[str, Any]
    title: str = ""
    content: str = ""

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass
class Item:
    """One (record, key) decision."""

    row: Row
    key: str
    canonical: Optional[str]
    verdict: str          # "plan" | "refuse" | "ignore"
    reason: str
    detail: str = ""


BLOCKING_REASONS = ("conflict", "ambiguous_owner")


@dataclass
class RowPlan:
    row: Row
    items: List[Item] = field(default_factory=list)

    @property
    def planned(self) -> List[Item]:
        return [i for i in self.items if i.verdict == "plan"]

    @property
    def blocked(self) -> List[Item]:
        """Refusals that forbid rewriting the row at all."""
        return [i for i in self.items if i.reason in BLOCKING_REASONS]

    @property
    def residue(self) -> List[Item]:
        """Keys left exactly where they are, for reasons that do not block the
        rest of the row (free-form prose)."""
        return [i for i in self.items if i.verdict == "refuse" and i.reason not in BLOCKING_REASONS]


# ── classification ───────────────────────────────────────────────────────

def classify_row(row: Row) -> RowPlan:
    plan = RowPlan(row)
    meta = row.metadata or {}
    for key in sorted(meta):
        if is_canonical(key):
            continue  # already normalized; not residue
        canonical = LEGACY_TO_CANONICAL.get(key)
        if canonical is None:
            plan.items.append(Item(row, key, None, "refuse", "free_form"))
            continue
        anchor = CONDITIONAL.get(canonical)
        if anchor and anchor[1] not in meta:
            plan.items.append(Item(row, key, canonical, "refuse", "ambiguous_owner",
                                   detail=f"missing anchor {anchor[1]}"))
            continue
        if canonical in meta:
            if meta[canonical] == meta[key]:
                plan.items.append(Item(row, key, canonical, "plan", "duplicate_value"))
            else:
                plan.items.append(Item(row, key, canonical, "refuse", "conflict"))
            continue
        plan.items.append(Item(row, key, canonical, "plan", "map"))
    return plan


def build_plans(rows: Sequence[Row]) -> List[RowPlan]:
    return [classify_row(r) for r in rows]


def normalize_metadata(plan: RowPlan) -> Dict[str, Any]:
    """Pure: the metadata object to write, or the unchanged input if refused."""
    meta = dict(plan.row.metadata or {})
    if plan.blocked:
        return meta
    for item in plan.planned:
        value = meta.pop(item.key, None)
        meta[item.canonical] = value
    return meta


def summarize(plans: Sequence[RowPlan]) -> Dict[str, Any]:
    reasons: Dict[str, int] = {}
    planned_keys = planned_rows = refused_rows = 0
    for plan in plans:
        if plan.planned:
            planned_rows += 1
            planned_keys += len(plan.planned)
        if plan.blocked:
            refused_rows += 1
        for item in plan.items:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
    return {
        "rows_scanned": len(plans),
        "rows_with_legacy_keys": sum(1 for p in plans if p.items),
        "rows_to_write": planned_rows,
        "keys_to_move": planned_keys,
        "rows_refused": refused_rows,
        "occurrences": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "reason_help": {k: REASONS.get(k, "") for k in sorted(reasons)},
    }


def report_tsv(plans: Sequence[RowPlan]) -> str:
    lines = ["record_id\trole\tlegacy_key\tcanonical_key\tverdict\treason\tdetail"]
    for plan in plans:
        for item in plan.items:
            lines.append("\t".join([
                item.row.id, item.row.role or "", item.key, item.canonical or "",
                item.verdict, item.reason, (item.detail or "").replace("\t", " "),
            ]))
    return "\n".join(lines) + "\n"


# ── database read side ───────────────────────────────────────────────────

def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("psycopg2 is required to read the corpus") from exc
    return psycopg2.connect(dsn)


def load_rows(dsn: str = DSN) -> Tuple[List[Row], int]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from nebula.agent_records")
        total = int(cur.fetchone()[0])
        cur.execute(
            """
            select id, coalesce(role,''), coalesce(metadata,'{}'::jsonb),
                   coalesce(title,''), coalesce(content,'')
              from nebula.agent_records
             where metadata <> '{}'::jsonb
             order by created_at asc
            """
        )
        rows = [Row(id=str(r[0]), role=r[1], metadata=dict(r[2] or {}), title=r[3], content=r[4])
                for r in cur.fetchall()]
    return rows, total


# ── write side (nebula API, read-back verified) ──────────────────────────

# The service rate-limits point reads and patches at 300/min per IP. A write
# pass touches ~5k records, so a 429 must back off and retry rather than be
# recorded as a normalization failure.
RETRY_STATUSES = (429, 502, 503, 504)
MAX_ATTEMPTS = 5
PACE_SECONDS = 0.7  # 3 requests per row at <= 270 requests/min

_sleep = time.sleep  # seam for hermetic tests


def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    header = (exc.headers or {}).get("Retry-After") if exc.headers else None
    try:
        wait = float(header) if header is not None else 0.0
    except (TypeError, ValueError):
        wait = 0.0
    if wait <= 0:
        wait = 2.0 * (attempt + 1)
    return min(wait, 60.0)


def _http(method: str, url: str, body: Optional[dict], timeout: int = 30) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode() or "{}")
            except Exception:
                payload = {}
            if exc.code in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                _sleep(_retry_after_seconds(exc, attempt))
                continue
            return exc.code, payload
    return 0, {"error": "retries exhausted"}


def fetch_point(base: str, record_id: str) -> dict:
    status, body = _http("GET", f"{base}/api/agent-records/{record_id}", None)
    if status != 200:
        raise RuntimeError(f"point read failed for {record_id}: HTTP {status} {body}")
    return body


def patch_metadata(base: str, record_id: str, metadata: dict) -> Tuple[int, Any]:
    return _http("PATCH", f"{base}/api/agent-records/{record_id}", {"metadata": metadata})


def _md5(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def apply_plans(
    plans: Sequence[RowPlan],
    base: str,
    max_apply: int = DEFAULT_MAX_APPLY,
    fetch: Callable[[str, str], dict] = fetch_point,
    write: Callable[[str, str, dict], Tuple[int, Any]] = patch_metadata,
    log: Callable[[str], None] = lambda _m: None,
    pace: float = PACE_SECONDS,
    sleep: Callable[[float], None] = None,
) -> Dict[str, Any]:
    """Apply the planned normalizations; verify every write by read-back."""
    to_write = [p for p in plans if p.planned and not p.blocked]
    if len(to_write) > max_apply:
        raise RuntimeError(
            f"{len(to_write)} rows exceed --max-apply {max_apply}: raise the bound "
            f"deliberately (nothing was written)"
        )

    nap = sleep or _sleep
    verified: List[str] = []
    failed: List[Dict[str, str]] = []
    for index, plan in enumerate(to_write):
        if index and pace > 0:
            nap(pace)
        rid = plan.row.id
        target = normalize_metadata(plan)
        try:
            fresh = fetch(base, rid)
        except Exception as exc:
            failed.append({"id": rid, "error": f"pre-write read failed: {exc}"})
            continue
        current = dict(fresh.get("metadata") or {})
        if hashlib.sha256(json.dumps(current, sort_keys=True, separators=(",", ":")).encode()).hexdigest() \
                != plan.row.digest():
            # Someone else touched this row since the scan. Rewriting from the
            # stale copy would clobber their keys — refuse instead.
            failed.append({"id": rid, "error": "stale: metadata changed since scan"})
            continue

        status, body = write(base, rid, target)
        if status not in (200, 201, 204):
            failed.append({"id": rid, "error": f"PATCH HTTP {status}: {str(body)[:200]}"})
            continue
        try:
            after = fetch(base, rid)
        except Exception as exc:
            failed.append({"id": rid, "error": f"read-back failed: {exc}"})
            continue
        back = dict(after.get("metadata") or {})
        problems: List[str] = []
        for item in plan.planned:
            if item.canonical not in back:
                problems.append(f"{item.canonical} missing")
            elif back[item.canonical] != plan.row.metadata.get(item.key) and item.reason == "map":
                problems.append(f"{item.canonical} value changed")
            if item.key in back:
                problems.append(f"{item.key} still present")
        bystanders = {k: v for k, v in plan.row.metadata.items()
                      if k not in {i.key for i in plan.planned}}
        if {k: back.get(k) for k in bystanders} != bystanders:
            problems.append("a bystander key changed")
        if _md5(after.get("content", "")) != _md5(plan.row.content):
            problems.append("body changed")
        if problems:
            failed.append({"id": rid, "error": "; ".join(problems)})
            continue
        verified.append(rid)
        log(f"normalized {rid[:8]} ({len(plan.planned)} key(s))")

    return {"planned": len(to_write), "verified": verified, "failed": failed}


def post_audit(summary: Dict[str, Any], result: Dict[str, Any]) -> None:
    lines = [
        "## agent-record metadata namespace normalization",
        "",
        f"- rows carrying metadata: **{summary['rows_scanned']}** "
        f"({summary['rows_with_legacy_keys']} carry a non-canonical key)",
        f"- rows written: **{summary['rows_to_write']}**, keys moved: **{summary['keys_to_move']}**",
        f"- verified: **{len(result.get('verified', []))}**, failures: **{len(result.get('failed', []))}**",
        "",
        "### Occurrences by reason",
    ]
    for key, count in summary["occurrences"].items():
        lines.append(f"- `{key}` — {count}: {REASONS.get(key, '')}")
    body = "\n".join(lines)
    subprocess.run(
        [str(BIN_DIR / "post-change-log.sh"), "--title",
         f"metadata namespace: {summary['keys_to_move']} keys normalized across "
         f"{len(result.get('verified', []))} records, {summary['rows_refused']} rows refused",
         "--body", body],
        check=False,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Move legacy tool-owned metadata keys to their canonical names.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", metavar="PATH")
    ap.add_argument("--max-apply", type=int, default=DEFAULT_MAX_APPLY)
    ap.add_argument("--no-changelog", action="store_true")
    ap.add_argument("--nebula-url", default=DEFAULT_NEBULA)
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args(argv)

    try:
        rows, total = load_rows(args.dsn)
    except Exception as exc:
        print(f"ERROR: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    plans = build_plans(rows)
    summary = summarize(plans)
    summary["total_records"] = total

    if args.report:
        Path(args.report).write_text(report_tsv(plans), encoding="utf-8")
        summary["report"] = args.report

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("metadata namespace normalize — " + ("APPLY" if args.apply else "DRY RUN (no writes)"))
        print(f"records: {total}  with metadata: {len(rows)}  rows to write: "
              f"{summary['rows_to_write']}  keys to move: {summary['keys_to_move']}  "
              f"rows refused: {summary['rows_refused']}")
        print("\nkey occurrences by reason:")
        for key, count in summary["occurrences"].items():
            print(f"  {key:<18} {count:>6}  {REASONS.get(key, '')}")

    if not args.apply:
        return 0

    result = apply_plans(plans, args.nebula_url, args.max_apply, log=lambda m: print("  " + m))
    print(f"applied: {len(result['verified'])} verified, {len(result['failed'])} failed")
    for f in result["failed"]:
        print(f"  FAILED {f['id'][:8]}: {f['error']}", file=sys.stderr)
    if not args.no_changelog:
        post_audit(summary, result)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
