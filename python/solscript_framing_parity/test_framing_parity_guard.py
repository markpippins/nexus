"""Tests for the Ruling 32 §4 cross-runtime framing parity guard.

Fast tests are pure Python (matrix/contract invariants, canonicalisation,
version pins and gate discovery, the Python runtime, failure-mode wiring). One
end-to-end test runs the full guard (Python + TypeScript + throwaway SQL) and is
skipped when ``node``, ``tsc`` or a reachable PostgreSQL is unavailable.

Run with::

    PYTHONPATH=python python3 -m pytest python/solscript_framing_parity -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from solscript_framing_parity import guard, matrix as matrix_module, version_pins
from solscript_framing_parity.canonical import (
    Outcome,
    RuntimeUnavailable,
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

import sys
_GUARD_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools, missing_tools, skip_reason  # noqa: E402

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


def _completed(
    returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["psql"], returncode=returncode, stdout=stdout, stderr=stderr
    )


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


def test_compare_catches_drift_within_a_pinned_divergence() -> None:
    """A row can drift and still diverge: only the per-runtime expectation sees it."""
    observations = _observations_from_contract()
    case = next(c for c in MATRIX if c.id == "supplementary_absent_context")
    observations["sql"][case.id] = Outcome("Rejected", False, "context_required")

    verdicts = next(v for v in compare(observations) if v.case.id == case.id)
    # Still three distinct outcomes, so the divergence check alone stays quiet.
    assert len(
        {observations[runtime][case.id].comparison_key() for runtime in RUNTIMES}
    ) == 3
    assert not verdicts.ok
    assert verdicts.problems == [
        "sql: expected null/fail/context_required, observed Rejected/fail/context_required"
    ]


# ── Unavailable vs failed runtimes ─────────────────────────────────────────
def _stub_runtimes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unavailable: tuple = (),
    broken: tuple = (),
) -> None:
    """Replace all three runtimes so guard wiring can be tested without them."""

    def contract_outcomes(runtime: str) -> dict:
        return {case.id: case.expected_for(runtime) for case in MATRIX}

    for runtime, module in (
        ("python", python_runtime),
        ("typescript", ts_runtime),
        ("sql", sql_runtime),
    ):
        if runtime in unavailable:
            def raise_unavailable(*args, _runtime=runtime, **kwargs):
                raise RuntimeUnavailable(f"{_runtime} toolchain missing")

            monkeypatch.setattr(module, "run_matrix", raise_unavailable)
        elif runtime in broken:
            def raise_broken(*args, _runtime=runtime, **kwargs):
                raise ValueError(f"{_runtime} harness exploded")

            monkeypatch.setattr(module, "run_matrix", raise_broken)
        else:
            monkeypatch.setattr(
                module,
                "run_matrix",
                lambda *args, _runtime=runtime, **kwargs: contract_outcomes(_runtime),
            )


def test_unavailable_runtime_is_not_attested_and_exits_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtimes(monkeypatch, unavailable=("sql",))

    report = guard.run_guard(REPO_ROOT, pins_path=PINS_PATH)

    assert set(report.runtime_errors) == {"sql"}
    assert report.unavailable_runtimes == ["sql"]
    assert report.failures == 0
    assert report.not_attested_cases == len(MATRIX)
    assert all(verdict.verdict == "NOT_ATTESTED" for verdict in report.cases)
    assert report.exit_code == guard.EXIT_RUNTIME_UNAVAILABLE

    payload = report.to_json()
    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert payload["unavailable_runtimes"] == ["sql"]
    assert payload["summary"]["not_attested_cases"] == len(MATRIX)


def test_a_crashed_runtime_is_a_parity_failure_not_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_runtimes(monkeypatch, broken=("python",))

    report = guard.run_guard(REPO_ROOT, pins_path=PINS_PATH)

    assert report.unavailable_runtimes == []
    assert report.failures >= 1
    assert report.exit_code == guard.EXIT_PARITY_FAILURE


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


# ── TypeScript compile command ─────────────────────────────────────────────
def test_compile_command_passes_no_module_resolution_flag() -> None:
    """Naming a resolution mode excludes a supported compiler.

    ``node`` (node10) was removed in TypeScript 7; ``bundler`` does not exist
    before 5.0. The sources import each other with explicit ``.js``
    specifiers, so the compiler default is correct on 4.9 through 7.
    """
    command = ts_runtime.compile_command(
        "tsc", Path("/tmp/src/interpreter.ts"), Path("/tmp/out")
    )

    assert "--moduleResolution" not in command
    assert command[:5] == ("tsc", "--target", "ES2022", "--module", "ESNext")
    assert "--skipLibCheck" in command
    assert command[-1] == "/tmp/src/interpreter.ts"
    assert command[command.index("--outDir") : command.index("--outDir") + 2] == (
        "--outDir",
        "/tmp/out",
    )


@skip_unless_host_tools(
    "tsc", doing="these tests compile the interpreter with the installed compiler"
)
def test_compile_from_source_compiles_with_the_installed_compiler(
    tmp_path: Path,
) -> None:
    """Whatever ``tsc`` is on PATH must build the interpreter unaided.

    Regression for the CI failure this replaced: a resolution flag baked into
    the command broke the whole TS leg under the runner's TypeScript 7.
    """
    bundle = ts_runtime.compile_from_source(REPO_ROOT, tmp_path)

    assert bundle.interpreter_module.is_file()
    emitted = bundle.interpreter_module.read_text(encoding="utf-8")
    # Explicit specifiers are what make the compiler default sufficient.
    assert "./models.js" in emitted


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


# ── Gate discovery: a future migration must not silently redefine the gate ──
_GATE_TEXT = """CREATE OR REPLACE FUNCTION resolution.evaluate_proposition(
    p_proposition_id uuid,
    p_trigger_reason text DEFAULT 'manual'::text,
    p_context jsonb DEFAULT NULL::jsonb
) RETURNS TABLE(disposition text, all_passed boolean, context_status text)
LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN;
END;
$function$;
"""


def test_migration_version_parsing() -> None:
    assert version_pins.migration_version(Path("resolution_migration_v32.sql")) == 32
    assert (
        version_pins.migration_version(
            Path("resolution_migration_v33_sweep_overload_defaults.sql")
        )
        == 33
    )
    assert version_pins.migration_version(Path("resolution_migration_v24b.sql")) == 24
    assert (
        version_pins.migration_version(Path("resolution_schema_consolidated.sql")) is None
    )


def test_current_checkout_has_no_post_v32_gate_redefinition() -> None:
    assert version_pins.discover_later_gate_definitions(REPO_ROOT) == []


def _fake_repo(tmp_path: Path, later_file: str, later_text: str) -> Path:
    """Minimal checkout with a pinned gate and one later migration."""
    root = tmp_path / "repo"
    migrations = root / "schemas" / "migrations" / "resolution"
    migrations.mkdir(parents=True)
    (migrations / "resolution_migration_v32.sql").write_text(_GATE_TEXT)
    (migrations / later_file).write_text(later_text)
    (root / "gate.py").write_text("gate\n")
    return root


def _fake_pins(root: Path, pins_path: Path) -> None:
    payload = version_pins.current_pin_values(root)
    pins_path.write_text(json.dumps(payload))


def test_verify_pins_reports_a_post_v32_gate_redefinition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fake_repo(
        tmp_path,
        "resolution_migration_v38_gate_tweak.sql",
        _GATE_TEXT,
    )
    monkeypatch.setattr(version_pins, "PINNED_FILES", {"gate.py": "test gate"})
    pins_path = tmp_path / "pins.json"
    _fake_pins(root, pins_path)

    drifts = verify_pins(root, pins_path)

    assert [drift.path for drift in drifts] == [
        "schemas/migrations/resolution/resolution_migration_v38_gate_tweak.sql"
    ]
    assert "redefines" in drifts[0].role


def test_verify_pins_allows_a_later_migration_that_only_mentions_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fake_repo(
        tmp_path,
        "resolution_migration_v37_grant.sql",
        "GRANT EXECUTE ON FUNCTION resolution.evaluate_proposition(uuid, text, jsonb) "
        "TO pguser;\n",
    )
    monkeypatch.setattr(version_pins, "PINNED_FILES", {"gate.py": "test gate"})
    pins_path = tmp_path / "pins.json"
    _fake_pins(root, pins_path)

    assert verify_pins(root, pins_path) == []


# ── Extraction and teardown ────────────────────────────────────────────────
def test_extract_gate_sql_returns_the_verbatim_definition() -> None:
    migration = REPO_ROOT / sql_runtime.GATE_MIGRATION
    text = migration.read_text(encoding="utf-8")
    gate = sql_runtime.extract_gate_sql(text)

    assert gate in text
    assert gate.startswith(
        "CREATE OR REPLACE FUNCTION resolution.evaluate_proposition("
    )
    assert gate.rstrip().endswith("$function$;")


def test_extract_gate_sql_rejects_missing_or_duplicate_definitions() -> None:
    with pytest.raises(sql_runtime.SqlHarnessError):
        sql_runtime.extract_gate_sql("SELECT 1;")
    with pytest.raises(sql_runtime.SqlHarnessError):
        sql_runtime.extract_gate_sql(_GATE_TEXT + _GATE_TEXT)


def test_throwaway_database_reports_a_failed_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_psql(args, stdin_text=None, timeout=120):
        joined = " ".join(args)
        if "DROP DATABASE" in joined:
            return _completed(
                1, stderr="FATAL: database is being accessed by other users"
            )
        return _completed(0)

    monkeypatch.setattr(sql_runtime, "_psql", fake_psql)
    database = sql_runtime.ThrowawayDatabase("SELECT 1;")

    with pytest.raises(sql_runtime.SqlHarnessError) as raised:
        with database:
            pass
    assert "could not drop throwaway parity database" in str(raised.value)


def test_failed_drop_does_not_mask_an_in_flight_exception(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def fake_psql(args, stdin_text=None, timeout=120):
        if "DROP DATABASE" in " ".join(args):
            return _completed(1, stderr="FATAL: still in use")
        return _completed(0)

    monkeypatch.setattr(sql_runtime, "_psql", fake_psql)
    database = sql_runtime.ThrowawayDatabase("SELECT 1;")

    with pytest.raises(ValueError, match="boom"):
        with database:
            raise ValueError("boom")

    assert "could not drop throwaway parity database" in capsys.readouterr().err


# ── run_guard owns the version binding ────────────────────────────────────
def test_run_guard_refuses_version_drift_without_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtimes(monkeypatch)
    pins = load_pins()
    pins["sql_gate_sha256"] = "f" * 64
    tampered = tmp_path / "version_pins.json"
    tampered.write_text(json.dumps(pins))

    report = guard.run_guard(REPO_ROOT, pins_path=tampered)

    assert [drift.path for drift in report.drifts] == [
        f"{sql_runtime.GATE_MIGRATION}#evaluate_proposition"
    ]
    assert report.exit_code == guard.EXIT_VERSION_DRIFT


def test_run_guard_update_pins_refreshes_and_reverifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runtimes(monkeypatch)
    original = load_pins()
    pins = json.loads(json.dumps(original))
    pins["sql_gate_sha256"] = "f" * 64
    tampered = tmp_path / "version_pins.json"
    tampered.write_text(json.dumps(pins))

    report = guard.run_guard(REPO_ROOT, update_pins=True, pins_path=tampered)

    assert report.drifts == []
    assert report.exit_code == guard.EXIT_OK
    refreshed = load_pins(tampered)
    observed = current_pin_values(REPO_ROOT)
    assert refreshed["sql_gate_sha256"] == observed["sql_gate_sha256"]
    assert refreshed["pinned_files"] == observed["pinned_files"]
    # Descriptive metadata is preserved; only the hash payload is refreshed.
    assert refreshed["runtimes"] == original["runtimes"]
    assert refreshed["guard_schema_version"] == original["guard_schema_version"]


# ── End-to-end (hermetic) run ──────────────────────────────────────────────
def _sql_reachable() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        completed = sql_runtime._psql(("-d", "postgres", "-tAc", "SELECT 1"))
    except Exception:  # pragma: no cover - connection probe
        return False
    return completed.returncode == 0


_MISSING_STACK = missing_tools("node", "tsc")

requires_full_stack = pytest.mark.skipif(
    bool(_MISSING_STACK) or not _sql_reachable(),
    reason=(
        skip_reason(_MISSING_STACK, "the full parity guard needs the JS toolchain")
        if _MISSING_STACK
        else "PostgreSQL unavailable — full parity guard cannot run"
    ),
)


@requires_full_stack
def test_full_guard_runs_hermetically_and_holds_the_contract() -> None:
    report = guard.run_guard(REPO_ROOT)
    assert report.runtime_errors == {}
    assert report.drifts == []
    assert report.unavailable_runtimes == []
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
    assert report["version_drift"] == []
    assert report["hermetic"]["live_database_touched"] is False
    assert report["hermetic"]["migrations_applied"] is False
    assert report["hermetic"]["admission_or_production_change"] is False
    assert set(report["follow_ups"]) == set(FOLLOW_UPS)
    assert report["summary"] == {
        "cases": len(MATRIX),
        "agreement_cases": 6,
        "divergence_cases": 5,
        "not_attested_cases": 0,
        "failures": 0,
    }
