"""Tests for the Ruling 32 §4 cross-runtime framing parity guard.

Fast tests are pure Python (matrix/contract invariants, canonicalisation,
version pins, the Python runtime). One end-to-end test runs the full guard
(Python + TypeScript + throwaway SQL) and is skipped when ``node``, ``tsc`` or
a reachable PostgreSQL is unavailable.

Run with::

    PYTHONPATH=python python3 -m pytest python/solscript_framing_parity -q
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from solscript_framing_parity import guard, matrix as matrix_module
from solscript_framing_parity.canonical import (
    Outcome,
    UNKNOWN_FRAME_DIMENSION,
    UNCLASSIFIED,
    classify_error,
)
from solscript_framing_parity.guard import compare
from solscript_framing_parity.matrix import FOLLOW_UPS, MATRIX, RUNTIMES
from solscript_framing_parity import python_runtime, sql_runtime, ts_runtime
from solscript_framing_parity.version_pins import (
    PINS_PATH,
    current_pin_values,
    load_pins,
    verify_pins,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

PLAN_CLAUSES = {
    "zero-required unframed",
    "supplementary absent context",
    "supplementary matching context",
    "supplementary contradicting context",
    "non-empty incomplete instance",
    "complete instance without context",
    "matching context",
    "mismatching context",
}


# ── Matrix / contract invariants ────────────────────────────────────────────
def test_matrix_covers_every_plan_clause() -> None:
    covered = {case.plan_clause for case in MATRIX}
    missing = PLAN_CLAUSES - covered
    assert not missing, f"matrix is missing plan clauses: {sorted(missing)}"


def test_case_ids_are_unique() -> None:
    ids = [case.id for case in MATRIX]
    assert len(ids) == len(set(ids))


def test_every_divergence_cites_an_open_follow_up() -> None:
    for case in MATRIX:
        if case.expectation.kind != "divergence":
            continue
        follow_up = case.expectation.follow_up
        assert follow_up in FOLLOW_UPS
        assert FOLLOW_UPS[follow_up]["status"] == "open"
        assert case.expectation.outcomes["python"] != case.expectation.outcomes["sql"]


def test_divergence_expectations_actually_diverge() -> None:
    for case in MATRIX:
        if case.expectation.kind != "divergence":
            continue
        keys = {outcome.comparison_key() for outcome in case.expectation.outcomes.values()}
        assert len(keys) > 1, case.id


def test_agreement_expectations_are_single_valued() -> None:
    for case in MATRIX:
        if case.expectation.kind != "agree":
            continue
        for runtime in RUNTIMES:
            assert case.expected_for(runtime) == case.expectation.outcome


def test_matrix_payload_is_json_serialisable_and_complete() -> None:
    payload = matrix_module.matrix_payload()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert len(decoded["cases"]) == len(MATRIX)
    assert set(decoded["dimensions"]) == set(matrix_module.DIMENSIONS)
    for case in decoded["cases"]:
        assert set(case) == {
            "id",
            "description",
            "required_dimensions",
            "instance_frames",
            "context",
        }


# ── Canonical outcomes ─────────────────────────────────────────────────────
def test_outcome_is_either_outcome_or_error() -> None:
    with pytest.raises(ValueError):
        Outcome(disposition="Asserted", error=UNKNOWN_FRAME_DIMENSION)


def test_error_classification_is_message_prefix_independent() -> None:
    assert (
        classify_error(
            "evaluate_proposition: context key 'bogus' names no known frame_dimension"
        )
        == UNKNOWN_FRAME_DIMENSION
    )
    assert (
        classify_error(
            "ERROR:  evaluate_proposition: context key bogus names no known frame_dimension"
        )
        == UNKNOWN_FRAME_DIMENSION
    )
    assert classify_error("something else entirely") == UNCLASSIFIED


def test_outcome_render_distinguishes_null_disposition() -> None:
    assert Outcome(None, False, "context_required").render() == "null/fail/context_required"
    assert Outcome("Asserted", True, "scoped").render() == "Asserted/pass/scoped"


# ── Comparison logic ───────────────────────────────────────────────────────
def _observations_from_contract() -> dict:
    return {
        runtime: {
            case.id: case.expected_for(runtime)
            for case in MATRIX
        }
        for runtime in RUNTIMES
    }


def test_compare_accepts_the_pinned_contract() -> None:
    verdicts = compare(_observations_from_contract())
    assert all(verdict.ok for verdict in verdicts)
    assert sum(1 for v in verdicts if v.verdict == "AGREE") == 6
    assert sum(1 for v in verdicts if v.verdict.startswith("DIVERGE")) == 5


def test_compare_fails_when_an_agreement_case_diverges() -> None:
    observations = _observations_from_contract()
    case = next(c for c in MATRIX if c.id == "zero_required_unframed")
    observations["sql"][case.id] = Outcome("Rejected", False, "not_scoped")
    verdict = next(v for v in compare(observations) if v.case.id == case.id)
    assert not verdict.ok
    assert verdict.verdict == "FAIL"


def test_compare_fails_when_a_divergence_case_silently_agrees() -> None:
    observations = _observations_from_contract()
    case = next(c for c in MATRIX if c.id == "incomplete_instance_no_context")
    for runtime in RUNTIMES:
        observations[runtime][case.id] = Outcome(None, False, "context_required")
    verdict = next(v for v in compare(observations) if v.case.id == case.id)
    assert not verdict.ok
    assert any("pinned as divergence" in problem for problem in verdict.problems)


# ── Python runtime ─────────────────────────────────────────────────────────
def test_python_runtime_matches_pinned_contract() -> None:
    observed = python_runtime.run_matrix(MATRIX, REPO_ROOT)
    for case in MATRIX:
        expected = case.expected_for("python")
        assert observed[case.id].comparison_key() == expected.comparison_key(), case.id


def test_python_runtime_is_isolated_per_case() -> None:
    first = python_runtime.run_matrix(MATRIX, REPO_ROOT)
    second = python_runtime.run_matrix(MATRIX, REPO_ROOT)
    assert {key: value.comparison_key() for key, value in first.items()} == {
        key: value.comparison_key() for key, value in second.items()
    }


# ── Version binding ────────────────────────────────────────────────────────
def test_version_pins_match_the_working_tree() -> None:
    assert verify_pins(REPO_ROOT) == []


def test_version_pins_cover_all_three_runtimes() -> None:
    pins = load_pins()
    assert set(pins["runtimes"]) == set(RUNTIMES)
    assert pins["runtimes"]["sql"]["later_migrations_verified_through"] == list(
        ["v33", "v34", "v35", "v36", "v37"]
    )
    assert "resolution_migration_v32.sql" in pins["runtimes"]["sql"]["gate_migration"]
    assert pins["sql_gate_sha256"]


def test_version_drift_is_detected_and_fails_loudly(tmp_path: Path) -> None:
    pins = load_pins()
    pins["pinned_files"]["python/SOLScript/solscript/interpreter.py"] = "0" * 64
    tampered = tmp_path / "version_pins.json"
    tampered.write_text(json.dumps(pins))

    drifts = verify_pins(REPO_ROOT, tampered)
    assert len(drifts) == 1
    assert drifts[0].path == "python/SOLScript/solscript/interpreter.py"
    assert drifts[0].expected == "0" * 64
    assert drifts[0].actual == current_pin_values(REPO_ROOT)["pinned_files"][
        "python/SOLScript/solscript/interpreter.py"
    ]


def test_main_returns_version_drift_exit_code_without_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = load_pins()
    pins["sql_gate_sha256"] = "f" * 64
    tampered = tmp_path / "version_pins.json"
    tampered.write_text(json.dumps(pins))
    monkeypatch.setattr(guard, "PINS_PATH", tampered)

    assert guard.main(["--repo-root", str(REPO_ROOT)]) == guard.EXIT_VERSION_DRIFT


def test_pins_file_is_tracked_next_to_the_package() -> None:
    assert PINS_PATH.is_file()
    assert PINS_PATH.parent.name == "solscript_framing_parity"


# ── End-to-end (hermetic) run ──────────────────────────────────────────────
def _sql_reachable() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        completed = sql_runtime._psql(("-d", "postgres", "-tAc", "SELECT 1"))
    except Exception:  # pragma: no cover - connection probe
        return False
    return completed.returncode == 0


requires_full_stack = pytest.mark.skipif(
    not (shutil.which("node") and shutil.which("tsc") and _sql_reachable()),
    reason="node/tsc/PostgreSQL unavailable — full parity guard cannot run",
)


@requires_full_stack
def test_full_guard_runs_hermetically_and_holds_the_contract() -> None:
    report = guard.run_guard(REPO_ROOT)
    assert report.runtime_errors == {}
    assert report.failures == 0
    assert report.exit_code == guard.EXIT_OK
    assert len(report.cases) == len(MATRIX)
    assert all(verdict.ok for verdict in report.cases)


@requires_full_stack
def test_full_guard_leaves_no_throwaway_database() -> None:
    guard.run_guard(REPO_ROOT)
    completed = sql_runtime._psql(
        (
            "-d",
            "postgres",
            "-tAc",
            "SELECT count(*) FROM pg_database "
            f"WHERE datname LIKE '{sql_runtime.DB_PREFIX}%'",
        )
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "0"


@requires_full_stack
def test_json_report_declares_hermeticity_and_follow_ups() -> None:
    report = guard.run_guard(REPO_ROOT).to_json()
    assert report["ok"] is True
    assert report["hermetic"]["live_database_touched"] is False
    assert report["hermetic"]["migrations_applied"] is False
    assert report["hermetic"]["admission_or_production_change"] is False
    assert set(report["follow_ups"]) == set(FOLLOW_UPS)
    assert report["summary"] == {
        "cases": len(MATRIX),
        "agreement_cases": 6,
        "divergence_cases": 5,
        "failures": 0,
    }
