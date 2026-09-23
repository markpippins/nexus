"""Tests for reconcile-python.py exit-code honesty + summary/detail agreement.

Background (contract-capture sweep finding, 2026-09): the reconciler used to
exit 0 despite GAPS and its summary table could print "OK 0/0" for services
whose detail section flagged every contract op as EXTRA (vacuous source
scans: vision-srv, rover). These tests pin the fixed behavior:

  * status_of: OK requires no missing AND no extra AND a non-empty source
    scan; NO-SOURCE is its own status; extras are GAPS; UNMODELED stays
    not-an-error.
  * exit_code_for / main(): 0 only when every modeled service is honestly
    OK; 1 on any gap — in full, --service, and --json modes.
  * CLI agreement: the exit code the script actually returns always equals
    exit_code_for() over the JSON report (holds whatever the repo's live
    coverage state is, so it survives coverage fixes without editing tests).

Run:
    python3 -m pytest typespec/v1/scripts/test_reconcile_python.py -q
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "reconcile-python.py"

_spec = importlib.util.spec_from_file_location("reconcile_python_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Fixtures: synthetic repo trees (monkeypatch the module's path globals)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    py = tmp_path / "python"
    tsp = tmp_path / "typespec" / "v1"
    py.mkdir()
    tsp.mkdir(parents=True)
    monkeypatch.setattr(mod, "REPO", str(tmp_path))
    monkeypatch.setattr(mod, "PY_DIR", str(py))
    monkeypatch.setattr(mod, "TSP_DIR", str(tsp))
    return tmp_path


def write_fastapi_service(root: Path, name: str, routes: list[tuple[str, str]]):
    """routes: [(method, path)] — writes python/<name>/app.py."""
    d = root / "python" / name
    d.mkdir(parents=True)
    body = "from fastapi import FastAPI\napp = FastAPI()\n"
    body += "".join(
        f'@app.{method.lower()}("{path}")\ndef h_{i}(): pass\n'
        for i, (method, path) in enumerate(routes)
    )
    (d / "app.py").write_text(body)


def write_contract(root: Path, name: str, ops: list[tuple[str, str]]):
    """ops: [(VERB, path)] — writes typespec/v1/<name>/python/{main,operations}.tsp."""
    d = root / "typespec" / "v1" / name / "python"
    d.mkdir(parents=True)
    body = "".join(
        f'@route("{p}")\n@{v.lower()}\nop op_{i}(): void;\n'
        for i, (v, p) in enumerate(ops)
    )
    (d / "operations.tsp").write_text(body)
    (d / "main.tsp").write_text('import "./operations.tsp";\n')


def rest_entry(name: str, src: str | None = None) -> dict:
    return {"name": name, "type": "rest",
            "src_root": src if src is not None else f"python/{name}",
            "framework": "fastapi"}


# ---------------------------------------------------------------------------
# status_of — the honest status model
# ---------------------------------------------------------------------------

def test_status_ok_requires_full_match_and_nonempty_source(fake_repo):
    write_fastapi_service(fake_repo, "svc", [("GET", "/health")])
    write_contract(fake_repo, "svc", [("GET", "/health")])
    r = mod.reconcile(rest_entry("svc"))
    assert mod.status_of(r) == "OK"
    assert r["covered"] == 1 and r["total"] == 1


def test_missing_is_gaps(fake_repo):
    write_fastapi_service(fake_repo, "svc", [("GET", "/health"), ("GET", "/items")])
    write_contract(fake_repo, "svc", [("GET", "/health")])
    r = mod.reconcile(rest_entry("svc"))
    assert r["missing"] == ["GET /items"]
    assert mod.status_of(r) == "GAPS"


def test_extra_alone_is_gaps_not_ok(fake_repo):
    """Regression pin: extras are coverage gaps. The old code printed OK 0/0
    (or N/N) for extra-only services because it only looked at `missing`.
    Source scan must be NON-empty here — an empty scan is NO-SOURCE territory
    (precedence: gap classifications from a vacuous scan are unreliable)."""
    write_fastapi_service(fake_repo, "svc", [("GET", "/real")])
    write_contract(fake_repo, "svc", [("GET", "/real"), ("GET", "/ghost-route")])
    r = mod.reconcile(rest_entry("svc"))
    assert r["extra"] == ["GET /ghost-route"]
    assert r["missing"] == []
    assert r["total"] == 1  # scan non-empty → genuinely GAPS, not NO-SOURCE
    assert mod.status_of(r) == "GAPS"


def test_empty_scan_with_contract_is_no_source_even_if_dir_exists(fake_repo):
    """Exists-but-empty source dir is the same vacuous-scan class as a missing
    dir: NO-SOURCE, never OK."""
    (fake_repo / "python" / "svc").mkdir(parents=True)
    write_contract(fake_repo, "svc", [("GET", "/a")])
    r = mod.reconcile(rest_entry("svc"))
    assert r["no_source"] is True
    assert mod.status_of(r) == "NO-SOURCE"


def test_empty_source_scan_with_contract_is_no_source(fake_repo):
    """Regression pin: the vision-srv/rover class. Contract declares ops, the
    source scan finds nothing (stale src_root) — must NOT read as OK 0/0."""
    write_contract(fake_repo, "svc", [("GET", "/a"), ("POST", "/b")])
    # src_root points at a directory that does not exist
    r = mod.reconcile(rest_entry("svc", src="python/ghost"))
    assert r["no_source"] is True
    assert r["total"] == 0 and len(r["extra"]) == 2
    assert mod.status_of(r) == "NO-SOURCE"


def test_unmodeled_stays_not_an_error(fake_repo):
    """Documented convention: manifest service without a contract dir is
    UNMODELED, not an error."""
    write_fastapi_service(fake_repo, "svc", [("GET", "/health")])
    r = mod.reconcile(rest_entry("svc"))  # no contract written
    assert mod.status_of(r) == "UNMODELED"
    assert mod.exit_code_for([r]) == 0


# ---------------------------------------------------------------------------
# exit_code_for + main() — non-zero on any gap, in every mode
# ---------------------------------------------------------------------------

def test_exit_code_zero_only_when_all_ok(fake_repo):
    write_fastapi_service(fake_repo, "a", [("GET", "/x")])
    write_contract(fake_repo, "a", [("GET", "/x")])
    write_fastapi_service(fake_repo, "b", [("GET", "/y")])
    write_contract(fake_repo, "b", [("GET", "/y")])
    results = [mod.reconcile(rest_entry("a")), mod.reconcile(rest_entry("b"))]
    assert mod.exit_code_for(results) == 0


def test_exit_code_one_on_missing_extra_or_no_source(fake_repo):
    write_fastapi_service(fake_repo, "ok_svc", [("GET", "/x")])
    write_contract(fake_repo, "ok_svc", [("GET", "/x")])
    write_fastapi_service(fake_repo, "missing_svc", [("GET", "/y")])
    write_contract(fake_repo, "missing_svc", [])
    # extra_svc: non-empty scan (so it is GAPS, not NO-SOURCE) with one
    # matched route plus one contract-only route
    write_fastapi_service(fake_repo, "extra_svc", [("GET", "/real")])
    write_contract(fake_repo, "extra_svc", [("GET", "/real"), ("GET", "/z")])
    write_contract(fake_repo, "nosource_svc", [("GET", "/w")])
    results = [
        mod.reconcile(rest_entry("ok_svc")),
        mod.reconcile(rest_entry("missing_svc")),
        mod.reconcile(rest_entry("extra_svc")),
        mod.reconcile(rest_entry("nosource_svc", src="python/ghost")),
    ]
    assert [mod.status_of(r) for r in results] == ["OK", "GAPS", "GAPS", "NO-SOURCE"]
    assert mod.exit_code_for(results) == 1


def test_main_full_mode_exits_nonzero_on_gaps(fake_repo, monkeypatch, capsys):
    write_fastapi_service(fake_repo, "ok_svc", [("GET", "/x")])
    write_contract(fake_repo, "ok_svc", [("GET", "/x")])
    write_fastapi_service(fake_repo, "gap_svc", [])
    write_contract(fake_repo, "gap_svc", [("GET", "/z")])
    monkeypatch.setattr(mod, "MANIFEST", [rest_entry("ok_svc"), rest_entry("gap_svc")])
    monkeypatch.setattr(sys, "argv", ["reconcile-python.py"])
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "GAPS" in out and "COVERAGE GAPS DETECTED" in out
    # summary/detail agreement: the gap service's EXTRA rows exist in detail
    assert "EXTRA    gap_svc" in out


def test_main_full_mode_exits_zero_when_complete(fake_repo, monkeypatch, capsys):
    write_fastapi_service(fake_repo, "svc", [("GET", "/x")])
    write_contract(fake_repo, "svc", [("GET", "/x")])
    monkeypatch.setattr(mod, "MANIFEST", [rest_entry("svc")])
    monkeypatch.setattr(sys, "argv", ["reconcile-python.py"])
    assert mod.main() == 0
    assert "COVERAGE COMPLETE" in capsys.readouterr().out


def test_main_service_mode_exits_nonzero_on_gaps(fake_repo, monkeypatch):
    write_fastapi_service(fake_repo, "svc", [("GET", "/x"), ("GET", "/y")])
    write_contract(fake_repo, "svc", [("GET", "/x")])
    monkeypatch.setattr(mod, "MANIFEST", [rest_entry("svc")])
    monkeypatch.setattr(sys, "argv", ["reconcile-python.py", "--service", "svc"])
    assert mod.main() == 1


def test_main_json_mode_also_carries_the_exit_code(fake_repo, monkeypatch, capsys):
    """The --json report is machine-consumed; its exit code must be honest too."""
    write_fastapi_service(fake_repo, "svc", [("GET", "/real")])
    write_contract(fake_repo, "svc", [("GET", "/real"), ("GET", "/z")])
    monkeypatch.setattr(mod, "MANIFEST", [rest_entry("svc")])
    monkeypatch.setattr(sys, "argv", ["reconcile-python.py", "--json"])
    assert mod.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert mod.exit_code_for(report) == 1
    assert mod.status_of(report[0]) == "GAPS"


def test_main_unknown_service_is_usage_error(fake_repo, monkeypatch):
    monkeypatch.setattr(mod, "MANIFEST", [])
    monkeypatch.setattr(sys, "argv", ["reconcile-python.py", "--service", "ghost"])
    assert mod.main() == 2


def test_mcp_tool_reconciliation_ok(fake_repo):
    d = fake_repo / "python" / "mcpsvc"
    d.mkdir(parents=True)
    (d / "server.py").write_text(
        "@mcp.tool()\ndef tool_alpha(): pass\n"
    )
    tsp = fake_repo / "typespec" / "v1" / "mcpsvc" / "python"
    tsp.mkdir(parents=True)
    (tsp / "operations.tsp").write_text('@route("/tools/tool_alpha")\n@post\nop t(): void;\n')
    (tsp / "main.tsp").write_text('import "./operations.tsp";\n')
    entry = {"name": "mcpsvc", "type": "mcp", "src_root": "python/mcpsvc", "framework": "mcp"}
    r = mod.reconcile(entry)
    assert mod.status_of(r) == "OK"


# ---------------------------------------------------------------------------
# Live-repo consistency (drift-proof): whatever the real coverage state is,
# the CLI's exit code must equal the honest computation over its own JSON,
# and no extra-bearing / empty-scan service may carry status OK.
# ---------------------------------------------------------------------------

def test_live_cli_exit_matches_honest_computation():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        capture_output=True, text=True, timeout=120,
    )
    report = json.loads(proc.stdout)
    assert proc.returncode == mod.exit_code_for(report)
    for r in report:
        if not r.get("modeled"):
            continue
        # precedence mirrors status_of: NO-SOURCE outranks GAPS (gap
        # classifications derived from a vacuous scan are unreliable)
        if r.get("no_source"):
            assert mod.status_of(r) == "NO-SOURCE"
            assert r["total"] == 0
        elif r["missing"] or r["extra"]:
            assert mod.status_of(r) == "GAPS"


def test_live_cli_table_has_no_ok_zero_zero_with_extras():
    """The exact contradiction from the finding: a summary row reading OK 0/0
    while the detail section prints EXTRA rows for the same service."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=120,
    )
    lines = proc.stdout.splitlines()
    detail_services = {ln.split()[1] for ln in lines if ln.strip().startswith("EXTRA")}
    for ln in lines:
        parts = ln.split()
        if len(parts) >= 7 and parts[2] == "OK" and parts[3] == "0/0":
            assert parts[0] not in detail_services, (
                f"summary/detail contradiction: {parts[0]} is OK 0/0 but has EXTRA rows"
            )
