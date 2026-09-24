#!/usr/bin/env python3
"""Conformance tests for the S4 relation-candidate bridge (Structure S4).

Pins to-do thread 8f52f17e's acceptance criteria: fixtures cover recognized,
ambiguous, and unmapped references; candidate output retains the AST anchor
and obeys the cond-3 vocabulary contract (governed V182 names only — no
invented predicates); no AST fact self-authorizes a governed relation
(candidates carry names, never UUIDs; state is always 'candidate').
Dual-runnable: pytest-compatible functions plus
`python3 python/structure/test_bridge.py`.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import bridge as br  # noqa: E402
import contract as sc  # noqa: E402
import sql_parser as sp  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "python" / "structure" / "fixtures"
CORPUS = FIXTURES / "sql" / "V178__role_memory.sql"
VOCAB = json.loads((FIXTURES / "relation_vocabulary_v182.json").read_text())


def _corpus_run() -> list[dict]:
    src = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )
    return sp.build_run([src])["observations"]


def _fk_obs() -> dict:
    return next(o for o in _corpus_run() if o["fact_kind"] == "foreign_key")


def _seed_obs() -> dict:
    return next(o for o in _corpus_run() if o["fact_kind"] == "seed_value")


def _unrecognized_obs() -> dict:
    return {
        "observation_id": "0" * 64,
        "source_fact_id": "1" * 64,
        "fact_kind": "named_constraint",
        "parse_status": "complete",
        "payload": {"kind": "check", "expr": "length(tag_key) > 0"},
        "anchor": {"node_path": "statements[7].table.x.constraints[0]"},
        "source": {"source_uri": "x", "revision": "r", "content_hash": "2" * 64,
                   "language": "sql", "source_kind": "sql_migration"},
    }


# --------------------------------------------------------- vocabulary pin


def test_vocabulary_fixture_is_pinned_and_intact() -> None:
    assert len(VOCAB["names"]) == 23
    assert br.GOVERNED_NAMES == frozenset(VOCAB["names"])
    assert br.VOCABULARY_REVISION == "resolution.relation_vocabulary@V182"
    assert VOCAB["source_freeze_draft"] == "df6b70c4-ec1a-4d53-affe-79fcb33d48c9"
    # names only: the pin itself carries no governed UUIDs
    assert "id" not in VOCAB["names"]


def test_vocabulary_names_are_snake_case_verbatim() -> None:
    for name in br.GOVERNED_NAMES:
        assert name == name.lower() and " " not in name
    assert "depends_on" in br.GOVERNED_NAMES
    # the crucial vocabulary fact the bridge is built around: there is NO
    # governed 'references' type — a table->table FK has no exact governed
    # reading, which is why FK shapes defer to Resolution/Aspects
    assert "references" not in br.GOVERNED_NAMES


# --------------------------------------------------------- recognized


def test_fk_shape_is_structurally_ambiguous_unmapped_finding() -> None:
    """'references' is NOT in V182; depends_on and basis_of both plausibly
    match a table->table FK, so the mapping is ambiguous and DEFERRED — an
    explicit unresolved finding listing the plausible governed types, never
    an auto-picked candidate."""
    out = br.derive_candidates(_corpus_run())
    fk_findings = [u for u in out["unresolved"]
                   if u["resolution"] == "ambiguous"]
    assert len(fk_findings) == 2
    f = fk_findings[0]
    assert f["relation_type"] == br.UNRESOLVED
    assert sorted(f["plausible_governed_types"]) == ["basis_of", "depends_on"]
    assert f["subject"]["value"] == "role_memory_tag"
    assert f["object"]["value"] in ("role_memory", "governed_tag")
    assert "deferred" in f["reason"]
    # and no FK-shaped candidate may exist
    assert not any(
        c["subject"]["value"] == "role_memory_tag" for c in out["candidates"]
    )


def test_candidates_retain_ast_anchor() -> None:
    out = br.derive_candidates(_corpus_run())
    for c in out["candidates"] + out["unresolved"]:
        assert c.get("anchor"), "candidate must retain its AST anchor"
        assert c["anchor"].get("node_path")


def test_seed_yields_governed_member_of_candidate() -> None:
    out = br.derive_candidates(_corpus_run())
    seeds = [c for c in out["candidates"] if c["relation_type"] == "member_of"]
    assert len(seeds) == 2
    cand = seeds[0]
    assert cand["state"] == "candidate"
    assert cand["resolution"] == "resolved"
    assert cand["subject"]["kind"] == "source_name" == cand["object"]["kind"]
    assert cand["subject"]["value"] == "role_memory"
    assert "bootstrap" in cand["object"]["value"]
    assert cand["authority_status"] == sc.AUTHORITY_STATUS
    assert cand["evidence_refs"] == [_seed_obs()["observation_id"]]


def test_insert_select_yields_derives_from_candidate() -> None:
    src = sp.SqlSource(source_uri="db/migrations/V2__copy.sql", revision="r",
                       text="INSERT INTO tags_new SELECT * FROM tags_old;")
    run = sp.build_run([src])
    out = br.derive_candidates(run["observations"])
    derives = [c for c in out["candidates"] if c["relation_type"] == "derives_from"]
    assert len(derives) == 1
    assert derives[0]["subject"]["value"] == "tags_new"
    assert "tags_old" in derives[0]["object"]["value"]


def test_unsupported_observations_produce_nothing() -> None:
    run = sp.build_run([
        sp.SqlSource(source_uri="n.sql", revision="r",
                     text=pathlib.Path(
                         "python/structure/fixtures/sql/negative_unsupported.sql"
                     ).read_text()),
    ])
    out = br.derive_candidates(run["observations"])
    assert out["candidates"] == []
    assert out["unresolved"] == []
    # unsupported facts make no claims — nothing bridges from them


# --------------------------------------------------------- ambiguous


def test_multi_match_goes_to_unresolved_not_candidates() -> None:
    """Spec: ambiguous mappings remain findings — never candidates."""
    out = br.derive_candidates(_corpus_run())
    ambiguous = [u for u in out["unresolved"] if u["resolution"] == "ambiguous"]
    resolved = [c for c in out["candidates"] if c["resolution"] == "resolved"]
    assert ambiguous, "the FK shapes must be present as ambiguous findings"
    # every resolved candidate comes from a single-match shape
    assert all(c["relation_type"] in br.GOVERNED_NAMES for c in resolved)


def test_resolved_candidates_only_from_exact_single_match() -> None:
    out = br.derive_candidates(_corpus_run())
    for c in out["candidates"]:
        assert c["relation_type"] in ("member_of", "derives_from")
        assert c["resolution"] == "resolved"


# --------------------------------------------------------- unmapped


def test_non_relation_shapes_are_silently_ignored() -> None:
    """Only relation-shaped facts (FK, seed, derivation) reach the pattern
    table; columns/constraints/etc. are not candidate material and produce
    neither candidates nor findings."""
    out = br.derive_candidates([_unrecognized_obs()])
    assert out["candidates"] == []
    assert out["unresolved"] == []


def test_no_invented_predicates_in_output() -> None:
    out = br.derive_candidates(_corpus_run())
    for c in out["candidates"]:
        assert c["relation_type"] in br.GOVERNED_NAMES
    for u in out["unresolved"]:
        assert u["relation_type"] == br.UNRESOLVED
        # plausible types recorded on findings are governed too
        for t in u.get("plausible_governed_types", []):
            assert t in br.GOVERNED_NAMES


# --------------------------------------------------------- guardrails


def test_validator_passes_wellformed_bridge() -> None:
    out = br.derive_candidates(_corpus_run())
    assert br.validate_candidates(out) == []


def test_validator_rejects_non_governed_type() -> None:
    out = br.derive_candidates(_corpus_run())
    out["candidates"][0]["relation_type"] = "quantum_entangles"
    assert br.validate_candidates(out)


def test_validator_rejects_uuid_leak() -> None:
    out = br.derive_candidates(_corpus_run())
    out["candidates"][0]["object"]["value"] = "550e8400-e29b-41d4-a716-446655440000"
    assert any("UUID" in e for e in br.validate_candidates(out))


def test_validator_rejects_admitted_state_and_governed_fields() -> None:
    out = br.derive_candidates(_corpus_run())
    out["candidates"][0]["state"] = "admitted_relation"
    assert br.validate_candidates(out)
    out2 = br.derive_candidates(_corpus_run())
    out2["candidates"][0]["governed_relation_id"] = "some-uuid"
    assert any("forbidden governed field" in e for e in br.validate_candidates(out2))


def test_validator_rejects_missing_evidence() -> None:
    out = br.derive_candidates(_corpus_run())
    out["candidates"][0]["evidence_refs"] = []
    assert br.validate_candidates(out)


# --------------------------------------------------------- integration


def test_candidates_from_registered_run_pass_s3_guard() -> None:
    """End-to-end: observations from an S3-registered run bridge cleanly."""
    import registry as rg

    src = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )
    snap = sp.snapshot_run(sp.build_run([src]), [src])
    reg = rg.RunRegistry()
    reg.register_run(snap)
    loaded = reg.load_run(snap["run"]["run_id"])  # integrity verified
    out = br.derive_candidates(loaded["run"]["observations"])
    assert br.validate_candidates(out) == []
    assert len(out["candidates"]) == 2  # 2 seed member_of (FKs defer as findings)
    # the S3 escalation guard would reject governed-identity smuggling; the
    # bridge output carries none
    rg.check_domain_identity(out)  # must not raise


def test_bridge_output_is_deterministic() -> None:
    a = br.derive_candidates(_corpus_run())
    b = br.derive_candidates(_corpus_run())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_disclaimer_and_ladder_present() -> None:
    out = br.derive_candidates(_corpus_run())
    assert out["admission_authority"] == "resolution-aspects"
    assert out["state_ladder"] == [
        "structural_fact", "relation_candidate", "evaluated_relation",
        "admitted_relation",
    ]
    assert "admission" in out["disclaimer"]


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
