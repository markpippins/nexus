#!/usr/bin/env python3
"""Guards for the Lilac executable-topology inventory (plan 8261667, parent 8261639).

Pins the six acceptance criteria:

  AC1  the JSON is *generated* (idempotent, self-describing), records pid,
       working tree, service unit, port, invocation id, language, database
       target and route per producer/consumer, and `--check` exits non-zero
       when the committed artifact drifts.
  AC2  coverage is fail-closed per surface: zero production writers, or a
       writer whose lifecycle is `unknown` with no disposition, is a failure.
  AC3  every Python/TypeScript overlap carries exactly one classification from
       the fixed vocabulary and one disposition naming the replacing path.
  AC4  the Markdown states canonical ownership, lists live consumers, and every
       alternate canonical implementation appears as a violation with file:line.
  AC5  retired surfaces are `historical`, and a retired unit described as live
       authority is a guard failure.
  AC6  the change set is read-only: `git diff --name-only origin/main` touches
       docs/ and bin/ only — no sql/, no migration, no service code.

Hermetic: every fixture builds a scratch tree in tmp_path; the runtime probe is
injected, never executed. The one repo-level test scans the real tree and runs
`--check` with the probe disabled.

Dual-runnable: `pytest bin/tests/test_lilac_topology_inventory.py` or
`python3 bin/tests/test_lilac_topology_inventory.py`.

topology-inventory: ignore-file — this file's SQL fixtures are test data, not
producers, and must not appear in the inventory it guards.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "bin" / "lilac-topology-inventory.py"
ARTIFACT_JSON = REPO_ROOT / "docs" / "lilac" / "executable-topology.json"
ARTIFACT_MD = REPO_ROOT / "docs" / "lilac" / "executable-topology.md"

LIFECYCLES = {"live", "test/worktree", "retired", "compatibility", "unknown"}
OVERLAP_CLASSES = {
    "intentional-channel",
    "semantic-duplicate",
    "stale-or-deprecated",
    "test-only",
}
OVERLAP_DISPOSITIONS = {"redirect", "adapter", "retirement"}
REQUIRED_ENTRY_FIELDS = {
    "surface",
    "path",
    "line",
    "language",
    "target_schema",
    "table",
    "role",
    "lifecycle",
    "disposition",
    "working_tree",
    "route",
    "route_note",
}


def _load_module():
    spec = importlib.util.spec_from_file_location("lilac_topology_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


TOPOLOGY = _load_module()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

LILAC_PY = '''"""canonical owner fixture"""
SCHEMA_DEFAULT = 1


class Store:
    def __init__(self, schema: str = "resolution"):
        self._schema = schema

    def write(self, sql):
        sql = sql.replace("%SCHEMA%", self._schema)
        return [
            """INSERT INTO %SCHEMA%.receipt (id) VALUES (1)""",
            """INSERT INTO %SCHEMA%.ticket (id) VALUES (1)""",
            """INSERT INTO %SCHEMA%.ticket_transition (id) VALUES (1)""",
            """INSERT INTO %SCHEMA%.fanout_transition (id) VALUES (1)""",
        ]
'''

ALT_CANONICAL_PY = '''"""second canonical receipt writer"""
class Writer:
    def __init__(self, schema: str = "resolution"):
        self._schema = schema

    def write(self, sql):
        return """INSERT INTO %SCHEMA%.receipt (id) VALUES (1)"""
'''

UNRESOLVED_PY = '''"""writer whose schema cannot be resolved"""
def write(sql):
    return """INSERT INTO %SCHEMA%.receipt (id) VALUES (1)"""
'''

LEGACY_TS = '''// legacy writer fixture
const VISION_SCHEMA = "vision";
function write(sql) {
  return [
    `INSERT INTO ${VISION_SCHEMA}.receipts (id) VALUES (1)`,
    `INSERT INTO ${VISION_SCHEMA}.tickets (id) VALUES (1)`,
  ];
}
'''

TEST_PY = '''"""test writer — must never count as coverage"""
def seed(sql):
    return """INSERT INTO %SCHEMA%.receipt (id) VALUES (1)"""
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_tree(root: Path, *, alternate_canonical: bool = True, ts: bool = True,
                test_file: bool = False) -> Path:
    _write(root / "python" / "conduit" / "lilac.py", LILAC_PY)
    if alternate_canonical:
        _write(root / "python" / "expression" / "persistence.py", ALT_CANONICAL_PY)
    if ts:
        _write(root / "typescript" / "conduit-mcp" / "src" / "db.ts", LEGACY_TS)
    if test_file:
        _write(root / "python" / "conduit" / "tests" / "test_seed.py", TEST_PY)
    return root


FAKE_LIVE = {
    "available": True,
    "services": [
        {
            "unit": "conduit-kernel.service",
            "port": 3103,
            "pid": 4242,
            "invocation_id": "inv-abc",
            "language": "python",
            "working_tree": "/srv/nexus",
            "route": "tcp://127.0.0.1:3103",
            "route_note": "listening socket this unit serves",
            "lifecycle": "live",
        }
    ],
}


# ---------------------------------------------------------------------------
# AC1 — generated artifact, complete fields, drift detection
# ---------------------------------------------------------------------------


def test_every_entry_records_the_required_runtime_facts(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(
        root=_build_tree(tmp_path), live=FAKE_LIVE, generated_at="2026-11-11T00:00:00+00:00"
    )
    entries = [
        entry
        for surface in inventory["surfaces"].values()
        for kind in ("writers", "readers")
        for entry in surface[kind]
    ]
    assert entries, "fixture produced no writers or readers"
    for entry in entries:
        missing = REQUIRED_ENTRY_FIELDS - set(entry)
        assert not missing, f"{entry['path']} missing {sorted(missing)}"
        assert entry["lifecycle"] in LIFECYCLES
        assert entry["line"] >= 1

    service = inventory["services"][0]
    for field in ("unit", "port", "pid", "invocation_id", "language", "working_tree", "route", "lifecycle"):
        assert field in service, f"service entry missing {field}"
    assert service["route"], "the live service must record the endpoint it serves"
    assert all(entry["route_note"] for entry in entries), "a null route must carry a reason"


def test_artifact_is_generated_and_idempotent(tmp_path: Path) -> None:
    root = _build_tree(tmp_path)
    first = TOPOLOGY.build_inventory(root=root, live=FAKE_LIVE, generated_at="2026-11-11T00:00:00+00:00")
    second = TOPOLOGY.build_inventory(root=root, live=FAKE_LIVE, generated_at="2026-11-11T00:00:00+00:00")
    assert TOPOLOGY.render(first) == TOPOLOGY.render(second)
    assert first["_meta"]["generator"] == "bin/lilac-topology-inventory.py"
    assert first["_meta"]["plan"] == "8261667"
    # Regeneration must be a pure function of the tree + probe, not of history.
    assert "generated_at" in first["_meta"]


def test_check_exits_nonzero_on_drift(tmp_path: Path) -> None:
    root = _build_tree(tmp_path)
    out = tmp_path / "artifacts" / "topology.json"
    md = tmp_path / "artifacts" / "topology.md"

    assert TOPOLOGY.main(["--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 0
    assert TOPOLOGY.main(["--check", "--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 0

    payload = json.loads(out.read_text())
    payload["coverage"]["receipt"]["production_writers"] += 1
    out.write_text(TOPOLOGY.render(payload), encoding="utf-8")
    assert TOPOLOGY.main(["--check", "--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 1

    out.unlink()
    assert TOPOLOGY.main(["--check", "--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 2


def test_check_refuses_a_fail_open_artifact_even_when_it_is_current(tmp_path: Path) -> None:
    """A regenerated artifact that records a fail-open state must not pass.

    This is the reviewer's reproduced hole (record 51488d35): drift compares
    committed against fresh, so an artifact that faithfully recorded a red
    coverage table was drift-clean and green. Exit 3 is the separate answer.
    """
    root = _build_tree(tmp_path)
    out = tmp_path / "topology.json"
    md = tmp_path / "topology.md"
    assert TOPOLOGY.main(["--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 0

    # CASE C — a surface with no production writer, regenerated so the file
    # agrees with the tree and drift can see nothing.
    payload = json.loads(out.read_text())
    payload["coverage"]["fanout_transition"] = {
        "production_writers": 0,
        "canonical_writers": 0,
        "undeclared_unknown_writers": 0,
        "ok": False,
    }
    out.write_text(TOPOLOGY.render(payload), encoding="utf-8")
    assert TOPOLOGY.integrity_problems(payload)
    assert TOPOLOGY.main(["--check", "--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 3


# ---------------------------------------------------------------------------
# AC2/AC3 — the recorded state itself must be valid (exit 3)
# ---------------------------------------------------------------------------


def test_integrity_problems_flags_zero_writer_surface(tmp_path: Path) -> None:
    _write(tmp_path / "python" / "conduit" / "receipts_only.py", RECEIPT_ONLY_PY)
    inventory = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
    problems = TOPOLOGY.integrity_problems(inventory)
    assert any("$coverage.ticket" in p and "no production writer" in p for p in problems), problems
    assert any("$coverage.fanout_transition" in p for p in problems), problems
    assert not any("$coverage.receipt" in p for p in problems), problems


def test_integrity_problems_flags_undeclared_unknown_writer(tmp_path: Path) -> None:
    _write(tmp_path / "python" / "mystery" / "writer.py", UNRESOLVED_PY)
    inventory = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
    problems = TOPOLOGY.integrity_problems(inventory)
    assert any("writers[0]" in p and "no disposition" in p for p in problems), problems


def test_integrity_problems_flags_unclassified_overlap(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    assert inventory["overlaps"]
    for overlap in inventory["overlaps"]:
        overlap["classification"] = "unknown"
        overlap["disposition"] = "unknown"
        overlap["replaces"] = None
    problems = TOPOLOGY.integrity_problems(inventory)
    assert any("classification" in p for p in problems), problems
    assert any("disposition" in p for p in problems), problems
    assert any("replaces" in p for p in problems), problems


def test_integrity_problems_accepts_the_committed_state(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    assert TOPOLOGY.integrity_problems(inventory) == []


def test_check_exits_nonzero_when_markdown_drifts(tmp_path: Path) -> None:
    root = _build_tree(tmp_path)
    out = tmp_path / "topology.json"
    md = tmp_path / "topology.md"
    assert TOPOLOGY.main(["--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 0
    md.write_text(md.read_text() + "\nhand edit\n", encoding="utf-8")
    assert TOPOLOGY.main(["--check", "--root", str(root), "--out", str(out), "--md-out", str(md), "--no-probe"]) == 1


# ---------------------------------------------------------------------------
# AC2 — fail-closed coverage
# ---------------------------------------------------------------------------


RECEIPT_ONLY_PY = '''"""single-surface writer fixture"""
class Writer:
    def __init__(self, schema: str = "resolution"):
        self._schema = schema

    def write(self, sql):
        return """INSERT INTO %SCHEMA%.receipt (id) VALUES (1)"""
'''


def test_coverage_fails_closed_when_a_surface_has_no_production_writer(tmp_path: Path) -> None:
    _write(tmp_path / "python" / "conduit" / "receipts_only.py", RECEIPT_ONLY_PY)
    inventory = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
    # Only receipt is written by this fixture; the other three must be red.
    assert inventory["coverage"]["receipt"]["ok"] is True
    for surface in ("ticket", "ticket_transition", "fanout_transition"):
        assert inventory["coverage"][surface]["ok"] is False
        assert inventory["coverage"][surface]["production_writers"] == 0


def test_test_only_writers_do_not_satisfy_coverage(tmp_path: Path) -> None:
    _write(tmp_path / "python" / "conduit" / "tests" / "test_seed.py", TEST_PY)
    inventory = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
    assert inventory["coverage"]["receipt"]["production_writers"] == 0
    assert inventory["coverage"]["receipt"]["ok"] is False


def test_unknown_lifecycle_writer_requires_a_disposition(tmp_path: Path) -> None:
    _write(tmp_path / "python" / "mystery" / "writer.py", UNRESOLVED_PY)
    inventory = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
    writer = inventory["surfaces"]["receipt"]["writers"][0]
    assert writer["target_schema"] == "unknown"
    assert writer["lifecycle"] == "unknown"
    assert writer["disposition"] is None
    assert inventory["coverage"]["receipt"]["undeclared_unknown_writers"] == 1
    assert inventory["coverage"]["receipt"]["ok"] is False

    # Declaring the seam makes it a compatibility path, not an unknown.
    TOPOLOGY.DISPOSITIONS[writer["path"]] = "declared seam under test"
    try:
        declared = TOPOLOGY.build_inventory(root=tmp_path, live={"available": False, "services": []})
        entry = declared["surfaces"]["receipt"]["writers"][0]
        assert entry["lifecycle"] == "compatibility"
        assert entry["disposition"] == "declared seam under test"
        assert declared["coverage"]["receipt"]["ok"] is True
    finally:
        TOPOLOGY.DISPOSITIONS.pop(writer["path"], None)


# ---------------------------------------------------------------------------
# AC3 — overlap classification
# ---------------------------------------------------------------------------


def test_python_typescript_overlap_is_classified_and_disposed(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    assert inventory["overlaps"], "fixture should produce a receipt overlap"
    for overlap in inventory["overlaps"]:
        assert overlap["classification"] in OVERLAP_CLASSES
        assert overlap["disposition"] in OVERLAP_DISPOSITIONS
        assert overlap["replaces"], f"{overlap['surface']} overlap names no replacing path"
        assert overlap["replaces"] != overlap["typescript_writers"][0]
        assert overlap["python_writers"] and overlap["typescript_writers"]


def test_undeclared_overlap_is_reported_as_unknown(tmp_path: Path) -> None:
    # `ticket` has a Python and a TypeScript writer but no declared class in the
    # fixture-only registry: the guard must surface it, not invent a verdict.
    original = TOPOLOGY.OVERLAP_CLASSIFICATION.pop("ticket", None)
    try:
        inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
        tickets = [o for o in inventory["overlaps"] if o["surface"] == "ticket"]
        assert tickets and tickets[0]["classification"] == "unknown"
        assert tickets[0]["disposition"] == "unknown"
    finally:
        if original is not None:
            TOPOLOGY.OVERLAP_CLASSIFICATION["ticket"] = original


# ---------------------------------------------------------------------------
# AC4 — ownership statement, consumer list, violations with file:line
# ---------------------------------------------------------------------------


def test_alternate_canonical_writer_is_a_violation_with_file_and_line(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    violations = {(v["path"], v["line"]) for v in inventory["violations"]}
    assert any(path == "python/expression/persistence.py" for path, _ in violations)
    assert not any(path == "python/conduit/lilac.py" for path, _ in violations), \
        "the canonical owner must never be its own violation"

    markdown = TOPOLOGY.render_markdown(inventory)
    for path, line in violations:
        assert f"`{path}:{line}`" in markdown, f"{path}:{line} not cited in the ownership document"
    assert inventory["canonical_ownership"]["statement"] in markdown
    for surface in ("receipt", "ticket"):
        assert f"**{'receipt' if surface == 'receipt' else 'ticket'}**" in markdown


def test_violation_list_is_complete_in_both_directions(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    canonical_non_owner = {
        (w["path"], w["line"])
        for surface in inventory["surfaces"].values()
        for w in surface["writers"]
        if w["role"] == "canonical"
        and w["path"] != inventory["canonical_ownership"]["owner"]
        and w["lifecycle"] != "test/worktree"
    }
    recorded = {(v["path"], v["line"]) for v in inventory["violations"]}
    assert canonical_non_owner == recorded, "a canonical write escaped the violation list"


# ---------------------------------------------------------------------------
# AC5 — retired surfaces are historical, never live authority
# ---------------------------------------------------------------------------


def test_retired_surfaces_are_historical_everywhere(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    markdown = TOPOLOGY.render_markdown(inventory)
    assert inventory["retired_surfaces"], "no retired surfaces recorded"
    for retired in inventory["retired_surfaces"]:
        assert retired["authority"] == "historical"
        assert retired["token"] in markdown
        assert retired["evidence"]
    assert TOPOLOGY.retired_authority_problems(inventory, markdown) == []


def test_retired_authority_guard_bites(tmp_path: Path) -> None:
    inventory = TOPOLOGY.build_inventory(root=_build_tree(tmp_path), live={"available": False, "services": []})
    bad_markdown = TOPOLOGY.render_markdown(inventory) + "\nTemporal is authoritative for work dispatch today.\n"
    assert TOPOLOGY.retired_authority_problems(inventory, bad_markdown)

    inventory["retired_surfaces"][0]["authority"] = "live"
    assert TOPOLOGY.retired_authority_problems(inventory, TOPOLOGY.render_markdown(inventory))


# ---------------------------------------------------------------------------
# AC6 — read-only change set
# ---------------------------------------------------------------------------


def test_change_set_touches_only_docs_and_bin() -> None:
    if not SCRIPT.exists():  # pragma: no cover - repo layout guard
        pytest.skip("inventory script not present")
    probe = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "--quiet", "origin/main"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        pytest.skip("origin/main is unavailable (shallow checkout); change-set guard needs the base ref")
    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "origin/main...HEAD"],
        capture_output=True, text=True, check=False,
    )
    assert diff.returncode == 0, diff.stderr
    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    if not changed:
        pytest.skip("HEAD has no committed changes against origin/main yet; nothing to inspect")
    changed = list(changed)
    offenders = [path for path in changed if not (path.startswith("docs/") or path.startswith("bin/"))]
    assert not offenders, f"change set is not read-only: {offenders}"
    assert not any(path.startswith("sql/") for path in changed)


# ---------------------------------------------------------------------------
# the committed artifact must be current (enforced in CI)
# ---------------------------------------------------------------------------


def test_committed_artifact_is_current() -> None:
    assert ARTIFACT_JSON.is_file(), "docs/lilac/executable-topology.json is missing"
    assert ARTIFACT_MD.is_file(), "docs/lilac/executable-topology.md is missing"
    rc = TOPOLOGY.main(["--check", "--no-probe", "--root", str(REPO_ROOT)])
    assert rc == 0, "committed topology artifacts are stale; regenerate with bin/lilac-topology-inventory.py"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
