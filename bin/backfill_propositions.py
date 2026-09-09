#!/usr/bin/env python3
"""backfill_propositions.py — T24 Phase 2 (plan 0006, re-derived as plan 8261642).

Mint resolution.proposition rows for existing concept_relationships that
have evidence (statement_evidence type='concept_relationship') but no
corresponding resolution_proposition link. Idempotent — safe to re-run.

Schema authority (V134, plan 8261642): the ontology tables are canonical in
resolution.* — semantics.concept_relationship / semantics.concept were retired
by V134. fetch_candidates() queries resolution.concept_relationship +
resolution.concept; semantics.statement_evidence (the evidence-link table) was
NOT retired and is still the join target for evidence. Do NOT re-point at
semantics.* ontology tables; they do not exist.

Usage:  backfill_propositions.py [--dry-run]
"""

import argparse
import os
import sys
from typing import Optional

import psycopg2
import psycopg2.extras

DSN = os.environ.get(
    "EPISTEMOLOGIST_PG_DSN",
    os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus"),
)
_FREEBUFF = "6d03abed-cd17-4623-b723-9d96e900f5f2"

def connect():
    return psycopg2.connect(DSN)


def fetch_candidates(cur):
    cur.execute("""
        WITH edged AS (
            SELECT cr.id AS edge_id, cr.from_concept_id, cr.to_concept_id,
                   cr.relationship_type, cr.notes,
                   se.evidence_item_id, se.strength AS ev_strength,
                   se.comment AS ev_comment
            FROM resolution.concept_relationship cr
            JOIN semantics.statement_evidence se
              ON se.statement_id = cr.id
             AND se.statement_type = 'concept_relationship'
             AND se.expired_at IS NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM semantics.statement_evidence se2
                WHERE se2.evidence_item_id = se.evidence_item_id
                  AND se2.statement_type = 'resolution_proposition'
                  AND se2.expired_at IS NULL
            )
        )
        SELECT e.*, fc.name AS from_name, tc.name AS to_name
        FROM edged e
        LEFT JOIN resolution.concept fc ON fc.id = e.from_concept_id
        LEFT JOIN resolution.concept tc ON tc.id = e.to_concept_id
        ORDER BY e.edge_id
    """)
    return [dict(r) for r in cur.fetchall()]


def get_dim_id(cur):
    cur.execute("SELECT id FROM resolution.frame_dimension WHERE name='execution_backend'")
    return cur.fetchone()["id"]


def main():
    ap = argparse.ArgumentParser(description="T24 Phase 2: backfill propositions for existing edges")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    candidates = fetch_candidates(cur)
    conn.close()

    if not candidates:
        print("No edges need backfill. Nothing to do.")
        return 0

    if args.dry_run:
        print(f"DRY RUN: {len(candidates)} edge(s) would be backfilled:")
        for e in candidates[:15]:
            fn = e["from_name"] or e["from_concept_id"][:8]
            tn = e["to_name"] or e["to_concept_id"][:8]
            print(f"  {e['edge_id'][:8]}  {fn} --[{e['relationship_type']}]--> {tn}")
        if len(candidates) > 15:
            print(f"  ... and {len(candidates) - 15} more")
        return 0

    dim_id = None
    with connect() as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cc:
            dim_id = get_dim_id(cc)
    if not dim_id:
        print("FATAL: resolution.frame_dimension 'execution_backend' not found")
        return 1

    created = failed = 0
    for i, e in enumerate(candidates):
        fn = e["from_name"] or f"concept:{e['from_concept_id'][:8]}"
        tn = e["to_name"] or f"concept:{e['to_concept_id'][:8]}"
        title = f"Relationship: {fn} --[{e['relationship_type']}]--> {tn}"
        desc = (
            f"Backfill from T24 Phase 2 (plan 0006). "
            f"source={fn} target={tn} type={e['relationship_type']}"
        )
        try:
            with connect() as c:
                with c.cursor() as cc:
                    # 1. Mint proposition
                    cc.execute(
                        "INSERT INTO resolution.proposition "
                        "(title, description, value, semantic_type_id) "
                        "VALUES (%s, %s, TRUE, "
                        "(SELECT id FROM resolution.semantic_type WHERE LOWER(name)='assertion' LIMIT 1)) "
                        "RETURNING id",
                        (title, desc),
                    )
                    prop_id = cc.fetchone()[0]

                    # 2. Frame
                    cc.execute(
                        "INSERT INTO resolution.proposition_frame_value "
                        "(proposition_id, dimension_id, reference_value_id) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (prop_id, dim_id, _FREEBUFF),
                    )

                    # 3. Link evidence → proposition
                    cc.execute(
                        "INSERT INTO semantics.statement_evidence "
                        "(evidence_item_id, statement_type, statement_id, role, strength, comment) "
                        "VALUES (%s, 'resolution_proposition', %s, 'epistemologist', %s, %s) "
                        "ON CONFLICT (evidence_item_id, statement_type, statement_id, role) "
                        "WHERE expired_at IS NULL DO NOTHING",
                        (e["evidence_item_id"], prop_id,
                         e.get("ev_strength"), e.get("ev_comment")),
                    )
                    c.commit()

            created += 1
            print(f"  [{i+1}/{len(candidates)}] {e['edge_id'][:8]}  {fn} --[{e['relationship_type']}]--> {tn}  →  prop {prop_id[:8]}")
        except Exception as exc:
            failed += 1
            print(f"  [{i+1}/{len(candidates)}] {e['edge_id'][:8]}  FAILED: {exc}")

    # Recheck
    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    remaining = len(fetch_candidates(cur))
    conn.close()

    print(f"\nDone: {created} created, {failed} failed, {remaining} remaining")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())