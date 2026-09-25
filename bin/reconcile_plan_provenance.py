#!/usr/bin/env python3
"""Provenance-based reconcile report for the Conduit pending population.

Implements the classification the architect's 2026-09-24 18:37 analysis
(record f9390b52) calls for: "classify and reconcile rows by provenance
(completed wrapper, active execution, docketed decomposition, orphan, or
unverified), then repair the Conduit/Nebula status projection; do not
bulk-close pending rows merely because tickets expired."

What it does (READ-ONLY — no rows are mutated):

  1. Reproduces the 22-plan population the analysis refers to: live
     pending plans ∪ plans that left `pending` inside the snapshot window
     (the 8261652 archive, the nine 09-22 18:20 bulk-stamp completions).
  2. Loads live signals per plan from the canonical stores (nebula plan
     row, vision.tickets surface, resolution.receipt chain, plan_ref
     evidence mass).
  3. Classifies each plan into the five provenance classes with a
     documented precedence, using the architect's correlation evidence
     (bin/provenance-specs/fleet-22.json) for what live stores cannot
     show (docket membership, To Do closeout comments).
  4. Reconciles: flags live/declared contradictions, reports class
     counts, and leaves purge decisions empty — purge is the architect's
     call (directive 433b06e5 step 6: report exact plan IDs, architect
     reviews before any purge).

Classes (precedence order):
  completed-wrapper  work concluded outside the row's own Conduit flow
                     (bulk nebula-stamp, evidenced closeout, or
                     superseded-by-children); a completed-wrapper plan
                     still sitting live-pending IS the class's definition
                     (stale side), not a discrepancy
  active             genuine in-flight execution (non-terminal receipt,
                     declared stage blocker, or live rework disposition)
  docketed           lifecycle governed by a decomposition docket
                     (parent docket or awaiting-re-emit child)
  orphan             no provenance: probe/dead-ticket row with no docket
                     and no evidence mass
  unverified         correlation pass found no establishing evidence
                     either way — left unverified, not assumed complete

Exit codes: 0 = all plans reconcile with their declared class;
1 = at least one live/declared discrepancy; 2 = tool error.

DSN override: SRCDSN. Spec: bin/provenance-specs/fleet-22.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DSN = os.environ.get(
    "SRCDSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

# Snapshot window: the architect's 18:37 analysis refers to a pending
# population that included rows which left `pending` afterwards.
SNAPSHOT_WINDOW_START = "2026-09-22 18:20"  # the bulk-completion stamp
SNAPSHOT_WINDOW_END = "2026-09-24 23:59"    # covers the D-2 archive

SPEC = Path(__file__).resolve().parent / "provenance-specs" / "fleet-22.json"

CLASSES = ("completed-wrapper", "active", "docketed", "orphan", "unverified")

NON_TERMINAL_RECEIPTS = {
    "plan_create", "implementation", "review_reject", "critique",
    "critique_pass", "critique_reject", "block", "plan_block",
}


def _psql(sql: str) -> str:
    ran = subprocess.run(["psql", DSN, "-Atc", sql],
                         capture_output=True, text=True, timeout=30)
    if ran.returncode != 0:
        raise RuntimeError(f"psql failed: {ran.stderr.strip()[-200:]}")
    return (ran.stdout or "").strip()


def load_population() -> dict:
    """The 22-plan snapshot population: live pending ∪ rows that left
    pending within the snapshot window (archive + bulk completions)."""
    rows = _psql(
        "select plan_number, status, to_char(updated_at,"
        "'YYYY-MM-DD HH24:MI') from nebula.implementation_plans "
        f"where status='pending' or (status <> 'pending' and "
        f"updated_at >= '{SNAPSHOT_WINDOW_START}:00'::timestamp and "
        f"updated_at <= '{SNAPSHOT_WINDOW_END}:59'::timestamp) "
        "order by plan_number;")
    pop = {}
    for line in rows.splitlines():
        if not line.strip():
            continue
        plan, status, updated = line.split("|", 2)
        pop[plan] = {"status": status, "updated_at": updated}
    return pop


def load_live_signals(plan_ids: list[str]) -> dict:
    """Canonical signals per plan: latest receipt kind, ticket surface
    (last row per role), plan_ref evidence mass."""
    sig = {p: {"latest_receipt": None, "tickets": {},
               "evidence_mass": 0} for p in plan_ids}
    patterns = ", ".join("'" + p + "%'" for p in plan_ids)
    for line in _psql(
            "select payload->>'plan_id', kind from resolution.receipt "
            f"where payload->>'plan_id' like any(array[{patterns}]) "
            "order by created_at;").splitlines():
        if "|" not in line:
            continue
        plan, kind = line.split("|", 1)
        for p in plan_ids:
            if plan.startswith(p):
                sig[p]["latest_receipt"] = kind
    for line in _psql(
            "select plan_id||'|'||role||'|'||status from vision.tickets "
            f"where plan_id like any(array[{patterns}]) "
            "order by created_at;").splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        plan, role, status = parts
        for p in plan_ids:
            if plan.startswith(p):
                sig[p]["tickets"][role] = status
    for line in _psql(
            "select plan_ref, count(*) from nebula.agent_records "
            "where plan_ref is not null group by plan_ref;").splitlines():
        ref, cnt = line.split("|", 1)
        for p in plan_ids:
            if ref.startswith(p):
                sig[p]["evidence_mass"] = int(cnt)
    return sig


def classify(status: str, latest_receipt: str | None,
             declared: dict) -> tuple[str, list[str]]:
    """Pure five-class classification. Precedence: completed-wrapper >
    active > docketed > orphan > unverified. A completed-wrapper plan
    still live-pending is the class's definition (stale side), not an
    inconsistency."""
    reasons: list[str] = []

    # Live-store facts outrank declarations when they are conclusive.
    if status == "completed":
        reasons.append("nebula status=completed (09-22 18:20 bulk stamp)")
        return "completed-wrapper", reasons
    if status == "archived" and declared.get("class") == "completed-wrapper":
        reasons.append("archived per planner D-2 (superseded by children)")
        return "completed-wrapper", reasons

    declared_class = declared.get("class")
    if declared_class == "completed-wrapper":
        reasons.append(declared.get("evidence", ""))
        return "completed-wrapper", reasons
    if declared_class == "active":
        if latest_receipt in NON_TERMINAL_RECEIPTS or latest_receipt is None:
            reasons.append(
                f"non-terminal flow (latest receipt: "
                f"{latest_receipt or 'none'})")
        reasons.append(declared.get("evidence", ""))
        return "active", reasons
    if declared_class == "docketed":
        reasons.append(declared.get("evidence", ""))
        return "docketed", reasons
    if declared_class == "orphan":
        reasons.append(declared.get("evidence", ""))
        return "orphan", reasons
    return "unverified", ["correlation pass found no establishing evidence"]


def verify(status: str, declared: dict, sig: dict) -> list[str]:
    """Live checks that must hold for the declared class. Returns
    discrepancy strings (empty = verified)."""
    bad: list[str] = []
    dc = declared.get("class")

    if dc == "completed-wrapper":
        # Still-pending is the class's definition; only these contradict:
        if status == "completed" and sig["latest_receipt"] not in (
                None, "review_pass"):
            bad.append(f"nebula-completed but latest receipt is "
                       f"{sig['latest_receipt']} — stamp lacks terminal "
                       f"receipt")
        if status == "archived" and not declared.get("archived_disposition"):
            bad.append("archived live but spec lacks archived_disposition")
    elif dc == "active":
        if status != "pending":
            bad.append(f"expected pending, live is {status}")
        if sig["latest_receipt"] in ("review_pass",):
            bad.append("terminal receipt exists but class is active")
    elif dc == "docketed":
        if status != "pending":
            bad.append(f"expected pending, live is {status}")
    elif dc == "orphan":
        if sig["tickets"].get("builder") not in (None, "completed",
                                                 "expired"):
            bad.append("orphan claim but builder ticket is live-open")
    elif dc in (None, "unverified"):
        # Missing declaration is treated as unverified for the evidence
        # check — an undeclared plan with real evidence mass must surface.
        if sig["evidence_mass"] >= 5:
            bad.append(f"unverified but evidence mass is "
                       f"{sig['evidence_mass']} — re-classify")
    return bad


def dc_of(declared: dict) -> str:
    return declared.get("class", "(unlisted)")


def reconcile() -> dict:
    spec = json.loads(SPEC.read_text())
    pop = load_population()
    extra = sorted(set(pop) - set(spec["plans"]))
    missing = sorted(set(spec["plans"]) - set(pop))
    sig = load_live_signals(sorted(pop))

    classes: dict[str, list[dict]] = {c: [] for c in CLASSES}
    discrepancies: list[dict] = []
    for plan, row in sorted(pop.items()):
        declared = spec["plans"].get(plan, {})
        cls, reasons = classify(row["status"], sig[plan]["latest_receipt"],
                                declared)
        bad = verify(row["status"], declared, sig[plan])
        if plan in extra:
            bad = bad + ["live population member absent from spec"]
        if bad:
            discrepancies.append({"plan": plan,
                                  "declared": dc_of(declared),
                                  "classified": cls, "issues": bad})
        classes[cls].append({
            "plan": plan,
            "status": row["status"],
            "updated_at": row["updated_at"],
            "latest_receipt": sig[plan]["latest_receipt"],
            "tickets": sig[plan]["tickets"],
            "evidence_mass": sig[plan]["evidence_mass"],
            "declared_class": dc_of(declared),
            "reasons": reasons,
            "note": declared.get("note", ""),
        })

    return {
        "population": {
            "size": len(pop),
            "live_pending": sum(1 for r in pop.values()
                                if r["status"] == "pending"),
            "left_pending_in_window": {
                "archived": [p for p, r in pop.items()
                             if r["status"] == "archived"],
                "bulk_stamped_completed": [p for p, r in pop.items()
                                           if r["status"] == "completed"],
            },
            "spec_only": missing,
            "live_only": extra,
        },
        "classes": {c: classes[c] for c in CLASSES},
        "discrepancies": discrepancies,
        "purge_ready": [],  # architect's call per directive 433b06e5 step 6
    }


def main() -> int:
    try:
        report = reconcile()
    except Exception as exc:  # noqa: BLE001
        print(f"tool error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    print("\n== provenance matrix ==", file=sys.stderr)
    for cls in CLASSES:
        rows = report["classes"][cls]
        print(f"{cls:>18}: {len(rows):>2}  "
              + ", ".join(r["plan"] for r in rows), file=sys.stderr)
    pop = report["population"]
    bulk = pop["left_pending_in_window"]["bulk_stamped_completed"]
    print(f"\npopulation: {pop['size']} "
          f"(live pending {pop['live_pending']}, "
          f"bulk-stamped {len(bulk)}, "
          f"archived {len(pop['left_pending_in_window']['archived'])})",
          file=sys.stderr)
    return 1 if report["discrepancies"] else 0


if __name__ == "__main__":
    sys.exit(main())
