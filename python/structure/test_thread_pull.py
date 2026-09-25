#!/usr/bin/env python3
"""Conformance tests for the S5 bounded thread-pull (Structure S5).

Pins to-do thread 925f229d's acceptance: termination bounds enforced,
provenance on every expansion item, missing/unparseable targets visible,
deterministic replay of the same read set. Dual-runnable: pytest-compatible
functions plus `python3 python/structure/test_thread_pull.py`.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import sql_parser as sp  # noqa: E402
import thread_pull as tp  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "python" / "structure" / "fixtures" / "sql" / "V178__role_memory.sql"
CYCLE_SQL = (
    "CREATE TABLE a (id int PRIMARY KEY, b_id int REFERENCES b(id));\n"
    "CREATE TABLE b (id int PRIMARY KEY, a_id int REFERENCES a(id));\n"
    "INSERT INTO a VALUES (1);\n"
)


def _corpus_run() -> dict:
    src = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )
    return sp.build_run([src])


def _cycle_run() -> dict:
    src = sp.SqlSource(source_uri="db/migrations/V900__cycle.sql", revision="r",
                       text=CYCLE_SQL)
    return sp.build_run([src])


def _fk_id(run: dict, table: str) -> str:
    return next(
        o["observation_id"] for o in run["observations"]
        if o["fact_kind"] == "foreign_key" and o["payload"].get("table") == table
    )


def _table_id(run: dict, name: str) -> str:
    return next(
        o["observation_id"] for o in run["observations"]
        if o["fact_kind"] == "table" and o["payload"].get("table") == name
    )


def _q(run: dict, anchor: str, **over: object) -> dict:
    query = {
        "direction": "descendants",
        "anchor_observation_id": anchor,
        "cycle_policy": "skip_visited",
        "budgets": {"max_depth": 8, "max_nodes": 64, "max_bytes": 1 << 20},
    }
    budgets = over.pop("budgets", None)
    query.update(over)
    if budgets is not None:
        query["budgets"].update(budgets)
    return tp.pull_neighborhood(run, query)


# ---------------------------------------------------------- query contract


def test_direction_and_policy_are_enforced() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    for bad in ("direction", "cycle_policy", "budgets", "anchor_observation_id"):
        q = {"direction": "descendants", "anchor_observation_id": anchor,
             "cycle_policy": "skip_visited",
             "budgets": {"max_depth": 4, "max_nodes": 8, "max_bytes": 99999}}
        if bad == "direction":
            q["direction"] = "everywhere"
        elif bad == "cycle_policy":
            q["cycle_policy"] = "yolo"
        elif bad == "budgets":
            q["budgets"] = {"max_depth": 0}
        else:
            q["anchor_observation_id"] = None
        try:
            tp.pull_neighborhood(run, q)
        except tp.QueryError:
            continue
        raise AssertionError(f"query accepted out-of-contract value for {bad!r}")
    try:
        tp.pull_neighborhood(run, {"direction": "descendants",
                                   "anchor_observation_id": "nope"})
    except tp.QueryError as e:
        assert "not in run" in str(e)
        return
    raise AssertionError("missing anchor must raise")


# ---------------------------------------------------------- directions


def test_descendants_returns_subtree_with_provenance() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    anchor_path = next(
        o["anchor"]["node_path"] for o in run["observations"]
        if o["observation_id"] == anchor
    )
    out = _q(run, anchor, direction="descendants")
    assert out["item_count"] >= 5  # 4 columns + constraints + alter-added column
    for item in out["items"]:
        assert item["source"]["content_hash"], "full observation embedded"
        assert item["anchor"]["node_path"].startswith(anchor_path + ".")
        assert "_via_direction" in item and "_depth" in item


def test_ancestors_returns_containers() -> None:
    run = _corpus_run()
    anchor = next(
        o["observation_id"] for o in run["observations"]
        if o["fact_kind"] == "column" and o["payload"].get("name") == "key"
    )
    out = _q(run, anchor, direction="ancestors")
    kinds = {i["fact_kind"] for i in out["items"]}
    assert "table" in kinds
    paths = [i["anchor"]["node_path"] for i in out["items"]]
    assert all(len(p) < len(next(
        o["anchor"]["node_path"] for o in run["observations"]
        if o["observation_id"] == anchor)) for p in paths)


def test_siblings_returns_same_parent_others() -> None:
    run = _corpus_run()
    anchor = next(
        o["observation_id"] for o in run["observations"]
        if o["fact_kind"] == "column" and o["payload"].get("name") == "value"
    )
    anchor_path = next(o["anchor"]["node_path"] for o in run["observations"]
                       if o["observation_id"] == anchor)
    parent_prefix = anchor_path.rsplit(".", 1)[0]
    out = _q(run, anchor, direction="siblings")
    assert out["item_count"] >= 3  # id, key, recorded_at
    names = {i["payload"].get("name") for i in out["items"]}
    assert {"id", "key", "recorded_at"} <= names
    # sibling semantics is path-parent, not fact-kind: the table's named
    # constraint legitimately shares the parent
    assert any(i["fact_kind"] == "named_constraint" for i in out["items"])
    assert all(i["anchor"]["node_path"].startswith(parent_prefix + ".")
               for i in out["items"])


def test_references_links_fk_to_referenced_table() -> None:
    run = _corpus_run()
    fk = _fk_id(run, "role_memory_tag")
    out = _q(run, fk, direction="references")
    kinds = {(i["fact_kind"], i["payload"].get("table")) for i in out["items"]}
    assert ("table", "role_memory") in kinds or ("table", "governed_tag") in kinds


def test_references_follows_multi_hop_chain() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    run = dict(run)
    # table -> weight column -> (second hop via bridge edge)
    weight = next(o["observation_id"] for o in run["observations"]
                  if o["fact_kind"] == "column" and o["payload"].get("name") == "weight")
    run["bridge"] = {"candidates": [{"evidence_refs": [weight, anchor]}]}
    out = _q(run, anchor, direction="references",
             budgets={"max_depth": 4, "max_nodes": 64, "max_bytes": 1 << 20})
    assert out["item_count"] >= 2


# ---------------------------------------------------------- budgets


def test_max_nodes_budget_enforced_and_honest() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants",
             budgets={"max_depth": 8, "max_nodes": 2, "max_bytes": 1 << 20})
    assert out["item_count"] == 2
    assert out["truncated"] is True
    assert out["truncation"]["budget"] == "max_nodes"
    assert out["truncation"]["frontier_remaining"] > 0


def test_max_depth_budget_enforced() -> None:
    run = _corpus_run()
    # the FK observation that REFERENCES governed_tag (fks[1]); _fk_id by
    # table alone returns fks[0] (-> role_memory), from which deep-oid is
    # unreachable at any depth — the budget would never bind
    fk = next(o["observation_id"] for o in run["observations"]
              if o["fact_kind"] == "foreign_key"
              and o["payload"].get("references_table") == "governed_tag")
    gov = _table_id(run, "governed_tag")
    # a deep node hanging off governed_tag via the bridge channel makes
    # max_depth bind in a path-shallow corpus: fk -> governed_tag -> deep
    run = dict(run)
    run["bridge"] = {"candidates": [{"evidence_refs": ["deep-oid", gov]}]}
    out = _q(run, fk, direction="references",
             budgets={"max_depth": 1, "max_nodes": 64, "max_bytes": 1 << 20})
    assert out["truncated"] is True
    assert out["truncation"]["budget"] == "max_depth"
    assert all(i["_depth"] <= 1 for i in out["items"])
    ids = {i["observation_id"] for i in out["items"]}
    assert gov in ids, "depth-1 nodes are returned"
    assert "deep-oid" not in ids, "depth-2 nodes are never expanded"
    assert out["truncation"]["frontier_remaining"] == 1, (
        "the single depth-2 node is counted as omitted"
    )


def test_max_depth_frontier_count_is_honest() -> None:
    # Tester finding 8a89a638: at first depth exhaustion the recorded
    # frontier_remaining was hardcoded to 0, understating omitted work.
    # Regression: one depth-1 hop, then three distinct depth-2 targets —
    # the recorded count must equal ALL nodes omitted under the bound
    # (in-budget nodes behind the cut are still returned, so the total is
    # finalized after the drain).
    run = _corpus_run()
    fk = next(o["observation_id"] for o in run["observations"]
              if o["fact_kind"] == "foreign_key"
              and o["payload"].get("references_table") == "governed_tag")
    gov = _table_id(run, "governed_tag")
    run = dict(run)
    run["bridge"] = {"candidates": [
        {"evidence_refs": [f"deep-{k}", gov]} for k in range(3)
    ]}
    out = _q(run, fk, direction="references",
             budgets={"max_depth": 1, "max_nodes": 64, "max_bytes": 1 << 20})
    assert out["truncated"] is True
    assert out["truncation"]["budget"] == "max_depth"
    ids = {i["observation_id"] for i in out["items"]}
    assert gov in ids, "depth-1 nodes are still returned after the cut"
    for k in range(3):
        assert f"deep-{k}" not in ids, "depth-2 nodes are never expanded"
    assert out["truncation"]["frontier_remaining"] == 3, (
        "every depth-omitted node is counted, not just the first"
    )


def test_max_bytes_budget_enforced() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants",
             budgets={"max_depth": 8, "max_nodes": 64, "max_bytes": 1200})
    assert out["truncated"] is True
    assert out["truncation"]["budget"] == "max_bytes"
    assert out["bytes_returned"] <= 1200


def test_unbounded_budgets_return_complete_neighborhood() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants")
    assert out["truncated"] is False and out["truncation"] is None


# ---------------------------------------------------------- cycles


def test_cycle_skip_visited_terminates() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    run = dict(run)
    run["bridge"] = {"candidates": [
        {"evidence_refs": [anchor, "x-oid"]},
        {"evidence_refs": ["x-oid", anchor]},
    ]}
    out = _q(run, anchor, direction="references")
    ids = [i["observation_id"] for i in out["items"]]
    assert len(ids) == len(set(ids)), "skip_visited must not revisit nodes"
    assert anchor not in ids


def test_cycle_fail_closed_raises() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    # an explicit edge cycle through the bridge channel: anchor -> x -> anchor
    run = dict(run)
    run["bridge"] = {"candidates": [
        {"evidence_refs": [anchor, "x-oid"]},
        {"evidence_refs": ["x-oid", anchor]},
    ]}
    try:
        _q(run, anchor, direction="references", cycle_policy="fail_closed",
           budgets={"max_depth": 6, "max_nodes": 64, "max_bytes": 1 << 20})
    except tp.CycleError:
        return
    raise AssertionError("fail_closed policy must raise on cycle revisit")


# ---------------------------------------------------------- determinism


def test_replay_same_read_set_is_deterministic() -> None:
    r1 = _q(_corpus_run(), _table_id(_corpus_run(), "role_memory"),
            direction="descendants")
    r2 = _q(_corpus_run(), _table_id(_corpus_run(), "role_memory"),
            direction="descendants")
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_ordering_is_depth_then_path_then_id() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants")
    keys = [(i["_depth"], i["anchor"]["node_path"], i["observation_id"])
            for i in out["items"]]
    assert keys == sorted(keys)


def test_result_is_stable_across_processes() -> None:
    run_id_code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from structure import sql_parser as sp, thread_pull as tp\n"
        "src = sp.SqlSource(source_uri='db/migrations/V178__role_memory.sql',\n"
        "                   revision='d46733ba',\n"
        "                   text=open(%r, encoding='utf-8').read())\n"
        "run = sp.build_run([src])\n"
        "anchor = next(o['observation_id'] for o in run['observations']\n"
        "              if o['fact_kind'] == 'table' and o['payload']['table'] == 'role_memory')\n"
        "out = tp.pull_neighborhood(run, {'direction': 'descendants',\n"
        "    'anchor_observation_id': anchor, 'cycle_policy': 'skip_visited',\n"
        "    'budgets': {'max_depth': 8, 'max_nodes': 64, 'max_bytes': 65536}})\n"
        "print(json.dumps(out, sort_keys=True))\n"
    ) % (str(REPO_ROOT / "python"), str(CORPUS))
    outs = []
    for seed in ("3", "77"):
        proc = subprocess.run(
            [sys.executable, "-c", run_id_code], capture_output=True, text=True,
            env=dict(os.environ, PYTHONHASHSEED=seed), timeout=120, check=True,
        )
        outs.append(proc.stdout)
    assert outs[0] == outs[1], "neighborhood must be byte-stable across processes"


# ---------------------------------------------------------- honesty


def test_provenance_echo_in_result() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants")
    assert out["provenance"]["read_set_fingerprint"] == run["read_set_fingerprint"]
    assert out["provenance"]["run_id"] == run["run_id"]
    assert out["provenance"]["parser"]["grammar_revision"] == sp.GRAMMAR_REVISION


def test_unresolved_targets_are_visible() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    # inject a dangling reference edge
    run2 = dict(run)
    run2["bridge"] = {"candidates": [{"evidence_refs": [anchor, "missing-oid"]}]}
    out = _q(run2, anchor, direction="references")
    assert any(u["observation_id"] == "missing-oid"
               for u in out["unresolved_targets"])


def test_fact_kind_filter_narrows_without_breaking_bounds() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants", fact_kind_filter=["column"])
    assert out["item_count"] >= 1
    assert all(i["fact_kind"] == "column" for i in out["items"])


def test_blackboard_surface_is_adapter_work() -> None:
    run = _corpus_run()
    anchor = _table_id(run, "role_memory")
    out = _q(run, anchor, direction="descendants")
    assert out["authority_status"] == "non_authoritative"
    assert "blackboard" in out["note"]


# ---------------------------------------------------------- integration


def test_works_over_s4_bridged_run() -> None:
    import bridge as br

    run = _corpus_run()
    brout = br.derive_candidates(run["observations"])
    run["bridge"] = brout
    fk = _fk_id(run, "role_memory_tag")
    out = _q(run, fk, direction="references")
    assert out["item_count"] >= 1
    assert br.validate_candidates(brout) == []


def test_descendants_work_for_schema_qualified_tables() -> None:
    # schema-qualified names: qualifier dots must not collide with the
    # node_path separator, or the table's subtree disconnects from its
    # columns and descendants silently returns nothing (found in the
    # 166-migration live smoke, where most tables carry schema qualifiers)
    src = sp.SqlSource(
        source_uri="db/migrations/V901__qualified.sql",
        revision="r",
        text=(
            "CREATE TABLE nebula.agent_connections (\n"
            "  conn_id int PRIMARY KEY,\n"
            "  session_id int NOT NULL,\n"
            "  recorded_on timestamptz DEFAULT now()\n"
            ");\n"
        ),
    )
    run = sp.build_run([src])
    anchor = next(o["observation_id"] for o in run["observations"]
                  if o["fact_kind"] == "table"
                  and o["payload"].get("table") == "nebula.agent_connections")
    out = _q(run, anchor, direction="descendants")
    assert out["item_count"] >= 3, (
        "qualified table must have its columns in one connected subtree"
    )
    assert all(i["fact_kind"] == "column" for i in out["items"])
    # the anchor's path contains no separator dots from the qualifier itself
    anchor_path = next(o["anchor"]["node_path"] for o in run["observations"]
                       if o["observation_id"] == anchor)
    assert "table.nebula__agent_connections" in anchor_path, anchor_path
    assert "table.nebula." not in anchor_path, anchor_path


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"{len(fns) - failed}/{len(fns)} pass")
    sys.exit(1 if failed else 0)
