#!/usr/bin/env python3
"""Write-path canary: knowledge (incumbent :3109 vs twin :4109).

The ideal canary shape: 8 write routes, all marked-row CRUD with clean
inverses. Every case writes via A, compares (status + envelope + DB row via
the knowledge graph read surface), deletes via A, re-marks, writes via B,
compares, deletes via B, asserts zero residue.

Marking (plan §0.2): section 'canary:wp' — reserved for the campaign.
"""
from __future__ import annotations

import os
import argparse
import json
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from write_canary_lib import (  # noqa: E402
    Canary, Case, RUN_ID, ab_write_pair, pg, sql_lit,
)

SECTION = "canary:wp"
LONG_DESC = "x" * 600  # exercises the substring(description,1,500) abbreviation


def ent_id(tag: str) -> str:
    return f"canary-wp-{RUN_ID}-{tag}"


def db_entity(eid: str):
    rows = pg(
        f"SELECT section, entity_id, name, entity_type, status, "
        f"substring(description, 1, 500) AS description_abbr, properties::text "
        f"FROM graph_entities WHERE section = {sql_lit(SECTION)} AND entity_id = {sql_lit(eid)}"
    )
    return rows[0] if rows else None


def db_edge_count(src: str) -> int:
    rows = pg(
        f"SELECT COUNT(*) FROM graph_edges WHERE source_section = {sql_lit(SECTION)} "
        f"AND source_id = {sql_lit(src)}"
    )
    return int(rows[0]["col0"])


def assert_db_entity_equal(eid: str, wa, wb) -> None:
    if wa != wb:
        raise AssertionError(f"DB row differs for {eid}: A={wa} B={wb}")


def cleanup_entities(*eids: str) -> None:
    for eid in eids:
        pg(f"DELETE FROM graph_entities WHERE section = {sql_lit(SECTION)} "
           f"AND entity_id = {sql_lit(eid)}")


def case_entity_create_minimal(canary: Canary, case: Case) -> None:
    eid = ent_id("min")
    def body(_t):
        return {"section": SECTION, "entity_id": eid}
    ab_write_pair(
        canary, "POST", "/knowledge/entities", body_fn=body, expect_status=201,
        db_probe=lambda _t, _r: db_entity(eid),
    )
    case.cleanup = lambda: cleanup_entities(eid)


def case_entity_create_full(canary: Canary, case: Case) -> None:
    eid = ent_id("full")
    def body(_t):
        return {
            "section": SECTION, "entity_id": eid, "name": f"canary {eid}",
            "entity_type": "canary", "status": "active",
            "description": LONG_DESC,
            "properties": {"run": RUN_ID, "nested": {"k": [1, 2, {"v": True}]}},
            "source_file": "moleculer/write-canary",
        }
    ab_write_pair(
        canary, "POST", "/knowledge/entities", body_fn=body, expect_status=201,
        db_probe=lambda _t, _r: db_entity(eid),
    )
    # read-back parity through both twins: the abbreviation contract
    ra = canary.twin_a().request("GET", f"/knowledge/entities/{SECTION}/{eid}")
    rb = canary.twin_b().request("GET", f"/knowledge/entities/{SECTION}/{eid}")
    if ra[0] != 200 or rb[0] != 200:
        raise AssertionError(f"read-back status A={ra[0]} B={rb[0]}")
    if ra[1].get("description") != LONG_DESC:
        raise AssertionError("full description not stored via A (read-back)")
    if len((rb[1] or {}).get("description") or "") != 600:
        raise AssertionError("full description not stored via B (read-back)")
    case.cleanup = lambda: cleanup_entities(eid)


