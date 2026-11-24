#!/usr/bin/env python3
"""gate_name_namespace.py — carry gate NAMES in `gate-name:<slug>`, keep `gate:` for PRs.

Ranking item 9 (architect ruling 0633f18d). `gate:` has two documented
meanings: a NUMBER is the PR a row is about (item 8, ruling 086cc2d1), and a
NAME (`gate:stage-2`, `gate:tester`, `gate:G0`) says which gate. Readers that
parse `gate:` as a PR number cannot use the name, and a name can never be
looked up as a gate name. The ruling settles the vocabulary:

  gate:<N>            canonical PR reference (unchanged; every numeric gate
                      carries pr:<N>, enforced by gate_pr_coherence_repair.py)
  gate:<name>         LEGACY named-gate syntax — legal, NEVER removed
  gate-name:<slug>    canonical carrier of a gate name; slug = lower(legacy value)

This tool adds the canonical carrier and nothing else. Every row whose
`gate:` value is not numeric gains `gate-name:<slug>` for each such value,
keeps its `gate:<name>` tag, keeps its body and every other tag, and gets one
canonical receipt under `metadata['record.gate_name_namespaced']`. Reversing a
row means dropping the added `gate-name:` tag and that receipt key.

Measured on merged main 90030074f: 12 non-numeric `gate:` values across 11
rows (`stage-2` ×3, `attestation` ×2, `tester`, `lease`, `admission`,
`compilation`, `cutover`, `G0`, `830-scope`, `passed`); one row (7e2c0f7c)
carries three names, and four rows also carry a real `pr:1006` — those keep
the PR and gain the name, so the row states both facts without conflating
them. `gate:830-scope` is a NAME that happens to start with digits; it gets
`gate-name:830-scope` and never a fabricated `pr:830`.

Rows are held, not rewritten, when the row is withdrawn (retired/superseded)
or would gain a slug it already has in a conflicting case (e.g. both
`gate:G0` and `gate-name:G0`), which would need a case ruling.

Dry run is the default. `--apply` is bounded (`--max-apply`, default 25) and
refuses when the plan exceeds the bound; it re-reads every row before writing,
refuses a row that changed since the scan, and after each PATCH reads the row
back and verifies (1) every expected `gate-name:<slug>` is present, (2) the
`gate:` tags are unchanged, (3) the tag set is unchanged apart from the added
`gate-name:` tags, (4) the body sha256 is unchanged, (5) the receipt is
present. A rerun after a successful apply plans zero repairs.

Usage:
  gate_name_namespace.py                  # dry run + summary
  gate_name_namespace.py --report PATH    # + per-row TSV
  gate_name_namespace.py --apply
  gate_name_namespace.py --json

Exit codes: 0 ok (dry run, or every write verified); 1 a write failed
verification; 2 environment error (nothing written).
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

BIN_DIR = Path(__file__).resolve().parent
DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_MAX_APPLY = 25
RECEIPT_KEY = "record.gate_name_namespaced"
NAME_PREFIX = "gate-name:"
CONVENTION = "ranking item 9 ruling 0633f18d"

WITHDRAWN_STATUS = ("status:retired", "status:superseded")
NUMERIC = re.compile(r"^\d+$")

HOLD_REASONS = {
    "withdrawn": "retired/superseded rows are history and are never rewritten",
    "slug_case_conflict": "a gate-name tag already exists for this slug in a different case; "
                          "choosing one spelling is a vocabulary decision this tool does not make",
}
REPAIR_REASON = ("`gate:<name>` is legacy named-gate syntax; the canonical carrier is "
                 "`gate-name:<slug>` (slug = lower(legacy value)), added additively")


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


def gate_values(tags: Sequence[str]) -> List[str]:
    return [t[5:] for t in tags if t.startswith("gate:")]


def gate_name_slugs(tags: Sequence[str]) -> List[str]:
    return [t[len(NAME_PREFIX):] for t in tags if t.startswith(NAME_PREFIX)]


@dataclass
class Plan:
    row: Row
    gates: List[str]                      # every non-numeric gate:<name> value
    action: str                           # "repair" | "hold" | "coherent"
    reason: str = ""
    add_slugs: List[str] = field(default_factory=list)

    @property
    def fixable(self) -> bool:
        return self.action == "repair" and bool(self.add_slugs)


def slugs_for(gates: Sequence[str]) -> List[str]:
    return [g.lower() for g in gates]


def classify(row: Row) -> Optional[Plan]:
    """Plan for a row carrying a non-numeric `gate:<name>`, else None."""
    named = [g for g in gate_values(row.tags) if not NUMERIC.match(g)]
    if not named:
        return None
    existing = gate_name_slugs(row.tags)
    wanted = slugs_for(named)
    missing = [s for s in wanted if s not in existing]
    withdrawn = any(t in WITHDRAWN_STATUS for t in row.tags)

    if not missing:
        return Plan(row, named, "coherent")
    if withdrawn:
        return Plan(row, named, "hold", "withdrawn")
    for other in existing:
        if any(other != slug and other.lower() == slug for slug in missing):
            # e.g. gate-name:G0 already present while the canonical slug is g0:
            # collapsing the case would be a vocabulary decision, not a repair.
            return Plan(row, named, "hold", "slug_case_conflict")
    return Plan(row, named, "repair", REPAIR_REASON, add_slugs=missing)


def build_plans(rows: Sequence[Row]) -> List[Plan]:
    out = [classify(r) for r in rows]
    return [p for p in out if p is not None]


def receipt(plan: Plan, at_iso: str) -> Dict[str, Any]:
    return {
        "at": at_iso,
        "by": "engineer",
        "performed_by": "gate_name_namespace",
        "action": "added-gate-name",
        "gates": list(plan.gates),
        "gate_names": [NAME_PREFIX + s for s in plan.add_slugs],
        "reason": plan.reason,
        "convention": CONVENTION,
    }


def tags_after(plan: Plan) -> List[str]:
    return list(plan.row.tags) + [NAME_PREFIX + s for s in plan.add_slugs]


def metadata_after(plan: Plan, at_iso: str) -> Dict[str, Any]:
    meta = dict(plan.row.metadata or {})
    meta[RECEIPT_KEY] = receipt(plan, at_iso)
    return meta


def summarize(plans: Sequence[Plan]) -> Dict[str, Any]:
    buckets: Dict[str, List[Plan]] = {}
    for p in plans:
        key = "repair" if p.action == "repair" else (p.reason if p.action == "hold" else "coherent")
        buckets.setdefault(key, []).append(p)
    return {
        "named_gate_rows": len(plans),
        "rows_to_repair": len(buckets.get("repair", [])),
        "names_to_add": sum(len(p.add_slugs) for p in buckets.get("repair", [])),
        "rows_held": sum(len(v) for k, v in buckets.items() if k not in ("repair", "coherent")),
        "rows_coherent": len(buckets.get("coherent", [])),
        "by_reason": {k: len(v) for k, v in sorted(buckets.items())},
    }


def report_tsv(plans: Sequence[Plan]) -> str:
    lines = ["record_id\trole\tgates\tgate_names\taction\treason"]
    for p in plans:
        lines.append("\t".join([
            p.row.id, p.row.role, ",".join(p.gates),
            ",".join(p.add_slugs) or ",".join(gate_name_slugs(p.row.tags)),
            "repair" if p.action == "repair" else p.action,
            p.reason if p.action != "coherent" else "",
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
             where exists (select 1 from unnest(tags) t where t like 'gate:%')
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
    to_write = [p for p in plans if p.fixable]
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
        present = set(gate_name_slugs(current.tags))
        if any(s in present for s in plan.add_slugs):
            failed.append({"id": rid, "error": "stale: gate-name tag appeared since scan"})
            continue

        status, body = write(base, rid, {"tags": tags_after(plan),
                                         "metadata": metadata_after(plan, at_iso)})
        if status != 200:
            failed.append({"id": rid, "error": f"PATCH HTTP {status}: {str(body)[:160]}"})
            continue
        try:
            after = fetch(base, rid)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": rid, "error": f"read-back failed: {exc}"})
            continue

        back = list(after.get("tags") or [])
        problems: List[str] = []
        slugs = set(gate_name_slugs(back))
        for s in plan.add_slugs:
            if s not in slugs:
                problems.append(f"gate-name:{s} missing after write (have {sorted(slugs)})")
        if gate_values(back) != gate_values(plan.row.tags):
            problems.append(f"gate tags are {gate_values(back)}, "
                            f"expected {gate_values(plan.row.tags)}")
        if sorted(t for t in back if not t.startswith(NAME_PREFIX)) != \
                sorted(t for t in plan.row.tags if not t.startswith(NAME_PREFIX)):
            problems.append("tags other than gate-name changed")
        if RECEIPT_KEY not in (after.get("metadata") or {}):
            problems.append("receipt missing")
        if _sha(after.get("content", "")) != _sha(plan.row.content):
            problems.append("body changed")
        if problems:
            failed.append({"id": rid, "error": "; ".join(problems)})
            continue
        verified.append(rid)
        log(f"namespaced {rid[:8]}: added "
            f"{', '.join(NAME_PREFIX + s for s in plan.add_slugs)} "
            f"(kept {', '.join('gate:' + g for g in plan.gates)})")

    return {"planned": len(to_write), "verified": verified, "failed": failed}


def post_audit(summary: Dict[str, Any], result: Dict[str, Any],
               run: Callable[..., Any] = subprocess.run) -> Tuple[bool, str]:
    lines = [
        "## gate-name namespace (ranking item 9, ruling 0633f18d)",
        "",
        f"- rows carrying a named `gate:`: **{summary['named_gate_rows']}**",
        f"- rows repaired: **{len(result.get('verified', []))}**, gate-name tags added: "
        f"**{summary['names_to_add']}**",
        f"- failures: **{len(result.get('failed', []))}**, held: **{summary['rows_held']}**, "
        f"already namespaced: **{summary['rows_coherent']}**",
        "",
        "### Outcomes",
    ]
    for reason, count in summary["by_reason"].items():
        lines.append(f"- `{reason}` — {count}")
    try:
        proc = run([str(BIN_DIR / "post-change-log.sh"), "--role", "engineer",
                    "--title", f"gate-name namespace: {len(result.get('verified', []))} rows "
                               f"gained gate-name:<slug>, {summary['rows_held']} held",
                    "--body", "\n".join(lines)],
                   check=False, capture_output=True, text=True, cwd=str(BIN_DIR.parent))
    except OSError as exc:
        return False, f"cannot run post-change-log.sh: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400] or f"exit {proc.returncode}"
    return True, (proc.stdout or "").strip()[:200]


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows) -> int:
    ap = argparse.ArgumentParser(description="Add gate-name:<slug> to rows carrying a named gate: tag.")
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
        print("gate-name namespace — " + ("APPLY" if args.apply else "DRY RUN (no writes)"))
        print(f"named-gate rows: {summary['named_gate_rows']}  to repair: "
              f"{summary['rows_to_repair']}  names: {summary['names_to_add']}  "
              f"held: {summary['rows_held']}  coherent: {summary['rows_coherent']}")
        for reason, count in summary["by_reason"].items():
            print(f"  {reason:<22} {count}")

    if not args.apply:
        return 0

    at_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = apply_plans(plans, args.nebula_url, at_iso, args.max_apply,
                         log=lambda m: print("  " + m))
    print(f"applied: {len(result['verified'])} verified, {len(result['failed'])} failed")
    for f in result["failed"]:
        print(f"  FAILED {f['id'][:8]}: {f['error']}", file=sys.stderr)
    if not args.no_changelog:
        ok, detail = post_audit(summary, result)
        if ok:
            print(f"change-log audit posted ({detail})")
        else:
            print(f"WARNING: change-log audit FAILED: {detail}", file=sys.stderr)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
