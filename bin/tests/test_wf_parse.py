"""Workflow PARSE guard — every workflow file must load as YAML.

Why this exists
---------------
Ruling 1/4 copied-vs-novel CodeQL classification was dead for ~11 days
(2026-10-01 → 2026-10-14) because `.github/workflows/codeql-twin-classification.yml`
had an unquoted scalar containing `": "` in a job name (line 127):

    - name: Baseline diff (RULING-6: report-only)

YAML reads the colon-space as the start of a nested mapping and refuses to
parse the file. The failure signature is what let it hide for so long:

  * a workflow that PARSES runs jobs; one that does NOT parse never starts;
  * GitHub therefore records the run as `failure` **with zero jobs**, which in
    any per-check or per-job view simply doesn't appear.

So "nothing is red" and "something is broken" were both true at once.

`bin/wf_lint.py` could not catch this by construction: it is a line-scanner
over named rules, and a whole-file parse error is not a line-level violation.
This guard is the missing complement — it does the one thing a line-scanner
structurally cannot: load each file and require it to parse.

Acceptance note
---------------
This guard asserts the workflow PARSES and DEFINES JOBS. It deliberately does
not assert CI is green: a parseable workflow can still fail for ordinary
reasons, and green-CI was never the property that hid this defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"


def workflow_files() -> list[Path]:
    if not WORKFLOWS.is_dir():
        return []
    return sorted(
        p
        for p in WORKFLOWS.iterdir()
        if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def _load(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf8"))


def test_workflow_directory_is_not_empty():
    files = workflow_files()
    assert files, (
        f"{WORKFLOWS} yielded no workflow files — this guard would pass "
        f"vacuously if the path moved"
    )


def test_workflow_directory_is_not_vacuous():
    """A guard over zero files is a green that means nothing."""
    assert len(workflow_files()) >= 20, (
        f"only {len(workflow_files())} workflows found; the repo has ~59 — "
        f"this guard is probably pointed at the wrong directory"
    )


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_parses(path: Path):
    """Every workflow must load as YAML.

    This is the check that would have caught codeql-twin-classification.yml:127
    on the push that introduced it, instead of 11 days and ~15 failed runs later.
    """
    try:
        _load(path)
    except yaml.YAMLError as exc:  # pragma: no cover - failure path
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        where = f"line {line + 1}" if line is not None else "unknown line"
        pytest.fail(
            f"{path.name} does not parse ({type(exc).__name__} at {where}). "
            f"A workflow that cannot parse never starts, so it fails with ZERO "
            f"jobs and is invisible in per-check views. An unquoted scalar "
            f"containing ': ' is the usual cause.\n{exc}"
        )


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_defines_at_least_one_job(path: Path):
    """A workflow that parses but declares no jobs produces zero jobs too.

    Same invisible-failure shape as the parse error, caught one step earlier.
    """
    data = _load(path)
    assert isinstance(data, dict), f"{path.name} parsed to {type(data).__name__}, not a mapping"
    jobs = data.get("jobs")
    assert jobs, f"{path.name} parses but defines no `jobs:` — it would run zero jobs"