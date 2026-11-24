#!/usr/bin/env python3
"""gate_pr_coherence_repair.py — make every `gate:N` reference resolvable to a PR.

Ranking item 8 (architect ruling 086cc2d1). `gate:<N>` is the gate's PR number:
`bin/merge_pr.py`, `GET /api/attestations?pr=N` and
`bin/attestation_residue_sweep.py` all read `pr:<N>` and `gate:<N>` as two
spellings of the same PR. A row that carries `gate:<N>` and no `pr:<N>` is
therefore reachable by gate number only — a PR-keyed lookup cannot see it —
and a row whose two tags name different PRs cannot be attributed at all.

Measured on merged main 5927e9598 (17,515 records): 76 rows carry `gate:`;
14 carry a numeric gate and no `pr:` (13 of them name that PR in the title),
11 use `gate:` for a NAMED gate (`gate:stage-2`, `gate:tester`, `gate:G0`, …),
and 0 carry a numeric mismatch. The 11 are a namespace collision, not a typo:
`gate:` is simultaneously "the PR number" and "which gate". They are reported
and never guessed at — choosing a new spelling for a named gate is a
vocabulary decision this tool does not own.

## The repair (additive, reversible)

Only rows with a NUMERIC `gate:<N>`, no `pr:` tag, and no contradicting PR
mention in the title get `pr:<N>` added. Nothing is removed or rewritten: the
body, every other tag and the `gate:` tag stay exactly as they are, and the
row gains one canonical receipt under `metadata['record.gate_pr_repaired']`
naming the gate, the PR, the action and the ruling. Undoing a repair means
dropping that one tag and that one receipt key.

Rows are held, not guessed at, when:
  - `gate_pr_mismatch`    — `gate:<N>` and `pr:<M>` disagree;
  - `gate_not_a_pr`       — every gate value on the row is a name (`gate:stage-2`); those rows
                            belong to `gate-name:<slug>` (bin/gate_name_namespace.py);
  - `title_contradicts_gate` — the title names a different PR and not this one;
  - `withdrawn`           — retired/superseded rows are history, left alone.

## Safety

Dry run is the default. `--apply` is bounded (`--max-apply`, default 25) and
refuses to write anything when the plan exceeds the bound; it re-reads every
row before writing and refuses a row that changed since the scan; after each
PATCH it reads the point record back and verifies (1) `pr:<N>` is present,
(2) `gate:<N>` is still present, (3) the non-`pr:` tag set is unchanged,
(4) the body sha256 is unchanged, (5) the receipt is present. A failed check is
reported, never claimed. A rerun after a successful apply plans zero repairs.

Usage:
  gate_pr_coherence_repair.py                 # dry run + summary
  gate_pr_coherence_repair.py --report PATH   # + per-row TSV
  gate_pr_coherence_repair.py --apply
  gate_pr_coherence_repair.py --json

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_shape  # noqa: E402  (shared classifier, ruling 4963dee4)

BIN_DIR = Path(__file__).resolve().parent
DSN = os.environ.get("SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")
DEFAULT_NEBULA = os.environ.get("NEBULA_BASE", "http://localhost:3101")
DEFAULT_MAX_APPLY = 25
RECEIPT_KEY = "record.gate_pr_repaired"
CONVENTION = "ranking item 8 ruling 086cc2d1"

WITHDRAWN_STATUS = ("status:retired", "status:superseded")
TITLE_PR = re.compile(r"#(\d{1,5})\b")

HOLD_REASONS = {
    "gate_pr_mismatch": "gate:<N> and pr:<M> name different PRs — attributing the row is a "
                        "judgement the tool refuses to make",
    "gate_not_a_pr": "the gate value is not a number: `gate:` is also used for NAMED gates "
                     "(stage-2, tester, G0), so this row's PR cannot be derived from it",
    "title_contradicts_gate": "the title names a different PR and never the gate number, so the "
                              "gate tag itself is suspect",
    "withdrawn": "retired/superseded rows are history and are never rewritten",
    gate_shape.R_EMPTY: "an empty gate: value has no shape — held, never guessed",
    gate_shape.R_MALFORMED: "a whitespace-bearing gate: value has no shape — held, never guessed",
    gate_shape.R_CONFLICT: "the row also carries gate-name:<value>, claiming the value as both "
                           "PR and name; choosing one is a vocabulary decision, so no pr: is "
                           "fabricated",
}
REPAIR_REASON = "gate:<N> is the gate's PR number, spelled twice by every reader; the row lacked pr:<N>"


def gate_values(tags: Sequence[str]) -> List[str]:
    """EVERY gate:<value> on the row, in tag order (a record may cover several
    PRs: 4f19a49f carries gate:835 gate:836 gate:837)."""
    return gate_shape.gate_values(tags)


def gate_value(tags: Sequence[str]) -> Optional[str]:
    for t in tags:
        if t.startswith("gate:"):
            return t[5:]
    return None


def pr_values(tags: Sequence[str]) -> List[str]:
    return [t[3:] for t in tags if t.startswith("pr:")]


def pr_value(tags: Sequence[str]) -> Optional[str]:
    for t in tags:
        if t.startswith("pr:"):
            return t[3:]
    return None


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
    gate: Optional[str]
    pr: Optional[str]
    action: str            # "repair" | "hold" | "coherent"
    reason: str = ""
    add_prs: List[str] = field(default_factory=list)
    gates: List[str] = field(default_factory=list)

    @property
    def add_pr(self) -> Optional[str]:
        return self.add_prs[0] if self.add_prs else None

    @property
    def fixable(self) -> bool:
        return self.action == "repair" and bool(self.add_prs)


def classify(row: Row) -> Optional[Plan]:
    """Plan for a row that carries a gate: tag, else None (not in scope).

    Coherence is judged PER GATE VALUE, so a record covering several PRs is