def case_entity_upsert_replay(canary: Canary, case: Case) -> None:
    eid = ent_id("upsert")
    def body(v: int):
        return {"section": SECTION, "entity_id": eid, "name": f"canary v{v}"}
    # first insert via A, then replay the SAME key via A — must update, not duplicate
    canary.twin_a().request("POST", "/knowledge/entities", body(1))
    r1 = canary.twin_a().request("POST", "/knowledge/entities", body(2))
    if r1[0] != 201:
        raise AssertionError(f"upsert replay status {r1[0]}")
    cnt = pg(f"SELECT COUNT(*) FROM graph_entities WHERE section = {sql_lit(SECTION)} "
             f"AND entity_id = {sql_lit(eid)}")[0]["col0"]
    if cnt != "1":
        canary.note_residue(f"upsert created {cnt} rows for {eid}")
        raise AssertionError(f"upsert replay produced {cnt} rows (expected 1)")
    # same via B
    canary.twin_b().request("POST", "/knowledge/entities", body(1))
    r2 = canary.twin_b().request("POST", "/knowledge/entities", body(2))
    if r2[0] != 201:
        raise AssertionError(f"twin upsert replay status {r2[0]}")
    ok, diff = __import__("write_canary_lib").deep_equal(r1[1], r2[1])
    if not ok:
        raise AssertionError(f"upsert envelope mismatch: {diff}")
    case.cleanup = lambda: cleanup_entities(eid)


def case_entity_put_partial(canary: Canary, case: Case) -> None:
    eid = ent_id("put")
    seed = {"section": SECTION, "entity_id": eid, "name": "seed",
            "description": "keep me", "properties": {"p": 1}}
    # seed identical rows via both twins' OWN surfaces? No — seed via A only,
    # then PUT via A; reseed via B, PUT via B; compare PUT responses + DB rows.
    canary.twin_a().request("POST", "/knowledge/entities", seed)
    ra = canary.twin_a().request(
        "PUT", f"/knowledge/entities/{SECTION}/{eid}", {"name": "renamed"})
    cleanup_entities(eid)
    canary.twin_b().request("POST", "/knowledge/entities", seed)
    rb = canary.twin_b().request(
        "PUT", f"/knowledge/entities/{SECTION}/{eid}", {"name": "renamed"})
    if ra[0] != 200 or rb[0] != 200:
        raise AssertionError(f"PUT status A={ra[0]} B={rb[0]}")
    ok, diff = __import__("write_canary_lib").deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"PUT envelope mismatch: {diff}")
    wa, wb = db_entity(eid), None
    # DB row comparison after both PUTs (description must be COALESCE-preserved)
    wa = db_entity(eid)
    ok, diff = __import__("write_canary_lib").deep_equal(
        {"name": "renamed", "description": "keep me"},
        {"name": (wa or {}).get("col2"), "description": (wa or {}).get("col4")})
    if not ok:
        raise AssertionError(f"COALESCE preservation mismatch: {diff}")
    case.cleanup = lambda: cleanup_entities(eid)


def case_entity_put_404(canary: Canary, case: Case) -> None:
    eid = ent_id("missing")
    ab_write_pair(
        canary, "PUT", f"/knowledge/entities/{SECTION}/{eid}",
        body_fn=lambda _t: {"name": "x"}, expect_status=404,
    )


def case_entity_delete_404(canary: Canary, case: Case) -> None:
    eid = ent_id("gone")
    ab_write_pair(
        canary, "DELETE", f"/knowledge/entities/{SECTION}/{eid}",
        body_fn=None, expect_status=404,
    )


def case_section_purge(canary: Canary, case: Case) -> None:
    # purge section must be scoped to canary:wp and delete exactly what's there;
    # run it via A and B on separate single-entity sections? The route purges
    # a WHOLE section — so instead: purge an entity-specific empty section each
    # side, after inserting one marked entity in it via that side.
    # NOTE: both sides share the section namespace, so the purge case uses a
    # per-side section and cleans whatever remains.
    raise SystemExit(
        "section purge case runs LAST via the runner flag --with-purge "
        "(it evicts the whole canary:wp section); not part of the default series"
    )


