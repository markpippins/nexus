#!/usr/bin/env python3
"""Conformance tests for the Structure contract (Structure S1).

Pins the architect ruling on the AST-parsing thread (record 143b0e04) at
test level: the two-identity scheme (source_fact_id vs observation_id),
changed-grammar ⇒ new observation population, parse honesty (partial/
unsupported/failed must carry diagnostics — absence never asserts
non-existence), the governed relation-mapping slot, manifest/TypeSpec/JSON
parity, and the producer-not-authority boundary. Dual-runnable:
pytest-compatible functions plus `python3 python/structure/test_contract.py`
(bin/tests/test_merge_pr.py convention).
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import contract as sc  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TSP_PATH = REPO_ROOT / "typespec" / "v1" / "structure" / "main.tsp"
MANIFEST_JSON_PATH = (
    REPO_ROOT / "typespec" / "v1" / "structure" / "contract-manifest.json"
)
FIXTURES = REPO_ROOT / "python" / "structure" / "fixtures"

PARSER = {
    "parser_identity": "structure-sql-parser",
    "parser_revision": "v0.1.0",
    "grammar_revision": "v0.1.0",
}


# ---------------------------------------------------------------- fixtures


def _source(**over: object) -> dict:
    base = {
        "source_uri": "db/migrations/V178__role_memory.sql",
        "revision": "5b06e287",
        "content_hash": "a" * 64,
        "language": "sql",
        "source_kind": "sql_migration",
    }
    base.update(over)
    return base


def _read_set() -> list:
    return [
        {
            "source_uri": "db/migrations/V178__role_memory.sql",
            "revision": "5b06e287",
            "content_hash": "a" * 64,
            "role": "migration",
        }
    ]


def _sfid(**over: object) -> str:
    kwargs = dict(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="5b06e287",
        content_hash="a" * 64,
        node_path="ddl[0].create_table.role_memory",
        fact_kind="table",
    )
    kwargs.update(over)
    return sc.source_fact_id_v1(**kwargs)


def _observation(**over: object) -> dict:
    sfid = _sfid()
    base = {
        "observation_id": sc.observation_id_v1(
            source_fact_id=sfid,
            read_set_fingerprint=sc.read_set_fingerprint(_read_set()),
            payload={"name": "role_memory"},
            **PARSER,
        ),
        "source_fact_id": sfid,
        "source": _source(),
        "parser": dict(PARSER),
        "anchor": {"node_path": "ddl[0].create_table.role_memory"},
        "fact_kind": "table",
        "payload": {"name": "role_memory"},
        "parse_status": "complete",
        "read_set_fingerprint": sc.read_set_fingerprint(_read_set()),
        "authority_status": sc.AUTHORITY_STATUS,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------- tests


def test_manifest_fingerprint_deterministic() -> None:
    a, b = sc.contract_fingerprint(), sc.contract_fingerprint()
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a)


def test_manifest_keys_pinned() -> None:
    m = sc.contract_manifest()
    assert m["contract_revision"] == "structure-v0.1"
    assert m["fingerprint_version"] == 1
    assert m["owner"] == "structure"
    assert m["authority_status"] == "non_authoritative"
    assert m["source_fact_id_version"] == 1
    assert m["observation_id_version"] == 1
    assert m["read_set_fingerprint_algorithm"] == "sha256-canonical-json-v1"
    assert m["parse_statuses"] == sc.PARSE_STATUSES
    assert m["relation_mapping_statuses"] == sc.RELATION_MAPPING_STATUSES
    assert m["authority_boundary"]["structure_is"] == "reader"
    assert m["authority_boundary"]["absence_never_asserts_nonexistence"] is True
    assert m["authority_boundary"]["state_boundary"] == [
        "structural_fact",
        "relation_candidate",
        "evaluated_relation",
        "admitted_relation",
    ]


def test_vocabularies_sorted_and_bounded() -> None:
    for vocab in (
        sc.SOURCE_KINDS,
        sc.FACT_KINDS,
        sc.PARSE_STATUSES,
        sc.RELATION_MAPPING_STATUSES,
        sc.CANDIDATE_STATUSES,
        sc.FINDING_CODES,
    ):
        assert vocab == sorted(vocab), "vocabularies must be sorted for canonical JSON"
        assert len(vocab) == len(set(vocab)), "vocabularies must have no duplicates"
    assert "unknown" in sc.SOURCE_KINDS
    assert "authority_escalation_denied" in sc.FINDING_CODES


def test_manifest_json_matches_python() -> None:
    file_manifest = json.loads(MANIFEST_JSON_PATH.read_text())
    py = sc.contract_manifest()
    for key, value in py.items():
        assert file_manifest.get(key) == value, f"JSON/Python mismatch on {key!r}"
    fp = file_manifest.get("contract_fingerprint", "")
    assert re.fullmatch(r"[0-9a-f]{64}", fp), "manifest JSON must pin a real fingerprint"
    assert fp == sc.contract_fingerprint(), "manifest fingerprint must equal the live one"


def test_typespec_declares_every_vocabulary_term() -> None:
    tsp = TSP_PATH.read_text()
    for term in (
        sc.SOURCE_KINDS
        + sc.FACT_KINDS
        + sc.PARSE_STATUSES
        + sc.RELATION_MAPPING_STATUSES
        + sc.CANDIDATE_STATUSES
        + sc.FINDING_CODES
    ):
        assert f'"{term}"' in tsp, f"TypeSpec contract missing term {term!r}"
    for model in (
        "StructureSource",
        "ParserProvenance",
        "NodeSpan",
        "NodeAnchor",
        "ParseDiagnostic",
        "GovernedRelationMapping",
        "StructuralObservation",
        "UnresolvedCandidate",
        "StructureFinding",
        "ReadSetEntry",
        "StructureRun",
        "StructureContractManifest",
    ):
        assert re.search(rf"^model {model}\b", tsp, re.M), f"missing model {model}"


def test_typespec_states_ruling_doctrine() -> None:
    tsp = TSP_PATH.read_text()
    tsp = re.sub(r"(?m)^\s*\*\s?", " ", tsp)  # strip doc-comment line markers
    tsp = re.sub(r"\s+", " ", tsp)
    for term in (
        "producer, not an authority",
        "never grant governed identity",
        "never self-authorize relations",
        "changed grammar revision creates a new observation population",
        "never turn absence of a fact into an assertion that the construct does not exist",
        "structural_fact ≠ relation_candidate ≠ evaluated_relation ≠ admitted_relation",
        "must not invent free-form predicates",
    ):
        assert term in tsp, f"TypeSpec must state the ruling term {term!r}"


def test_source_fact_id_is_parser_independent_location_identity() -> None:
    a = _sfid()
    assert a == _sfid()  # deterministic
    # Discriminates on every identity input.
    assert a != _sfid(node_path="ddl[0].create_table.other")
    assert a != _sfid(fact_kind="column")
    assert a != _sfid(revision="other-rev")
    assert a != _sfid(content_hash="b" * 64)
    assert a != _sfid(source_uri="db/seeds/tags.sql")
    for bad in (
        {"content_hash": ""},
        {"content_hash": "zz"},
        {"node_path": ""},
        {"fact_kind": "quantum_field"},
    ):
        kwargs = dict(
            source_uri="s", revision="r", content_hash="a" * 64,
            node_path="n", fact_kind="table",
        )
        kwargs.update(bad)
        try:
            sc.source_fact_id_v1(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"source_fact_id_v1 accepted bad input {bad!r}")


def test_observation_id_pins_parser_and_grammar() -> None:
    sfid = _sfid()
    rsf = sc.read_set_fingerprint(_read_set())
    kwargs = dict(
        source_fact_id=sfid,
        read_set_fingerprint=rsf,
        payload={"name": "role_memory"},
        **PARSER,
    )
    assert sc.observation_id_v1(**kwargs) == sc.observation_id_v1(**kwargs)

    # The ruling's acceptance property: a changed grammar revision yields a
    # NEW observation population over the same source fact.
    assert sc.observation_id_v1(**kwargs) != sc.observation_id_v1(
        **{**kwargs, "grammar_revision": "v0.2.0"}
    )
    assert sc.observation_id_v1(**kwargs) != sc.observation_id_v1(
        **{**kwargs, "parser_identity": "other-parser"}
    )
    assert sc.observation_id_v1(**kwargs) != sc.observation_id_v1(
        **{**kwargs, "parser_revision": "v0.1.1"}
    )
    assert sc.observation_id_v1(**kwargs) != sc.observation_id_v1(
        **{**kwargs, "payload": {"name": "other"}}
    )
    assert sc.observation_id_v1(**kwargs) != sc.observation_id_v1(
        **{**kwargs, "read_set_fingerprint": "c" * 64}
    )
    # Payload canonicalization is key-order insensitive.
    assert sc.observation_id_v1(**kwargs) == sc.observation_id_v1(
        **{**kwargs, "payload": {"name": "role_memory"}}
    )


def test_read_set_fingerprint_order_insensitive() -> None:
    rs = [
        {"source_uri": "db/migrations/V1__a.sql", "revision": "r1",
         "content_hash": "b" * 64, "role": "migration"},
        {"source_uri": "db/seeds/tags.sql", "revision": "r2",
         "content_hash": "c" * 64, "role": "seed"},
    ]
    assert sc.read_set_fingerprint(rs) == sc.read_set_fingerprint(list(reversed(rs)))
    touched = [dict(rs[0]), dict(rs[1])]
    touched[0]["content_hash"] = "d" * 64
    assert sc.read_set_fingerprint(rs) != sc.read_set_fingerprint(touched)


def test_validator_accepts_wellformed_observation() -> None:
    assert sc.validate_structural_observation(_observation()) == []


def test_validator_rejects_authority_escalation() -> None:
    errs = sc.validate_structural_observation(
        _observation(authority_status="authoritative")
    )
    assert any("authority_status" in e for e in errs)


def test_validator_enforces_parse_honesty() -> None:
    # Partial without diagnostics is the forbidden lie: absence pretending
    # to be knowledge.
    errs = sc.validate_structural_observation(_observation(parse_status="partial"))
    assert any("diagnostics" in e for e in errs)
    assert any("never" in e for e in errs)
    for status in ("partial", "unsupported", "failed"):
        errs = sc.validate_structural_observation(
            _observation(
                parse_status=status,
                diagnostics=[{"code": "unsupported_syntax", "message": "x"}],
            )
        )
        assert not any("diagnostics" in e for e in errs), status
    assert sc.validate_structural_observation(
        _observation(parse_status="nonsense")
    ) != []


def test_validator_enforces_governed_mapping_shape() -> None:
    base = _observation()
    assert sc.validate_structural_observation(base) == []  # absent = not attempted
    assert sc.validate_structural_observation(
        _observation(relation_mapping={"status": "unmapped"})
    ) == []
    errs = sc.validate_structural_observation(
        _observation(relation_mapping={"status": "mapped"})
    )
    assert any("governed_relation_id" in e for e in errs)
    assert any("evidence_refs" in e for e in errs)
    assert sc.validate_structural_observation(
        _observation(
            relation_mapping={
                "status": "mapped",
                "governed_relation_id": "rel-uuid",
                "evidence_refs": ["obs:whatever"],
            }
        )
    ) == []
    assert sc.validate_structural_observation(
        _observation(relation_mapping={"status": "invented"})
    ) != []


def test_validator_requires_two_identities_and_provenance() -> None:
    o = _observation()
    o["observation_id"] = "not-hex"
    assert any("observation_id" in e for e in sc.validate_structural_observation(o))
    o = _observation()
    del o["source_fact_id"]
    assert any("source_fact_id" in e for e in sc.validate_structural_observation(o))
    o = _observation()
    del o["parser"]["grammar_revision"]
    assert any("parser" in e for e in sc.validate_structural_observation(o))
    o = _observation(anchor={})
    assert any("node_path" in e for e in sc.validate_structural_observation(o))
    o = _observation(read_set_fingerprint="nope")
    assert any("read_set_fingerprint" in e for e in sc.validate_structural_observation(o))
    assert len(sc.validate_structural_observation({})) >= 10


def test_fixtures_conform() -> None:
    obs_fx = json.loads((FIXTURES / "wellformed_table_observation.json").read_text())
    obs = obs_fx["observation"]
    assert sc.validate_structural_observation(obs) == []
    # Both identities reproducible from the fixture's own fields (no magic).
    src, par, anc = obs["source"], obs["parser"], obs["anchor"]
    assert obs["source_fact_id"] == sc.source_fact_id_v1(
        source_uri=src["source_uri"],
        revision=src["revision"],
        content_hash=src["content_hash"],
        node_path=anc["node_path"],
        fact_kind=obs["fact_kind"],
    )
    assert obs["observation_id"] == sc.observation_id_v1(
        source_fact_id=obs["source_fact_id"],
        read_set_fingerprint=obs["read_set_fingerprint"],
        payload=obs["payload"],
        **{k: par[k] for k in ("parser_identity", "parser_revision", "grammar_revision")},
    )

    partial_fx = json.loads(
        (FIXTURES / "partial_parse_honesty.json").read_text()
    )
    partial = partial_fx["observation"]
    assert partial["parse_status"] == "partial"
    assert partial["diagnostics"], "honesty fixture must carry diagnostics"
    assert sc.validate_structural_observation(partial) == []
    # The dishonest twin — same observation with diagnostics stripped — must
    # be rejected: absence of facts never becomes an existence assertion.
    dishonest = copy.deepcopy(partial)
    del dishonest["diagnostics"]
    assert sc.validate_structural_observation(dishonest) != []

    cand_fx = json.loads((FIXTURES / "ambiguous_candidate.json").read_text())
    cand = cand_fx["unresolved_candidate"]
    assert cand["reason"] in sc.CANDIDATE_STATUSES
    assert re.fullmatch(r"[0-9a-f]{64}", cand["candidate_id"])


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
