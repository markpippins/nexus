#!/usr/bin/env python3
"""Conformance tests for the S2 SQL parser (Structure S2).

Pins to-do thread 9a7eab15's acceptance criteria and ruling 143b0e04's
first-slice gate: deterministic (byte-stable) output for pinned sources,
unsupported syntax as explicit findings, full provenance on every fact,
changed-grammar ⇒ new observation population, and parser-level negatives for
comments, dollar-quoted procedural bodies, dynamic SQL, and extension syntax.
Dual-runnable: pytest-compatible functions plus
`python3 python/structure/test_sql_parser.py`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import contract as sc  # noqa: E402
import sql_parser as sp  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "python" / "structure" / "fixtures" / "sql"
CORPUS = FIXTURES / "V178__role_memory.sql"
NEGATIVE = FIXTURES / "negative_unsupported.sql"


def _corpus_source() -> sp.SqlSource:
    return sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )


def _negative_source() -> sp.SqlSource:
    return sp.SqlSource(
        source_uri="db/migrations/fixtures/negative_unsupported.sql",
        revision="d46733ba",
        text=NEGATIVE.read_text(),
        role="negative_fixture",
    )


def _kinds(run: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in run["observations"]:
        counts[o["fact_kind"]] = counts.get(o["fact_kind"], 0) + 1
    return counts


# ------------------------------------------------------------ tokenizer


def test_tokenizer_hides_comments_and_bodies_dollar_quoted() -> None:
    tokens = sp.tokenize(CORPUS.read_text())
    joined = " ".join(t.text for t in tokens)
    # comment content is skipped entirely
    assert "nested" not in joined and "outer" not in joined
    # the procedural body is one dollar token, not parsed into words
    dollars = [t for t in tokens if t.kind == "dollar"]
    assert len(dollars) == 1
    assert "UPDATE role_memory" in dollars[0].text
    words = [t.text.upper() for t in tokens if t.kind == "word"]
    assert "BEGIN" not in words and "RETURN" not in words


def test_statements_do_not_split_inside_dollar_bodies() -> None:
    statements = sp.split_statements(sp.tokenize(CORPUS.read_text()))
    # 11 statements: type, 3 tables, 2 indexes, 2 alters, insert,
    # create function (dollar body kept whole), select
    assert len(statements) == 11
    fn_stmt = statements[9]
    assert sp._kw(fn_stmt.tokens[0]) == "create"


# ------------------------------------------------------------ grammar


def test_corpus_facts_inventory() -> None:
    run = sp.build_run([_corpus_source()])
    assert run["findings"] == [], run["findings"]
    counts = _kinds(run)
    assert counts == {
        "table": 3,
        "column": 11,  # 4 + 4 + 2 in tables, +1 via ALTER ADD COLUMN
        "foreign_key": 2,  # inline REFERENCES on role_memory_tag
        "named_constraint": 4,  # UNIQUE(key), uq_governed_tag, PK pair, CHECK
        "index": 2,
        "enum_type": 1,
        "seed_value": 2,
        "operation": 2,  # create function (unsupported) + SELECT (unsupported)
    }


def test_column_facts_carry_types_and_defaults() -> None:
    run = sp.build_run([_corpus_source()])
    cols = {
        o["payload"]["name"]: o["payload"]
        for o in run["observations"]
        if o["fact_kind"] == "column" and o["payload"]["table"] == "role_memory"
    }
    assert cols["id"]["type"] == "BIGSERIAL" and cols["id"]["primary_key"] is True
    assert cols["key"]["nullable"] is False
    assert cols["value"]["default"] == ""
    assert cols["recorded_at"]["default"] == {"raw": "now()"} or cols[
        "recorded_at"
    ]["default"] == {"raw": "now ()"}
    weight = [
        o["payload"]
        for o in run["observations"]
        if o["fact_kind"] == "column" and o["payload"]["name"] == "weight"
    ]
    assert weight and weight[0]["default"] == "1.0"


def test_foreign_keys_are_unmapped_relation_candidates() -> None:
    run = sp.build_run([_corpus_source()])
    fks = [o for o in run["observations"] if o["fact_kind"] == "foreign_key"]
    assert len(fks) == 2
    refs = sorted(fk["payload"]["references_table"] for fk in fks)
    assert refs == ["governed_tag", "role_memory"]
    for fk in fks:
        assert fk["relation_mapping"] == {"status": "unmapped"}


def test_alter_add_constraint_yields_check_fact() -> None:
    run = sp.build_run([_corpus_source()])
    checks = [
        o for o in run["observations"]
        if o["fact_kind"] == "named_constraint"
        and o["payload"].get("kind") == "check"
    ]
    assert len(checks) == 1
    assert checks[0]["payload"]["constraint_name"] == "fk_governed_tag_key"
    assert "length" in checks[0]["payload"]["expr"]


def test_enum_labels_are_ordered_and_complete() -> None:
    run = sp.build_run([_corpus_source()])
    enums = [o for o in run["observations"] if o["fact_kind"] == "enum_type"]
    assert len(enums) == 1
    assert enums[0]["payload"]["labels"] == ["human", "nlp", "structural"]


def test_seed_values_are_exact() -> None:
    run = sp.build_run([_corpus_source()])
    seeds = [o for o in run["observations"] if o["fact_kind"] == "seed_value"]
    assert [s["payload"]["values"] for s in seeds] == [
        ["bootstrap", "v1"],
        ["aspect", "g6"],
    ]
    assert seeds[0]["payload"]["columns"] == ["key", "value"]


def test_trailing_statement_without_semicolon_is_accounted() -> None:
    run = sp.build_run([
        sp.SqlSource(source_uri="db/migrations/V1__tail.sql", revision="r",
                     text="CREATE TABLE tail_t (id int)"),
    ])
    tables = [o for o in run["observations"] if o["fact_kind"] == "table"]
    assert len(tables) == 1 and tables[0]["payload"]["table"] == "tail_t"


def test_insert_select_is_partial_with_diagnostics() -> None:
    run = sp.build_run([
        sp.SqlSource(source_uri="db/migrations/V2__copy.sql", revision="r",
                     text="INSERT INTO tags_new SELECT * FROM tags_old;"),
    ])
    assert len(run["observations"]) == 1
    o = run["observations"][0]
    assert o["parse_status"] == "partial"
    assert o["diagnostics"], "partial parse must carry diagnostics"
    assert not any(x["fact_kind"] == "seed_value" for x in run["observations"])


# ------------------------------------------------------------ honesty


def test_unsupported_constructs_yield_explicit_findings_only() -> None:
    run = sp.build_run([_negative_source()])
    assert run["findings"] == []
    assert len(run["observations"]) == 5, "every statement accounted, exactly once"
    for o in run["observations"]:
        assert o["fact_kind"] == "operation"
        assert o["parse_status"] == "unsupported"
        assert o["diagnostics"], "unsupported observation must carry diagnostics"
        assert o["diagnostics"][0]["code"] == "unsupported_syntax"
    actions = " ".join(o["payload"]["action"] for o in run["observations"])
    for construct in ("create extension", "view", "do", "execute", "trigger"):
        assert construct in actions, f"missing explicit finding for {construct}"


def test_no_facts_esc_procedural_or_dynamic_internals() -> None:
    run = sp.build_run([_negative_source()])
    # nothing inside the DO body / EXECUTE / VIEW may become a structural fact
    for o in run["observations"]:
        assert o["fact_kind"] == "operation"
        payload_text = sp.json_canonical(o["payload"])
        assert "temp_table" not in payload_text
        assert "audit_log" not in payload_text
        assert "active_roles" not in payload_text
        # head-only detail policy: construct internals never echo into payloads
        assert "CREATE INDEX idx_temp" not in payload_text
        assert "some_table" not in payload_text


def test_function_body_interiors_are_not_facts() -> None:
    run = sp.build_run([_corpus_source()])
    for o in run["observations"]:
        assert not (
            o["fact_kind"] == "column" and o["payload"].get("table") == "role_memory"
            and o["payload"].get("name") == "recorded_at2"
        )
        payload_text = sp.json_canonical(o["payload"])
        # the UPDATE inside $body$ must not produce a table/column/index fact
        if o["fact_kind"] in ("table", "column", "index", "foreign_key"):
            assert "NEW.id" not in payload_text


# ------------------------------------------------------------ contract fit


def test_all_observations_pass_s1_validator() -> None:
    run = sp.build_run([_corpus_source(), _negative_source()])
    assert run["findings"] == [], run["findings"]


def test_every_fact_carries_full_provenance() -> None:
    src = _corpus_source()
    run = sp.build_run([src])
    expected_hash = hashlib.sha256(src.text.encode("utf-8")).hexdigest()
    for o in run["observations"]:
        assert o["source"]["content_hash"] == expected_hash
        assert o["source"]["revision"] == src.revision
        assert o["parser"]["parser_revision"] == sp.PARSER_REVISION
        assert o["parser"]["grammar_revision"] == sp.GRAMMAR_REVISION
        assert o["anchor"]["span"], "source span required"
        assert o["authority_status"] == sc.AUTHORITY_STATUS
        assert o["read_set_fingerprint"] == run["read_set_fingerprint"]


def test_run_read_set_fingerprint_matches_contract() -> None:
    run = sp.build_run([_corpus_source(), _negative_source()])
    assert run["read_set_fingerprint"] == sc.read_set_fingerprint(run["read_set"])
    assert run["contract_revision"] == sc.STRUCTURE_CONTRACT_REVISION


def test_capability_profile_is_explicit() -> None:
    profile = sp.CAPABILITY_PROFILE
    assert "plpgsql_procedural_bodies" in profile["explicitly_unsupported"]
    assert "dynamic_sql_execute" in profile["explicitly_unsupported"]
    assert profile["grammar_revision"] == sp.GRAMMAR_REVISION


def test_full_accounting_every_statement_yields_an_observation() -> None:
    run = sp.build_run([_corpus_source(), _negative_source()])
    # statement_index is per-source; both sources must be fully accounted
    corpus_seen = {
        o["payload"]["statement_index"]
        for o in run["observations"]
        if o["source"]["source_uri"].endswith("V178__role_memory.sql")
    }
    negative_seen = {
        o["payload"]["statement_index"]
        for o in run["observations"]
        if o["source"]["source_uri"].endswith("negative_unsupported.sql")
    }
    assert corpus_seen == set(range(11))
    assert negative_seen == set(range(5))


# ------------------------------------------------------------ replay


def test_snapshot_replay_is_byte_stable() -> None:
    srcs = [_corpus_source(), _negative_source()]
    snap = sp.snapshot_run(sp.build_run(srcs), srcs)
    report = sp.replay(snap)
    assert report["byte_stable"] is True, report
    assert report["snapshot_hash_matches"] is True
    assert report["observation_set_matches"] is True


def test_replay_detects_tampering() -> None:
    srcs = [_corpus_source()]
    snap = sp.snapshot_run(sp.build_run(srcs), srcs)
    tampered = copy.deepcopy(snap)
    tampered["run"]["observations"] = tampered["run"]["observations"][:-1]
    report = sp.replay(tampered)
    assert report["byte_stable"] is False
    assert report["observation_set_matches"] is False


def test_cross_process_byte_stability_under_hash_seeds() -> None:
    code = (
        "import hashlib, sys\n"
        "sys.path.insert(0, %r)\n"
        "from structure import sql_parser as sp\n"
        "src = sp.SqlSource(source_uri='db/migrations/V178__role_memory.sql',\n"
        "                   revision='d46733ba',\n"
        "                   text=open(%r, encoding='utf-8').read())\n"
        "snap = sp.snapshot_run(sp.build_run([src]), [src])\n"
        "print(snap['snapshot_hash'])\n"
    ) % (str(REPO_ROOT / "python"), str(CORPUS))
    hashes = []
    for seed in ("1", "42"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env=env, timeout=120, check=True,
        )
        hashes.append(proc.stdout.strip())
    local = sp.snapshot_run(sp.build_run([_corpus_source()]), [_corpus_source()])
    hashes.append(local["snapshot_hash"])
    assert len(set(hashes)) == 1, f"hashes diverge across seeds: {hashes}"


# ------------------------------------------------------------ drift


def test_changed_grammar_creates_new_population_same_source_facts() -> None:
    srcs = [_corpus_source()]
    snap_a = sp.snapshot_run(sp.build_run(srcs), srcs)
    original_revision = sp.GRAMMAR_REVISION
    try:
        sp.GRAMMAR_REVISION = "sql-ddl-v0.3.0"
        snap_b = sp.snapshot_run(sp.build_run(srcs), srcs)
    finally:
        sp.GRAMMAR_REVISION = original_revision
    report = sp.classify_drift(snap_a, snap_b)
    assert report["verdict"] == "new_population"
    assert report["same_parser"] is True
    assert report["same_grammar"] is False
    assert report["observation_ids_changed"] is True
    assert report["source_facts_shared"] is True, (
        "source_fact_id is location identity: parser/grammar independent"
    )


def test_same_revisions_diverging_output_is_parser_drift() -> None:
    srcs = [_corpus_source()]
    snap_a = sp.snapshot_run(sp.build_run(srcs), srcs)
    snap_b = copy.deepcopy(snap_a)
    snap_b["run"]["observations"] = snap_b["run"]["observations"][:-1]
    report = sp.classify_drift(snap_a, snap_b)
    assert report["verdict"] == "parser_drift"
    assert report["same_grammar"] is True


def test_identical_snapshots_classify_identical() -> None:
    srcs = [_corpus_source()]
    snap_a = sp.snapshot_run(sp.build_run(srcs), srcs)
    snap_b = sp.snapshot_run(sp.build_run(srcs), srcs)
    assert sp.classify_drift(snap_a, snap_b)["verdict"] == "identical"


# ------------------------------------------------------------ main


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
