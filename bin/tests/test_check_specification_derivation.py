#!/usr/bin/env python3
"""Guards for bin/check_specification_derivation.py.

The verifier is what lets a Specification -> Requirement derivation package be
reviewed mechanically before any type work depends on it, so its own failure
modes matter as much as the happy path:

  * a missing acceptance criterion or evidence line is a refusal (a Requirement
    that cannot be verified is not granular, it is a wish),
  * duplicate or un-namespaced ids are refusals (two Specifications must not be
    able to claim the same Requirement),
  * a dangling or cyclic depends_on is a refusal (the chain must be executable
    in order),
  * blocked_by must name a gap the package actually declares (no free-floating
    "blocked" excuse),
  * representable_today=true with blocked_by is a contradiction,
  * the committed derivation package validates and reports its counts.

Hermetic: fixtures are built in tmp_path; nothing touches a database or network.

Dual-runnable: `pytest bin/tests/test_check_specification_derivation.py` or
`python3 bin/tests/test_check_specification_derivation.py`.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "check_specification_derivation.py"
PACKAGE = REPO_ROOT / "docs" / "specifications" / "analyst-r2-derivation.v1.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_specification_derivation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _requirement(req_id: str = "SPC-1.R1", **overrides) -> dict:
    requirement = {
        "id": req_id,
        "title": "Ledger the migration",
        "outcome": "The ledger has exactly one row.",
        "acceptance_criteria": ["SELECT count(*) returns 1."],
        "evidence": ["ledger SELECT output"],
        "depends_on": [],
        "verification_owner": "tester",
        "representable_today": True,
        "blocked_by": [],
        "blueprint_intent": {
            "approach": "Idempotent reviewed SQL.",
            "deliverable": "sql/ledger/entry.sql",
            "tests": "apply twice, assert one row",
        },
        "work_request_intent": {
            "request_kind": "build",
            "deliverable": "ledger row present",
            "compile_via": "conduit plan",
        },
    }
    requirement.update(overrides)
    return requirement


def _package(**overrides) -> dict:
    package = {
        "artifact": "nexus.specification_derivation",
        "version": 1,
        "status": "proposed_awaiting_planner_ratification",
        "derived_from": {"record_id": "584d2e75-5308-48f7-991a-481b79c0ed57"},
        "type_gaps": [{"id": "G1", "title": "no grouping"}],
        "specifications": [
            {
                "id": "SPC-1",
                "title": "Canonicalization closeout",
                "goal": "Make the landing provable.",
                "outcome": "Residuals closed.",
                "requirements": [_requirement()],
            }
        ],
    }
    package.update(overrides)
    return package


def test_valid_package_has_no_errors():
    assert _load_module().validate(_package()) == []


def test_missing_acceptance_criteria_is_refused():
    pkg = _package()
    del pkg["specifications"][0]["requirements"][0]["acceptance_criteria"]
    errors = _load_module().validate(pkg)
    assert any("acceptance_criteria" in e for e in errors), errors


def test_missing_evidence_is_refused():
    pkg = _package()
    pkg["specifications"][0]["requirements"][0]["evidence"] = []
    errors = _load_module().validate(pkg)
    assert any("evidence" in e for e in errors), errors


def test_duplicate_requirement_id_is_refused():
    pkg = _package()
    req = _requirement()
    pkg["specifications"][0]["requirements"] = [req, copy.deepcopy(req)]
    errors = _load_module().validate(pkg)
    assert any("duplicate requirement id" in e for e in errors), errors


def test_requirement_id_must_be_namespaced():
    pkg = _package()
    pkg["specifications"][0]["requirements"][0]["id"] = "R1"
    errors = _load_module().validate(pkg)
    assert any("namespaced" in e for e in errors), errors


def test_dangling_dependency_is_refused():
    pkg = _package()
    pkg["specifications"][0]["requirements"][0]["depends_on"] = ["SPC-9.R9"]
    errors = _load_module().validate(pkg)
    assert any("unknown requirement" in e for e in errors), errors


def test_dependency_cycle_is_refused():
    pkg = _package()
    pkg["specifications"][0]["requirements"] = [
        _requirement("SPC-1.R1", depends_on=["SPC-1.R2"]),
        _requirement("SPC-1.R2", depends_on=["SPC-1.R1"]),
    ]
    errors = _load_module().validate(pkg)
    assert any("dependency cycle" in e for e in errors), errors


def test_undeclared_gap_is_refused():
    pkg = _package()
    req = pkg["specifications"][0]["requirements"][0]
    req["representable_today"] = False
    req["blocked_by"] = ["G99"]
    errors = _load_module().validate(pkg)
    assert any("undeclared gap" in e for e in errors), errors


def test_representable_and_blocked_is_a_contradiction():
    pkg = _package()
    pkg["specifications"][0]["requirements"][0]["blocked_by"] = ["G1"]
    errors = _load_module().validate(pkg)
    assert any("contradicts" in e for e in errors), errors


def test_incomplete_blueprint_intent_is_refused():
    pkg = _package()
    pkg["specifications"][0]["requirements"][0]["blueprint_intent"] = {"approach": "x"}
    errors = _load_module().validate(pkg)
    assert any("blueprint_intent" in e for e in errors), errors


def test_cli_reports_failure_with_nonzero_exit(tmp_path):
    pkg = _package()
    del pkg["specifications"][0]["requirements"][0]["work_request_intent"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(pkg))
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "work_request_intent" in proc.stderr


def test_cli_accepts_a_valid_package(tmp_path):
    path = tmp_path / "good.json"
    path.write_text(json.dumps(_package()))
    proc = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "1 specification(s), 1 requirement(s)" in proc.stdout


def test_committed_package_is_valid():
    pkg = json.loads(PACKAGE.read_text())
    assert _load_module().validate(pkg) == []
    proc = subprocess.run([sys.executable, str(SCRIPT), str(PACKAGE)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            if fn.__code__.co_argcount:
                import tempfile

                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
                fn()
            print(f"ok {name}")