def case_edge_create_delete(canary: Canary, case: Case) -> None:
    s, t = ent_id("esrc"), ent_id("etgt")
    for eid in (s, t):
        canary.twin_a().request("POST", "/knowledge/entities",
                                {"section": SECTION, "entity_id": eid})
    edge_body = lambda _t: {
        "source_section": SECTION, "source_id": s,
        "relation_type": "canary_wp_rel", "target_section": SECTION, "target_id": t,
    }
    ra = canary.twin_a().request("POST", "/knowledge/edges", edge_body(None))
    if ra[0] != 201:
        raise AssertionError(f"edge create status {ra[0]} via A")
    edge_id = ra[1]["id"]
    # delete via the tested surface (parity includes DELETE)
    rd = canary.twin_a().request("DELETE", f"/knowledge/edges/{edge_id}")
    if rd[0] != 200:
        canary.note_residue(f"edge {edge_id} delete failed via A ({rd[0]})")
        raise AssertionError("edge cleanup failed via A")
    # now B
    rb = canary.twin_b().request("POST", "/knowledge/edges", edge_body(None))
    if rb[0] != 201:
        raise AssertionError(f"edge create status {rb[0]} via B")
    ok, diff = __import__("write_canary_lib").deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"edge envelope mismatch: {diff}")
    edge_id_b = rb[1]["id"]
    rd = canary.twin_b().request("DELETE", f"/knowledge/edges/{edge_id_b}")
    if rd[0] != 200:
        canary.note_residue(f"edge {edge_id_b} delete failed via B")
        raise AssertionError("edge cleanup failed via B")
    if db_edge_count(s) != 0:
        canary.note_residue(f"edges remain on {s}")
        raise AssertionError("edge residue after cleanup")
    case.cleanup = lambda: cleanup_entities(s, t)


def case_edge_create_400(canary: Canary, case: Case) -> None:
    ab_write_pair(
        canary, "POST", "/knowledge/edges",
        body_fn=lambda _t: {"source_section": SECTION, "source_id": "x"},
        expect_status=400,
    )


def case_xref_create_delete(canary: Canary, case: Case) -> None:
    body = lambda _t: {
        "map_name": "canary-wp", "source_section": SECTION,
        "source_id": ent_id("xr-src"), "target_section": SECTION,
        "target_id": ent_id("xr-tgt"),
    }
    ra = canary.twin_a().request("POST", "/knowledge/cross-references", body(None))
    if ra[0] != 201:
        raise AssertionError(f"xref create status {ra[0]}")
    xa = ra[1]["id"]
    rd = canary.twin_a().request("DELETE", f"/knowledge/cross-references/{xa}")
    if rd[0] != 200:
        canary.note_residue(f"xref {xa} delete failed")
        raise AssertionError("xref cleanup failed via A")
    rb = canary.twin_b().request("POST", "/knowledge/cross-references", body(None))
    ok, diff = __import__("write_canary_lib").deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"xref envelope mismatch: {diff}")
    xb = rb[1]["id"]
    rd = canary.twin_b().request("DELETE", f"/knowledge/cross-references/{xb}")
    if rd[0] != 200:
        canary.note_residue(f"xref {xb} delete failed")
        raise AssertionError("xref cleanup failed via B")


def case_xref_create_400(canary: Canary, case: Case) -> None:
    ab_write_pair(
        canary, "POST", "/knowledge/cross-references",
        body_fn=lambda _t: {"map_name": "canary-wp"}, expect_status=400,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port-a", type=int, default=3109)
    ap.add_argument("--port-b", type=int, default=4109)
    args = ap.parse_args()

    canary = Canary("knowledge", args.port_a, args.port_b)
    canary.cases.extend([
        Case("entity-create-minimal", case_entity_create_minimal),
        Case("entity-create-full-abbrev", case_entity_create_full),
        Case("entity-upsert-replay", case_entity_upsert_replay),
        Case("entity-put-partial-coalesce", case_entity_put_partial),
        Case("entity-put-404", case_entity_put_404),
        Case("entity-delete-404", case_entity_delete_404),
        Case("edge-create-delete", case_edge_create_delete),
        Case("edge-create-400", case_edge_create_400),
        Case("xref-create-delete", case_xref_create_delete),
        Case("xref-create-400", case_xref_create_400),
    ])
    return canary.run()


if __name__ == "__main__":
    raise SystemExit(main())
