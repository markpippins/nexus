"""Guard orchestration: run the matrix, compare against the pinned contract.

Exit codes
----------
0  the parity contract holds for every case in every runtime
1  a runtime failed, or a case drifted from its pinned expectation
2  version drift — a pinned interpreter/migration file changed, so the
   recorded proof no longer applies (re-verify and refresh the pins)

The guard never writes to the repository and never touches the live nexus
database. Python and TypeScript are in-memory; SQL runs in a throwaway
database that is created and dropped by :mod:`solscript_framing_parity.sql_runtime`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from . import matrix as matrix_module
from .canonical import Outcome
from .matrix import MATRIX, RUNTIMES, RUNTIME_LABELS, Case
from .version_pins import (
    PINS_PATH,
    load_pins,
    pin_summary,
    verify_pins,
    write_pins,
)

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

EXIT_OK = 0
EXIT_PARITY_FAILURE = 1
EXIT_VERSION_DRIFT = 2


@dataclass
class CaseVerdict:
    case: Case
    observed: Dict[str, Outcome]
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def verdict(self) -> str:
        if not self.ok:
            return "FAIL"
        if self.case.expectation.kind == "agree":
            return "AGREE"
        return f"DIVERGE({self.case.expectation.follow_up})"

    def to_json(self) -> dict:
        return {
            "id": self.case.id,
            "plan_clause": self.case.plan_clause,
            "description": self.case.description,
            "expectation": self.case.expectation.kind,
            "follow_up": getattr(self.case.expectation, "follow_up", None),
            "observed": {
                runtime: outcome.to_json()
                for runtime, outcome in self.observed.items()
            },
            "problems": list(self.problems),
            "verdict": self.verdict,
        }


@dataclass
class GuardReport:
    repo_root: Path
    pins: Mapping
    cases: List[CaseVerdict]
    runtime_errors: Dict[str, str]
    versions: Mapping
    keep_sql_database: bool = False

    @property
    def failures(self) -> int:
        return len(self.runtime_errors) + sum(1 for verdict in self.cases if not verdict.ok)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.failures == 0 and not self.runtime_errors else EXIT_PARITY_FAILURE

    @property
    def agreement_count(self) -> int:
        return sum(1 for verdict in self.cases if verdict.case.expectation.kind == "agree")

    @property
    def divergence_count(self) -> int:
        return sum(
            1 for verdict in self.cases if verdict.case.expectation.kind == "divergence"
        )

    def to_json(self) -> dict:
        return {
            "ok": self.exit_code == EXIT_OK,
            "exit_code": self.exit_code,
            "repo_root": str(self.repo_root),
            "version_bindings": pin_summary(self.pins).splitlines(),
            "versions": dict(self.versions),
            "runtime_errors": dict(self.runtime_errors),
            "cases": [verdict.to_json() for verdict in self.cases],
            "summary": {
                "cases": len(self.cases),
                "agreement_cases": self.agreement_count,
                "divergence_cases": self.divergence_count,
                "failures": self.failures,
            },
            "follow_ups": {
                key: dict(value) for key, value in matrix_module.FOLLOW_UPS.items()
            },
            "hermetic": {
                "python": "in-memory only",
                "typescript": "compiled from source and run in a temp directory (removed)",
                "sql": "throwaway database created and dropped"
                + (" (kept: --keep-sql-database)" if self.keep_sql_database else ""),
                "live_database_touched": False,
                "migrations_applied": False,
                "admission_or_production_change": False,
            },
        }


def compare(runtime_outcomes: Dict[str, Dict[str, Outcome]]) -> List[CaseVerdict]:
    """Compare observed outcomes to the pinned parity contract."""
    verdicts: List[CaseVerdict] = []
    for case in MATRIX:
        observed = {
            runtime: runtime_outcomes[runtime][case.id]
            for runtime in RUNTIMES
            if case.id in runtime_outcomes.get(runtime, {})
        }
        problems: List[str] = []

        for runtime in RUNTIMES:
            if runtime not in observed:
                problems.append(f"{runtime}: no result (runtime did not run)")
                continue
            expected = case.expected_for(runtime)
            if observed[runtime].comparison_key() != expected.comparison_key():
                problems.append(
                    f"{runtime}: expected {expected.render()}, "
                    f"observed {observed[runtime].render()}"
                )

        if len(observed) == len(RUNTIMES):
            distinct = {outcome.comparison_key() for outcome in observed.values()}
            if case.expectation.kind == "agree" and len(distinct) != 1:
                problems.append(
                    "pinned as agreement but the runtimes produced "
                    f"{len(distinct)} distinct outcomes"
                )
            if case.expectation.kind == "divergence" and len(distinct) == 1:
                problems.append(
                    "pinned as divergence but all runtimes now agree — "
                    "re-verify the parity contract"
                )

        verdicts.append(CaseVerdict(case=case, observed=observed, problems=problems))
    return verdicts


def run_guard(
    repo_root: Optional[Path] = None,
    *,
    update_pins: bool = False,
    pins_path: Path = PINS_PATH,
    keep_sql_database: bool = False,
    keep_ts_temp: bool = False,
) -> GuardReport:
    """Run the whole guard and return a structured report (no printing)."""
    root = Path(repo_root) if repo_root is not None else DEFAULT_REPO_ROOT
    pins = load_pins(pins_path)

    if update_pins:
        pins = write_pins(root, pins_path)

    runtime_errors: Dict[str, str] = {}
    versions: Dict[str, object] = {}

    from . import python_runtime, sql_runtime, ts_runtime

    python_outcomes: Dict[str, Outcome] = {}
    typescript_outcomes: Dict[str, Outcome] = {}
    sql_outcomes: Dict[str, Outcome] = {}

    try:
        python_outcomes = python_runtime.run_matrix(MATRIX, root)
        versions["python"] = {
            "interpreter": "python/SOLScript/solscript/interpreter.py",
            "mode": "in-memory",
        }
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        runtime_errors["python"] = f"{type(exc).__name__}: {exc}"

    try:
        typescript_outcomes = ts_runtime.run_matrix(
            root, keep_temp=keep_ts_temp, cases=MATRIX
        )
        versions["typescript"] = ts_runtime.runtime_fingerprint(root) or {}
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        runtime_errors["typescript"] = f"{type(exc).__name__}: {exc}"

    try:
        sql_outcomes = sql_runtime.run_matrix(
            root, cases=MATRIX, keep_database=keep_sql_database
        )
        versions["sql"] = {
            "gate": sql_runtime.GATE_MIGRATION,
            "mode": "throwaway database",
        }
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        runtime_errors["sql"] = f"{type(exc).__name__}: {exc}"

    runtime_outcomes = {
        "python": python_outcomes,
        "typescript": typescript_outcomes,
        "sql": sql_outcomes,
    }
    if runtime_errors:
        # A runtime that failed cannot attest its rows; drop partial data so the
        # report shows missing results instead of a misleading subset.
        for runtime in runtime_errors:
            runtime_outcomes[runtime] = {}

    verdicts = compare(runtime_outcomes)
    return GuardReport(
        repo_root=root,
        pins=pins,
        cases=verdicts,
        runtime_errors=runtime_errors,
        versions=versions,
        keep_sql_database=keep_sql_database,
    )


def _format_table(report: GuardReport) -> str:
    id_width = max([len(case.case.id) for case in report.cases] + [4]) + 2

    cells: Dict[str, List[str]] = {}
    widths: Dict[str, int] = {}
    for runtime in RUNTIMES:
        column_cells = []
        for verdict in report.cases:
            outcome = verdict.observed.get(runtime)
            column_cells.append(outcome.render() if outcome else "<no result>")
        cells[runtime] = column_cells
        widths[runtime] = max([len(RUNTIME_LABELS[runtime])] + [len(c) for c in column_cells]) + 2

    header = f"{'CASE':<{id_width}}" + "".join(
        f"{RUNTIME_LABELS[runtime]:<{widths[runtime]}}" for runtime in RUNTIMES
    ) + "VERDICT"
    lines = [header, "-" * len(header)]
    for index, verdict in enumerate(report.cases):
        row = f"{verdict.case.id:<{id_width}}"
        for runtime in RUNTIMES:
            row += f"{cells[runtime][index]:<{widths[runtime]}}"
        row += verdict.verdict
        lines.append(row)
        for problem in verdict.problems:
            lines.append(f"{'':<{id_width}}  ! {problem}")
    return "\n".join(lines)


def _format_follow_ups() -> str:
    lines = ["Residual divergences (explicit follow-ups — no behaviour change here):"]
    for key, follow_up in matrix_module.FOLLOW_UPS.items():
        lines.append(f"  {key}: {follow_up['title']}  [{follow_up['status']}]")
        lines.append(f"      affects: {follow_up['runtimes']}")
    return "\n".join(lines)


def render_report(report: GuardReport) -> str:
    lines = [
        "Ruling 32 §4 — cross-runtime framing parity guard (shadow-only)",
        pin_summary(report.pins),
        "",
        _format_table(report),
        "",
    ]
    if report.runtime_errors:
        lines.append("Runtime errors:")
        for runtime, error in report.runtime_errors.items():
            lines.append(f"  {runtime}: {error}")
        lines.append("")
    lines.append(
        f"Summary: {len(report.cases)} cases, {report.agreement_count} agreement, "
        f"{report.divergence_count} pinned divergence, {report.failures} failure(s)"
    )
    lines.append("")
    lines.append(_format_follow_ups())
    lines.append("")
    lines.append(
        "Hermetic: Python in-memory; TypeScript compiled from source in a temp "
        "dir; SQL in a throwaway database (created + dropped). "
        "No live DB write, no migration, no admission change."
    )
    return "\n".join(lines)


def _drift_block(drifts) -> str:
    lines = [
        "VERSION DRIFT — the framing parity proof no longer applies.",
        "The following pinned interpreter/migration files changed:",
    ]
    lines.extend(drift.render() for drift in drifts)
    lines.append("")
    lines.append(
        "Re-verify the parity contract against the new revisions, then refresh "
        "the pins with `--update-pins`. Do not refresh blindly."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m solscript_framing_parity",
        description=(
            "Prove the E8.4 framing gate agrees across Python, SQL and "
            "TypeScript over one shared matrix (shadow-only, version-bound)."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="nexus repository root (default: auto-detected from this package)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full report as JSON on stdout",
    )
    parser.add_argument(
        "--update-pins",
        action="store_true",
        help="refresh version_pins.json from the working tree, then run",
    )
    parser.add_argument(
        "--keep-sql-database",
        action="store_true",
        help="do not drop the throwaway database (debugging only)",
    )
    parser.add_argument(
        "--keep-ts-temp",
        action="store_true",
        help="do not remove the TypeScript temp build dir (debugging only)",
    )
    args = parser.parse_args(argv)

    drifts = verify_pins(args.repo_root, PINS_PATH)
    if drifts and not args.update_pins:
        # stdout: human block; stderr keeps exit diagnostics separable.
        print(_drift_block(drifts))
        return EXIT_VERSION_DRIFT

    if drifts and args.update_pins:
        print(
            "WARNING: refreshing version pins after drift; the previous proof "
            "did not cover the observed revisions.",
            file=sys.stderr,
        )
        for drift in drifts:
            print(drift.render(), file=sys.stderr)

    report = run_guard(
        args.repo_root,
        update_pins=args.update_pins,
        keep_sql_database=args.keep_sql_database,
        keep_ts_temp=args.keep_ts_temp,
    )

    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    else:
        print(render_report(report))

    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via __main__
    raise SystemExit(main())