fully repaired (`gate:835 gate:836 gate:837` gets every missing `pr:`), and a
row whose `pr:835` exists while `gate:836` has no `pr:836` is still detected.

Coherence is judged PER GATE VALUE: `pr:835` present does not make
    `gate:836` coherent, and a row whose only tags are named gates belongs to
    the gate-name namespace (bin/gate_name_namespace.py), not here.
    """
    gates = gate_values(row.tags)
    if not gates:
        return None
    gate = gates[0]
    shapes = gate_shape.classify_row(row.tags)
    held_shape = next((s for s in shapes if s.is_held), None)
    numeric = [s.value for s in shapes if s.is_pr]
    prs = pr_values(row.tags)
    pr = prs[0] if prs else None
    withdrawn = any(t in WITHDRAWN_STATUS for t in row.tags)

    if held_shape is not None:
        return Plan(row, gate, pr, "hold", held_shape.reason_code, gates=gates)
    if not numeric:
        return Plan(row, gate, pr, "hold", "withdrawn" if withdrawn else "gate_not_a_pr",
                    gates=gates)

    # Compare as NUMBERS: `gate:0830` and `pr:830` name the same PR (ruling
    # 4963dee4 fork 2), so the leading-zero spelling never earns a second tag.
    pr_ints = {int(p) for p in prs if p.isdigit()}
    missing = [g for g in numeric if int(g) not in pr_ints]
    if prs and not missing:
        return Plan(row, gate, pr, "coherent", gates=numeric)
    if prs and missing and not pr_ints & {int(g) for g in numeric}:
        # No PR number the row claims appears in its gate tags: a contradiction.
        return Plan(row, gate, pr, "hold", "gate_pr_mismatch", gates=numeric)

    named = [int(m) for m in TITLE_PR.findall(row.title or "")]
    if named and not set(named) & {int(g) for g in numeric}:
        return Plan(row, gate, pr, "hold", "title_contradicts_gate", gates=numeric)
    if withdrawn:
        return Plan(row, gate, pr, "hold", "withdrawn", gates=numeric)
    return Plan(row, gate, pr, "repair", REPAIR_REASON, add_prs=missing, gates=numeric)


def build_plans(rows: Sequence[Row]) -> List[Plan]:
    out = [classify(r) for r in rows]
    return [p for p in out if p is not None]


def receipt(plan: Plan, at_iso: str) -> Dict[str, Any]:
    return {
        "at": at_iso,
        "by": "engineer",
        "performed_by": "gate_pr_coherence_repair",
        "action": "added-pr-from-gate",
        "pr": plan.add_pr,
        "prs_added": list(plan.add_prs),
        "gates": list(plan.gates),
        "reason": plan.reason,
        "convention": CONVENTION,
    }


def tags_after(plan: Plan) -> List[str]:
    return list(plan.row.tags) + [f"pr:{n}" for n in plan.add_prs]


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
        "gate_rows": len(plans),
        "rows_to_repair": len(buckets.get("repair", [])),
        "rows_held": sum(len(v) for k, v in buckets.items() if k not in ("repair", "coherent")),
        "rows_coherent": len(buckets.get("coherent", [])),
        "by_reason": {k: len(v) for k, v in sorted(buckets.items())},
    }


def report_tsv(plans: Sequence[Plan]) -> str:
    lines = ["record_id\trole\tgate\tpr\taction\treason\ttitle"]
    for p in plans:
        lines.append("\t".join([
            p.row.id, p.row.role, p.gate or "", p.pr or "",
            "repair" if p.action == "repair" else p.action,
            p.reason if p.action != "coherent" else "", (p.row.title or "")[:80],
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
        for n in plan.add_prs:
            if n not in pr_values(back):
                problems.append(f"pr:{n} missing after write (prs are {pr_values(back)})")
        if gate_values(back) != gate_values(plan.row.tags):
            problems.append(f"gate tags are {gate_values(back)}, expected {gate_values(plan.row.tags)}")
        if sorted(t for t in back if not t.startswith("pr:")) != \
                sorted(t for t in plan.row.tags if not t.startswith("pr:")):
            problems.append("non-pr tags changed")
        if RECEIPT_KEY not in (after.get("metadata") or {}):
            problems.append("receipt missing")
        if _sha(after.get("content", "")) != _sha(plan.row.content):
            problems.append("body changed")
        if problems:
            failed.append({"id": rid, "error": "; ".join(problems)})
            continue
        verified.append(rid)
        log(f"repaired {rid[:8]}: added {', '.join('pr:' + n for n in plan.add_prs)} "
            f"(gates {', '.join(plan.gates)} retained)")

    return {"planned": len(to_write), "verified": verified, "failed": failed}


def post_audit(summary: Dict[str, Any], result: Dict[str, Any],
               run: Callable[..., Any] = subprocess.run) -> Tuple[bool, str]:
    lines = [
        "## gate/pr coherence repair (ranking item 8, ruling 086cc2d1)",
        "",
        f"- rows carrying `gate:`: **{summary['gate_rows']}**",
        f"- repaired (added `pr:<N>`): **{len(result.get('verified', []))}**",
        f"- failures: **{len(result.get('failed', []))}**, held: **{summary['rows_held']}**, "
        f"already coherent: **{summary['rows_coherent']}**",
        "",
        "### Outcomes",
    ]
    for reason, count in summary["by_reason"].items():
        lines.append(f"- `{reason}` — {count}")
    try:
        proc = run([str(BIN_DIR / "post-change-log.sh"), "--role", "engineer",
                    "--title", f"gate/pr coherence: {len(result.get('verified', []))} rows gained "
                               f"their PR tag, {summary['rows_held']} held",
                    "--body", "\n".join(lines)],
                   check=False, capture_output=True, text=True, cwd=str(BIN_DIR.parent))
    except OSError as exc:
        return False, f"cannot run post-change-log.sh: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:400] or f"exit {proc.returncode}"
    return True, (proc.stdout or "").strip()[:200]


def main(argv: Optional[Sequence[str]] = None, *, load=load_rows) -> int:
    ap = argparse.ArgumentParser(description="Repair gate/pr tag coherence in agent records.")
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
        print("gate/pr coherence repair — " + ("APPLY" if args.apply else "DRY RUN (no writes)"))
        print(f"gate rows: {summary['gate_rows']}  to repair: {summary['rows_to_repair']}  "
              f"held: {summary['rows_held']}  coherent: {summary['rows_coherent']}")
        for reason, count in summary["by_reason"].items():
            print(f"  {reason:<26} {count}")

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
