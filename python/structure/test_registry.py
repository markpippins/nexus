#!/usr/bin/env python3
"""Conformance tests for the S3 run registry (Structure S3).

Pins to-do thread 904c2694's acceptance criteria: repeated runs are
byte-stable; source/parser/grammar changes are detectable; identity
collisions and stale source revisions fail closed; fact identity is never
confused with referenced domain identity (no silent name→UUID mapping).
Dual-runnable: pytest-compatible functions plus
`python3 python/structure/test_registry.py`.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import contract as sc  # noqa: E402
import registry as rg  # noqa: E402
import sql_parser as sp  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "python" / "structure" / "fixtures" / "sql" / "V178__role_memory.sql"


def _snapshot() -> dict:
    src = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )
    return sp.snapshot_run(sp.build_run([src]), [src])


# --------------------------------------------------------- persistence


def test_register_run_is_idempotent_by_identity() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    e1 = reg.register_run(snap)
    e2 = reg.register_run(snap)
    assert e1.run_id == e2.run_id
    assert e1.snapshot_hash == e2.snapshot_hash
    assert len(reg.index()) == 1


def test_registered_run_loads_with_verified_replay() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    reg.register_run(snap)
    loaded = reg.load_run(snap["run"]["run_id"])
    assert loaded["snapshot_hash"] == snap["snapshot_hash"]
    assert loaded["run"]["run_id"] == snap["run"]["run_id"]
    assert reg.attestations(snap["run"]["run_id"])[0]["byte_stable"] is True


def test_in_memory_and_jsonl_stores_agree() -> None:
    snap = _snapshot()
    mem = rg.RunRegistry()
    mem.register_run(snap)
    with tempfile.TemporaryDirectory() as td:
        jl = rg.RunRegistry(store=rg.JsonlRunStore(pathlib.Path(td) / "runs.jsonl"))
        jl.register_run(snap)
        assert [e.run_id for e in mem.index()] == [e.run_id for e in jl.index()]
        loaded = jl.load_run(snap["run"]["run_id"])
        assert loaded["snapshot_hash"] == snap["snapshot_hash"]
        # re-registration on a fresh registry over the same file is a no-op
        jl2 = rg.RunRegistry(store=rg.JsonlRunStore(pathlib.Path(td) / "runs.jsonl"))
        jl2.register_run(snap)
        assert len(jl2.index()) == 1


def test_jsonl_store_survives_reopening() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "runs.jsonl"
        snap = _snapshot()
        rg.RunRegistry(store=rg.JsonlRunStore(path)).register_run(snap)
        rg.RunRegistry(store=rg.JsonlRunStore(path)).register_run(snap)
        reopened = rg.RunRegistry(store=rg.JsonlRunStore(path))
        assert len(reopened.index()) == 1


# --------------------------------------------------------- fail-closed


def test_identity_collision_fails_closed() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    reg.register_run(snap)
    tampered = copy.deepcopy(snap)
    # same run_id, different content: drop an observation
    tampered["run"]["observations"] = tampered["run"]["observations"][:-1]
    try:
        reg.register_run(tampered)
    except rg.IdentityCollision:
        return
    raise AssertionError("same run_id with different content must fail closed")


def test_tampered_persisted_record_fails_on_load() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "runs.jsonl"
        snap = _snapshot()
        rg.RunRegistry(store=rg.JsonlRunStore(path)).register_run(snap)
        lines = path.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["snapshot"]["run"]["observations"] = rec["snapshot"]["run"]["observations"][:-1]
        lines[0] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")
        reg = rg.RunRegistry(store=rg.JsonlRunStore(path))
        try:
            reg.load_run(snap["run"]["run_id"])
        except rg.ReplayFailure:
            return
        raise AssertionError("tampered persisted record must fail closed on load")


def test_store_level_collision_detected_across_lines() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "runs.jsonl"
        snap = _snapshot()
        store = rg.JsonlRunStore(path)
        reg = rg.RunRegistry(store=store)
        reg.register_run(snap)
        # append a same-run_id different-hash line directly (simulating drift)
        rec = json.loads(path.read_text().splitlines()[0])
        rec["snapshot_hash"] = "f" * 64
        with path.open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            store.read_record(snap["run"]["run_id"])
        except rg.IdentityCollision:
            return
        raise AssertionError("store must detect conflicting lines for one run_id")


def test_stale_source_revision_fails_closed() -> None:
    src = sp.SqlSource(
        source_uri="db/migrations/V178__role_memory.sql",
        revision="d46733ba",
        text=CORPUS.read_text(),
    )
    snap = sp.snapshot_run(sp.build_run([src]), [src])
    reg = rg.RunRegistry()
    # pin the verified hash of the current bytes, then parse mutated bytes
    reg.pin_source_revision(
        src.source_uri,
        __import__("hashlib").sha256(src.text.encode()).hexdigest(),
    )
    mutated = sp.SqlSource(
        source_uri=src.source_uri,
        revision=src.revision,
        text=src.text.replace("role_memory", "role_memory2"),
    )
    snap2 = sp.snapshot_run(sp.build_run([mutated]), [mutated])
    try:
        reg.register_run(snap2)
    except rg.StaleSourceRevision:
        return
    raise AssertionError("stale source revision must fail closed")


def test_identity_escalation_rejected() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    tampered = copy.deepcopy(snap)
    tampered["run"]["observations"][0]["governed_tag_id"] = "some-uuid"
    try:
        reg.register_run(tampered)
    except rg.IdentityEscalation:
        return
    raise AssertionError("governed identity smuggling must be rejected")


def test_mapped_without_evidence_is_silent_mapping_rejected() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    tampered = copy.deepcopy(snap)
    fk = next(
        o for o in tampered["run"]["observations"]
        if o["fact_kind"] == "foreign_key"
    )
    fk["relation_mapping"] = {"status": "mapped", "governed_relation_id": "r1"}
    try:
        reg.register_run(tampered)
    except rg.IdentityEscalation:
        return
    raise AssertionError("mapped-without-evidence is a silent mapping and must fail")


def test_mapped_with_evidence_passes_domain_guard() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    fk = next(
        o for o in snap["run"]["observations"] if o["fact_kind"] == "foreign_key"
    )
    fk["relation_mapping"] = {
        "status": "mapped",
        "governed_relation_id": "rel-uuid",
        "evidence_refs": ["V182 relation vocabulary row 7"],
    }
    entry = reg.register_run(snap, verify_replay=False)
    assert entry.observation_count == len(snap["run"]["observations"])


# --------------------------------------------------------- change detection


def test_parser_change_is_detectable_as_new_identity() -> None:
    snap_a = _snapshot()
    original = sp.PARSER_REVISION
    try:
        sp.PARSER_REVISION = "v0.3.0"
        snap_b = _snapshot()
    finally:
        sp.PARSER_REVISION = original
    assert snap_a["run"]["run_id"] != snap_b["run"]["run_id"]
    assert snap_a["snapshot_hash"] != snap_b["snapshot_hash"]
    reg = rg.RunRegistry()
    e_a = reg.register_run(snap_a, verify_replay=False)
    e_b = reg.register_run(snap_b, verify_replay=False)
    assert e_a.run_id != e_b.run_id
    assert len(reg.index()) == 2


def test_grammar_change_is_detectable_and_shares_location_identity() -> None:
    snap_a = _snapshot()
    original = sp.GRAMMAR_REVISION
    try:
        sp.GRAMMAR_REVISION = "sql-ddl-v0.9.0"
        snap_b = _snapshot()
    finally:
        sp.GRAMMAR_REVISION = original
    assert snap_a["run"]["run_id"] != snap_b["run"]["run_id"]
    sfids_a = sorted(o["source_fact_id"] for o in snap_a["run"]["observations"])
    sfids_b = sorted(o["source_fact_id"] for o in snap_b["run"]["observations"])
    assert sfids_a == sfids_b, "location identity survives grammar changes"


def test_repeated_runs_byte_stable_cross_process() -> None:
    code = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "from structure import sql_parser as sp\n"
        "src = sp.SqlSource(source_uri='db/migrations/V178__role_memory.sql',\n"
        "                   revision='d46733ba',\n"
        "                   text=open(%r, encoding='utf-8').read())\n"
        "snap = sp.snapshot_run(sp.build_run([src]), [src])\n"
        "print(snap['run']['run_id'], snap['snapshot_hash'])\n"
    ) % (str(REPO_ROOT / "python"), str(CORPUS))
    outs = []
    for seed in ("7", "99"):
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env=dict(os.environ, PYTHONHASHSEED=seed), timeout=120, check=True,
        )
        outs.append(proc.stdout.split())
    assert outs[0] == outs[1], f"run identity diverges across hash seeds: {outs}"


# --------------------------------------------------------- provenance


def test_replay_attestations_are_append_only() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    run_id = snap["run"]["run_id"]
    reg.register_run(snap, verify_replay=False)
    r1 = sp.replay(snap)
    reg.append_replay_attestation(run_id, r1, verifier="verifier-a")
    r2 = sp.replay(snap)
    reg.append_replay_attestation(run_id, r2, verifier="verifier-b")
    atts = reg.attestations(run_id)
    assert len(atts) == 2
    assert atts[0]["verifier"] == "verifier-a"
    assert atts[1]["verifier"] == "verifier-b"
    assert all(a["byte_stable"] is True for a in atts)
    # second registration of the same snapshot appends another attestation
    reg.register_run(snap)
    assert len(reg.attestations(run_id)) == 3


def test_attestation_unknown_run_fails() -> None:
    reg = rg.RunRegistry()
    try:
        reg.append_replay_attestation("nope", {"byte_stable": True})
    except rg.RegistryError:
        return
    raise AssertionError("attestation for unknown run must fail")


def test_lookup_by_snapshot_hash() -> None:
    reg = rg.RunRegistry()
    snap = _snapshot()
    reg.register_run(snap)
    assert reg.lookup_by_snapshot_hash(snap["snapshot_hash"]) == snap["run"]["run_id"]
    assert reg.lookup_by_snapshot_hash("0" * 64) is None


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
