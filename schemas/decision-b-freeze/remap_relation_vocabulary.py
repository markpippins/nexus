#!/usr/bin/env python3
"""PC6 Track 1 — relation-vocabulary remap for knowledge.graph_edges.

Implements the ratified tail disposition:
  * Re-ratification 9b2ffd4b: remap non-backbone edge relation_type to the
    10-term canonical vocabulary per relation-vocabulary-map.json,
    preserving the original field name in properties.relation_payload.
  * Ruling c0345945 (architect, ratifying ontologist 1a1084d0): Option 1(b)
    for the 4 uq_graph_edges collision groups — remap 112 rows (108 clean +
    1 representative per group), HOLD the 6 non-representative members as
    permanent documented exceptions (original relation_type retained;
    properties.relation_payload AND properties.exception set).

Rules:
  * Backbone terms (describes, evaluated, instantiates, identified_by,
    governed_by, evidences) are FROZEN — never remapped.
  * governed_by / evidences edges (the deny-seeding pair) are FROZEN
    regardless of backbone membership.
  * Original field name -> properties.relation_payload on every touched
    row (remapped AND held) — never dropped.
  * Held exception rows: relation_type unchanged; properties.exception
    documents group, ruling, canonical term, and rationale — all sourced
    from the map artifact's "exceptions" section (the pinned Decision B
    source of truth), never hardcoded here.
  * Fail-closed vs the exception spec: each group's representative and
    each held member must match exactly ONE live in-scope row, and the
    held total must equal the map's held count. Any drift aborts the run
    before any write.
  * Idempotent: rows already carrying relation_payload are skipped
    (re-run = no-op).
  * Dry-run by default; --apply executes in ONE transaction with parity
    evidence printed before commit.

Connection: TACKLE_PG_DSN or default (pguser/pgpass@localhost:5432/nexus).
"""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

REPO_DEFAULT = Path(__file__).resolve().parents[2] / "schemas/decision-b-freeze/relation-vocabulary-map.json"

BACKBONE = {"describes", "evaluated", "instantiates", "identified_by", "governed_by", "evidences"}
FROZEN_TYPES = {"governed_by", "evidences"}  # deny-seeding pair — never remapped

EXCEPTION_KIND = "vocabulary-remap-exception"


def concept_tail(concept_id: str) -> str:
    """graph_edges.source_id tail after the store prefix (resolution:concept:X -> X)."""
    return concept_id.rsplit(":", 1)[-1]


