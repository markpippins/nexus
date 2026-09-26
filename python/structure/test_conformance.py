#!/usr/bin/env python3
"""Reference-runtime tests for the conformance engine (Structure S6).

Pins the vectors file lifecycle: byte-stable regeneration, self-verification
in CPython, the machine-readable verdict (deterministic key set, verdict_hash
over the results alone), the cross-runtime hash parity clause — the pinned
expected_verdict_hash is what a second runtime must reproduce — and the
tamper-evidence of the whole file. Dual-runnable: pytest-compatible
functions plus `python3 python/structure/test_conformance.py`
(bin/tests/test_merge_pr.py convention).
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import conformance as conf  # noqa: E402
import contract as sc  # noqa: E402

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "conformance_vectors.json"
)


def _load_vectors() -> dict:
    return conf.load_vectors(str(VECTORS_PATH))


# ------------------------------------------------------------- generation


def test_vectors_regenerate_byte_stable() -> None:
    """The committed vectors are exactly what build_vectors() produces."""
    vectors = _load_vectors()
    assert conf.build_vectors() == vectors


def test_vectors_file_covers_the_spec_cases() -> None:
    """Replay, tampered-source, parser-drift, unsupported-syntax, and
    authority-escalation are all represented (to-do thread 18e17032)."""
    vectors = _load_vectors()
    kinds = {v["kind"] for v in vectors["vectors"]}
    assert {"machine", "invalid_observation"} <= kinds
    machine = [v for v in vectors["vectors"] if v["kind"] == "machine"]
    # tampered-source: the machine vectors pin text + content_hash pairs
    assert all("text" in v["inputs"] and "content_hash" in v["inputs"] for v in machine)
    # parser-drift: observations carry the pinned grammar/identity
    assert all(v["inputs"]["grammar_revision"] for v in machine)
    # unsupported-syntax: one machine vector demands unsupported diagnostics
    assert any(
        v["checks"].get("expected_unsupported") for v in machine if "checks" in v
    )
    # authority-escalation: the invalid_observation vector demands rejection
    invalid = [v for v in vectors["vectors"] if v["kind"] == "invalid_observation"]
    assert any(
        v["inputs"].get("must_reject_with") == "authority_status" for v in invalid
    )


def test_vectors_carry_manifest_and_expected_hash() -> None:
    """The file is self-contained: a second runtime re-derives the contract
    fingerprint from the embedded manifest alone and pins its verdict against
    expected_verdict_hash — no Python involvement required."""
    vectors = _load_vectors()
    assert vectors["contract_manifest"] == sc.contract_manifest()
    assert vectors["contract_fingerprint"] == sc.contract_fingerprint()
    digest = hashlib.sha256(
        json.dumps(
            {
                "suite_version": conf.CONFORMANCE_SUITE_VERSION,
                "vector_results": [
                    {
                        "name": v["name"],
                        "kind": v["kind"],
                        "expectation": v["expectation"],
                        "ok": True,
                    }
                    for v in vectors["vectors"]
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert vectors["expected_verdict_hash"] == digest


# ------------------------------------------------------------ verification


def test_reference_runtime_verifies_own_vectors() -> None:
    vectors = _load_vectors()
    verdict = conf.verify_vectors(vectors)
    assert all(r["ok"] for r in verdict["vector_results"])
    assert len(verdict["vector_results"]) == len(vectors["vectors"])


def test_verdict_is_machine_readable_and_hash_pins_results() -> None:
    vectors = _load_vectors()
    verdict = conf.verify_vectors(vectors, implementation="probe-impl")
    assert verdict["implementation"] == "probe-impl"
    assert set(verdict) == {
        "suite_version",
        "vector_file",
        "implementation",
        "contract",
        "vector_results",
        "verdict_hash",
    }
    assert verdict["verdict_hash"] == vectors["expected_verdict_hash"]
    # the hash covers the results alone: implementation identity is NOT hashed
    other = conf.verify_vectors(vectors, implementation="another-impl")
    assert other["verdict_hash"] == verdict["verdict_hash"]


def test_failing_vector_changes_verdict_hash() -> None:
    vectors = _load_vectors()
    corrupted = copy.deepcopy(vectors)
    sfid = next(v for v in corrupted["vectors"] if v["kind"] == "source_fact_id")
    sfid["expected"] = "0" * 64
    verdict = conf.verify_vectors(corrupted)
    assert not all(r["ok"] for r in verdict["vector_results"])
    assert verdict["verdict_hash"] != vectors["expected_verdict_hash"]


def test_tampered_source_is_detected() -> None:
    """Mutation of the pinned text must fail the machine vector."""
    vectors = _load_vectors()
    tampered = copy.deepcopy(vectors)
    machine = next(v for v in tampered["vectors"] if v["kind"] == "machine")
    machine["inputs"]["text"] += "\n-- tampered\n"
    verdict = conf.verify_vectors(tampered)
    hit = next(r for r in verdict["vector_results"] if not r["ok"])
    assert "content_hash" in hit["detail"]


def test_grammar_drift_is_detected_as_parser_drift() -> None:
    """An observation whose grammar revision departs from the pinned one is
    a drift finding, not silently accepted output. Drift is injected into the
    PINNED revision (the observation mutations that would also break the
    observation_id re-derivation are covered by the identity chain already)."""
    vectors = _load_vectors()
    drifted = copy.deepcopy(vectors)
    machine = next(v for v in drifted["vectors"] if v["kind"] == "machine")
    machine["inputs"]["grammar_revision"] = "sql-ddl-v9.9.9"
    verdict = conf.verify_vectors(drifted)
    hit = next(r for r in verdict["vector_results"] if not r["ok"])
    assert "parser drift" in hit["detail"]


def test_malformed_vectors_are_rejected() -> None:
    vectors = _load_vectors()
    for bad in (
        {**vectors, "schema_version": 99},
        {**vectors, "vectors": []},
        {**vectors, "contract_revision": None},
        [{**vectors}],
    ):
        try:
            conf.verify_vectors(bad)
        except conf.ConformanceError:
            pass
        else:
            raise AssertionError(f"malformed vectors accepted: {bad!r}")


# ------------------------------------------------------------ cross-runtime


def test_second_runtime_reproduces_verdict_hash() -> None:
    """The cross-runtime proof: the Node/TypeScript implementation, run as a
    subprocess, verifies the same vectors and produces the identical
    verdict_hash — the S1 contract is implementable outside CPython."""
    ts_pkg = (
        pathlib.Path(__file__).resolve().parents[2] / "typescript" / "structure-conformance"
    )
    cli = ts_pkg / "src" / "cli.js"
    if not cli.exists():
        raise AssertionError(f"second runtime CLI missing: {cli}")
    proc = subprocess.run(
        [
            "node",
            str(cli),
            "--verify",
            str(VECTORS_PATH),
            "--implementation",
            "node-typescript-subprocess",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"second runtime failed: {proc.stderr}"
    verdict = json.loads(proc.stdout)
    assert verdict["implementation"] == "node-typescript-subprocess"
    assert verdict["verdict_hash"] == _load_vectors()["expected_verdict_hash"]


def test_second_runtime_rejects_tampered_vectors() -> None:
    """The verifier must catch drift, not merely pass good input: the Node
    runtime must also FAIL on a tampered vectors file."""
    vectors = copy.deepcopy(_load_vectors())
    machine = next(v for v in vectors["vectors"] if v["kind"] == "machine")
    machine["inputs"]["text"] += "\n-- tampered\n"
    tampered_path = pathlib.Path("/tmp/s6_tampered_vectors.json")
    tampered_path.write_text(json.dumps(vectors), encoding="utf-8")
    ts_pkg = (
        pathlib.Path(__file__).resolve().parents[2] / "typescript" / "structure-conformance"
    )
    proc = subprocess.run(
        ["node", str(ts_pkg / "src" / "cli.js"), "--verify", str(tampered_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0, "second runtime accepted tampered vectors"


if __name__ == "__main__":
    failures = 0
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