def load_map(path: Path) -> dict:
    m = json.loads(path.read_text())
    mapping = m["mapping"]
    for k, v in mapping.items():
        if v in BACKBONE:
            raise SystemExit(f"refusing: map entry {k!r} -> backbone term {v!r} would violate the backbone freeze")
    exc = m.get("exceptions")
    if not exc or "groups" not in exc:
        raise SystemExit("refusing: map artifact has no 'exceptions' section (ruling c0345945 requires it)")
    held_total = 0
    for g in exc["groups"]:
        missing = [
            k
            for k in ("source_section", "target_section", "source", "canonical", "target", "originals", "held", "representative", "rationale")
            if k not in g
        ]
        if missing:
            raise SystemExit(f"refusing: exception group missing keys {missing}: {g}")
        if set(g["held"]) | {g["representative"]} != set(g["originals"]):
            raise SystemExit(f"refusing: exception group held+representative != originals: {g}")
        if mapping.get(g["representative"]) != g["canonical"]:
            raise SystemExit(f"refusing: representative {g['representative']!r} not mapped to {g['canonical']!r}")
        for t in g["held"]:
            if mapping.get(t) != g["canonical"]:
                raise SystemExit(f"refusing: held member {t!r} not mapped to {g['canonical']!r}")
        held_total += len(g["held"])
    print(f"map loaded: {len(mapping)} entries, {len(exc['groups'])} exception groups, {held_total} held rows expected")
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", type=Path, default=REPO_DEFAULT, help="vocabulary map JSON")
    ap.add_argument("--apply", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--dsn", default=os.environ.get("TACKLE_PG_DSN") or "postgresql://pguser:pgpass@localhost:5432/nexus")
    args = ap.parse_args()

    full_map = load_map(args.map)
    mapping = full_map["mapping"]
    groups = full_map["exceptions"]["groups"]
    expected_held = sum(len(g["held"]) for g in groups)

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # ── BEFORE snapshot (parity evidence) ────────────────────────────
        cur.execute("SELECT relation_type, count(*)::int AS n FROM knowledge.graph_edges GROUP BY relation_type")
        before = {r["relation_type"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT id, source_section, source_id, relation_type,
                   target_section, target_id, properties
            FROM knowledge.graph_edges
            WHERE relation_type NOT IN %s
              AND NOT (properties ? 'relation_payload')
            ORDER BY id
            """,
            (tuple(BACKBONE),),
        )
        rows = cur.fetchall()

        # ── Classify each in-scope row: frozen / held-exception / remap ──
        plan = {}
        skipped_frozen = 0
        held_rows = []
        unmapped = set()
        for r in rows:
            t = r["relation_type"]
            if t in FROZEN_TYPES:
                skipped_frozen += 1
                continue
            if t not in mapping:
                unmapped.add(t)
                continue
            grp = next(
                (
                    g
                    for g in groups
                    if r["source_section"] == g["source_section"]
                    and r["target_section"] == g["target_section"]
                    and concept_tail(r["source_id"]) == g["source"]
                    and concept_tail(r["target_id"]) == g["target"]
                    and t in g["held"]
                ),
                None,
            )
            if grp is not None:
                held_rows.append((r, grp))
            else:
                plan.setdefault(mapping[t], []).append(r)

        if unmapped:
            print(f"UNMAPPED types — ABORT (map must cover 100%): {sorted(unmapped)}")
            return 2

        # ── Already-applied detection (idempotent no-op path) ────────────
        held_types_all = {t for g in groups for t in g["held"]}
        if not plan and not held_rows:
            cur.execute(
                """
                SELECT count(*)::int AS n FROM knowledge.graph_edges
                WHERE relation_type IN %s
                  AND properties ? 'relation_payload'
                  AND properties->'exception'->>'kind' = %s
                """,
                (tuple(held_types_all), EXCEPTION_KIND),
            )
            held_present = cur.fetchone()["n"]
            if held_present == expected_held:
                print("ALREADY APPLIED — nothing in scope, all exception docs present. No-op.")
                conn.rollback()
                return 0
            print(
                f"NOTHING IN SCOPE but only {held_present}/{expected_held} exception docs present — "
                "graph state does not match the ruling; refusing to guess. ABORT."
            )
            return 2

        # ── Fail-closed vs the exception spec ─────────────────────────────
        spec_errors = []
        if len(held_rows) != expected_held:
            spec_errors.append(f"held-row count {len(held_rows)} != map expectation {expected_held}")
        seen_held_keys = set()
        for r, grp in held_rows:
            key = (r["source_section"], r["relation_type"], r["target_section"])
            if key in seen_held_keys:
                spec_errors.append(f"duplicate held member row for {key}")
            seen_held_keys.add(key)
        for g in groups:
            rep_matches = [
                r
                for r in rows
                if r["source_section"] == g["source_section"]
                and r["target_section"] == g["target_section"]
                and concept_tail(r["source_id"]) == g["source"]
                and concept_tail(r["target_id"]) == g["target"]
                and r["relation_type"] == g["representative"]
                and not (r["properties"] or {}).get("relation_payload")
            ]
            if len(rep_matches) != 1:
                spec_errors.append(
                    f"group {g['source']}->{g['target']}: representative {g['representative']!r} matches {len(rep_matches)} in-scope rows (need exactly 1)"
                )
            for t in g["held"]:
                n = sum(
                    1
                    for r in rows
                    if r["source_section"] == g["source_section"]
                    and r["target_section"] == g["target_section"]
                    and concept_tail(r["source_id"]) == g["source"]
                    and concept_tail(r["target_id"]) == g["target"]
                    and r["relation_type"] == t
                )
                if n != 1:
                    spec_errors.append(f"group {g['source']}->{g['target']}: held member {t!r} matches {n} in-scope rows (need exactly 1)")
        if spec_errors:
            print("EXCEPTION-SPEC MISMATCH — ABORT, nothing written:")
            for e in spec_errors:
                print(f"  - {e}")
            return 2

        print(f"edges in scope (non-backbone, no relation_payload): {len(rows)}")
        print(f"  frozen (deny-seeding pair, untouched): {skipped_frozen}")
        print(f"  remap plan: {sum(len(v) for v in plan.values())} rows -> {dict(sorted((k, len(v)) for k, v in plan.items()))}")
        print(f"  held exceptions: {len(held_rows)}")
        for r, grp in held_rows:
            print(f"    HOLD {r['source_section']}.{r['relation_type']} -> {r['target_section']} (id {r['id']}) [{grp['source']}->{grp['target']}]")

        cur.execute("SELECT count(*)::int AS n FROM knowledge.graph_edges WHERE properties ? 'relation_payload'")
        already_payload = cur.fetchone()["n"]
        if already_payload:
            print(f"edges already carrying relation_payload (idempotent skip): {already_payload}")

        planned_remap = {c: len(v) for c, v in plan.items()}

        if not args.apply:
            print("\nDRY-RUN — no writes. Re-run with --apply to execute.")
            conn.rollback()
            return 0

        # ── APPLY: one transaction ───────────────────────────────────────
        # Held exception rows first: properties only, relation_type unchanged.
        held_updated = 0
        for r, grp in held_rows:
            props = dict(r["properties"] or {})
            props["relation_payload"] = r["relation_type"]
            props["exception"] = {
                "kind": EXCEPTION_KIND,
                "ruling": "c0345945",
                "ontologist_ruling": "1a1084d0",
                "group": f"{grp['source']} -> {grp['target']}",
                "canonical_term": grp["canonical"],
                "representative_original": grp["representative"],
                "rationale": grp["rationale"],
                "decided": "2026-09-15",
            }
            cur.execute(
                "UPDATE knowledge.graph_edges SET properties = %s WHERE id = %s",
                (psycopg2.extras.Json(props), r["id"]),
            )
            held_updated += cur.rowcount

        # Remapped rows: canonical type + relation_payload.
        updated = 0
        for canonical, edges in plan.items():
            for e in edges:
                props = dict(e["properties"] or {})
                props["relation_payload"] = e["relation_type"]
                cur.execute(
                    """
                    UPDATE knowledge.graph_edges
                       SET relation_type = %s,
                           properties = %s
                     WHERE id = %s
                    """,
                    (canonical, psycopg2.extras.Json(props), e["id"]),
                )
                updated += cur.rowcount

        # ── AFTER snapshot + parity checks (inside the transaction) ─────
        cur.execute("SELECT relation_type, count(*)::int AS n FROM knowledge.graph_edges GROUP BY relation_type")
        after = {r["relation_type"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT properties->>'relation_payload' AS orig, count(*)::int AS n
            FROM knowledge.graph_edges
            WHERE properties ? 'relation_payload'
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        payload = {r["orig"]: r["n"] for r in cur.fetchall()}

        held_types = held_types_all

        checks = []
        # 1. Row-count parity: every edge accounted for.
        checks.append(("total rows unchanged", sum(before.values()) == sum(after.values()), f"{sum(before.values())} -> {sum(after.values())}"))
        # 2. Backbone untouched.
        for t in BACKBONE:
            checks.append((f"backbone untouched: {t}", after.get(t) == before[t], f"{before[t]} -> {after.get(t)}"))
        # 3. Frozen pair: count unchanged, no payload.
        for t in FROZEN_TYPES:
            checks.append((f"frozen type count: {t}", after.get(t, 0) == before[t], f"{before[t]} -> {after.get(t, 0)}"))
        cur.execute(
            "SELECT count(*)::int AS n FROM knowledge.graph_edges WHERE relation_type IN %s AND properties ? 'relation_payload'",
            (tuple(BACKBONE),),
        )
        checks.append(("backbone has no relation_payload", cur.fetchone()["n"] == 0, ""))
        # 4. Full payload coverage: every non-backbone original type fully
        #    carries its field name in relation_payload (remapped + held).
        for t, n in before.items():
            if t in BACKBONE:
                continue
            checks.append((f"payload parity: {t}", payload.get(t, 0) == n, f"{n} -> payload {payload.get(t, 0)}"))
        # 5. Remap correctness per canonical term.
        for c, n in planned_remap.items():
            expect = before.get(c, 0) + n
            checks.append((f"canonical count: {c}", after.get(c, 0) == expect, f"expected {expect} -> {after.get(c, 0)}"))
        # 6. Post-state: the only non-backbone, non-frozen relation_types
        #    remaining are exactly the held exception types, one row each.
        leftover = {t: c for t, c in after.items() if t not in BACKBONE and t not in FROZEN_TYPES and t not in planned_remap and t not in held_types}
        checks.append(("no unexpected relation_types remain", not leftover, f"leftover={leftover}"))
        for t in sorted(held_types):
            checks.append((f"held exception type: {t}", after.get(t, 0) == 1, f"count={after.get(t, 0)}"))
        # 7. Held rows: exception doc + payload present, type unchanged.
        cur.execute(
            """
            SELECT count(*)::int AS n FROM knowledge.graph_edges
            WHERE relation_type IN %s
              AND properties ? 'relation_payload'
              AND properties->'exception'->>'kind' = %s
            """,
            (tuple(held_types), EXCEPTION_KIND),
        )
        held_doc_count = cur.fetchone()["n"]
        checks.append(("held rows carry exception doc + payload", held_doc_count == len(held_rows), f"{held_doc_count} vs {len(held_rows)}"))
        # 8. Remapped row total matches plan.
        checks.append(("remapped row count", updated == sum(planned_remap.values()), f"{updated} vs {sum(planned_remap.values())}"))
        checks.append(("held updated count", held_updated == len(held_rows), f"{held_updated} vs {len(held_rows)}"))

        ok = all(c[1] for c in checks)
        print("\n── PARITY CHECKS ──")
        for name, passed, detail in checks:
            print(f"  {'PASS' if passed else 'FAIL'}  {name}  {detail}")
        if not ok:
            print("\nPARITY FAILED — rolling back.")
            conn.rollback()
            return 3

        conn.commit()
        print(f"\nCOMMITTED: {updated} edges remapped, {held_updated} held as documented exceptions; payload preserved; frozen untouched.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
