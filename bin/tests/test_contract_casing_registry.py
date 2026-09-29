"""Decision 18 storage-shaped exemption registry — both directions.

The ruling requires the tester to verify this in two directions, explicitly:

    "Re-verify the ratchet under the registry, both directions: exempt-declared fields pass;
     undeclared snake_case fails (including a same-family-but-unlisted field)."

So the negative case is the one that matters most. An exemption registry that only ever
exempts is a suppression list, and the whole point of the ruling over a blanket family
exemption is that the ratchet keeps measuring drift.

Run: python3 -m pytest bin/tests/test_contract_casing_registry.py -q
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = ROOT / "bin" / "check_contract_casing.py"
REGISTRY = ROOT / "bin" / "contract-casing-storage-exempt.json"
BASELINE = ROOT / "bin" / "contract-casing-baseline.json"

spec = importlib.util.spec_from_file_location("ccc", CHECKER)
ccc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccc)


def entries():
    return ccc.load_registry()


def tmp_registry(payload):
    """Point the checker's REGISTRY at a temp file and reload. Restores on exit."""
    path = pathlib.Path("/tmp/ccc-registry-test.json")
    path.write_text(json.dumps(payload))
    original = ccc.REGISTRY
    ccc.REGISTRY = path
    return path, original


# ── registry contents ────────────────────────────────────────────────────────

def test_registry_exists_and_is_non_empty():
    assert REGISTRY.exists(), "Decision 18 registry file is missing"
    assert entries(), "registry has no entries; the ratchet would be measuring nothing"


def test_every_entry_names_a_real_table_column():
    """The column requirement is the mechanism that stops this becoming a suppression list."""
    for entry in entries():
        column = entry["column"]
        assert "." in column, f"{entry['field']}: column {column!r} is not table.column"
        assert not column.startswith(".") and not column.endswith("."), \
            f"{entry['field']}: column {column!r} is malformed"


def test_every_entry_cites_pr_and_rationale():
    """Decision 13 condition 2: cite the PR + column; no silent growth."""
    for entry in entries():
        assert entry["introduced_by"].strip(), f"{entry['field']}: no introducing PR/commit"
        assert entry["rationale"].strip(), f"{entry['field']}: no rationale"


def test_every_entry_is_actually_snake_case():
    """A camelCase entry could never be a violation, so it is dead weight at best."""
    for entry in entries():
        assert ccc.SNAKE.match(entry["field"]), \
            f"{entry['field']} is not snake_case and could never be exempt"


def test_no_duplicate_entries():
    seen = set()
    for entry in entries():
        key = (entry["field"], entry.get("contract") or "*")
        assert key not in seen, f"duplicate registry entry for {key}"
        seen.add(key)


# ── validation: a malformed registry must HARD FAIL, not under-exempt ─────────

def test_registry_entry_without_a_column_is_rejected():
    """A bare field with no column is exactly the suppression this ruling forbids."""
    _, original = tmp_registry({"entries": [
        {"field": "made_up_field", "introduced_by": "#1", "rationale": "trust me"}]})
    try:
        try:
            ccc.load_registry()
        except ccc.RegistryError as exc:
            assert "column" in str(exc)
            return
        raise AssertionError("a registry entry with no column must be rejected")
    finally:
        ccc.REGISTRY = original


def test_registry_entry_missing_rationale_is_rejected():
    _, original = tmp_registry({"entries": [
        {"field": "some_field", "column": "t.c", "introduced_by": "#1"}]})
    try:
        try:
            ccc.load_registry()
        except ccc.RegistryError as exc:
            assert "rationale" in str(exc)
            return
        raise AssertionError("a registry entry with no rationale must be rejected")
    finally:
        ccc.REGISTRY = original


def test_registry_entry_with_non_snake_field_is_rejected():
    _, original = tmp_registry({"entries": [
        {"field": "camelCase", "column": "t.c", "introduced_by": "#1", "rationale": "x"}]})
    try:
        try:
            ccc.load_registry()
        except ccc.RegistryError:
            return
        raise AssertionError("a camelCase registry entry must be rejected as dead weight")
    finally:
        ccc.REGISTRY = original


def test_duplicate_entry_is_rejected():
    _, original = tmp_registry({"entries": [
        {"field": "dup_field", "column": "t.c", "introduced_by": "#1", "rationale": "x"},
        {"field": "dup_field", "column": "t.c", "introduced_by": "#2", "rationale": "y"}]})
    try:
        try:
            ccc.load_registry()
        except ccc.RegistryError as exc:
            assert "duplicate" in str(exc)
            return
        raise AssertionError("a duplicate registry entry must be rejected")
    finally:
        ccc.REGISTRY = original


def test_malformed_registry_exits_nonzero_end_to_end():
    """A registry that cannot be validated must fail the check, not be silently ignored.

    Driven in-process through main(): a subprocess reads the real registry path, so it cannot
    observe a monkeypatched one, and the original version of this test asserted nothing about
    the code it claimed to cover.
    """
    path, original = tmp_registry({"entries": [
        {"field": "bad_field", "introduced_by": "#1", "rationale": "no column here"}]})
    argv = sys.argv
    try:
        sys.argv = ["check_contract_casing.py"]
        code = ccc.main()
        assert code != 0, "a malformed registry must fail the check, not warn"
    finally:
        sys.argv = argv
        ccc.REGISTRY = original


# ── direction 1: declared-exempt fields DO NOT count ─────────────────────────

def test_declared_exempt_field_is_exempt():
    reg = entries()
    declared = reg[0]
    assert ccc.is_exempt(declared["field"], declared.get("contract") or "", reg), \
        f"{declared['field']} is declared but reports as not exempt"


def test_real_registry_lowers_the_count_but_reports_both():
    data = ccc.scan(entries())
    assert data["exempt_total"] > 0, "no field in the real registry is being exempted"
    assert data["leak_total"] == data["leak_total_raw"] - data["exempt_total"]
    assert data["leak_total"] < data["leak_total_raw"]


# ── direction 2: UNDECLARED fields still fail — the direction that matters ────

def test_undeclared_snake_field_in_a_different_contract_is_NOT_exempt():
    """Scoping matters: a declaration in one contract must not excuse another."""
    declared = entries()[0]
    other = "typespec/v1/some-other-service/typescript/models.tsp"
    assert not ccc.is_exempt(declared["field"], other, entries()), \
        f"{declared['field']} is scoped to {declared.get('contract')} and must not leak to {other}"


def test_same_family_but_unlisted_field_is_a_violation():
    """The exact case the ruling calls out: inside the CDLC family, but not in the registry."""
    unlisted = "report_ids_with_no_findings"  # a census read-model field, deliberately NOT registered
    assert ccc.SNAKE.match(unlisted), "fixture must actually be snake_case"
    assert not ccc.is_exempt(unlisted, "typespec/v1/nexus-broker/typescript/models.tsp", entries()), \
        f"{unlisted} is NOT in the registry and must count as a violation"


def test_no_blanket_family_exemption():
    """Decision 18 rejected a family exemption (option c). Guard against one creeping back."""
    scopes = {e.get("contract") for e in entries()}
    assert None not in scopes or len(entries()) == 0, \
        "an unscoped entry would exempt the field everywhere; scope every entry"
    for entry in entries():
        assert entry.get("contract"), \
            f"{entry['field']} is unscoped; Decision 18 is a field-level registry, not a family one"


def test_derived_aggregates_are_not_registered():
    """Fields with no underlying column cannot be registered; the schema should refuse them."""
    reg = json.loads(REGISTRY.read_text())
    not_registered = set()
    for note in reg.get("_deliberately_not_registered", []):
        if note.get("field"):
            not_registered.add(note["field"])
        not_registered.update(note.get("fields", []))
    for field in not_registered:
        assert not ccc.is_exempt(field, "", entries()), \
            f"{field} is recorded as deliberately not registered but is exempt"


def test_registered_fields_all_have_a_nebula_executions_column():
    """Every entry in the shipped registry was introduced by #639 and mirrors a real column."""
    for entry in entries():
        assert entry["introduced_by"] == "#639", \
            f"{entry['field']} cites {entry['introduced_by']}, not #639"
        assert entry["column"].startswith("nebula.executions."), \
            f"{entry['field']} cites {entry['column']}, which is not a nebula.executions column"


# ── end-to-end ratchet behaviour ─────────────────────────────────────────────

def test_checker_exits_zero_at_the_recalculated_floor():
    proc = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, f"ratchet should pass at the floor:\n{proc.stdout}\n{proc.stderr}"


def test_floor_is_the_unexempt_count_not_the_raw_count():
    baseline = json.loads(BASELINE.read_text())
    data = ccc.scan(entries())
    assert baseline["leaks"] == data["leak_total"], \
        (f"floor {baseline['leaks']} != unexempt count {data['leak_total']} "
         f"(raw {data['leak_total_raw']})")


def test_registry_flag_prints_the_registry():
    proc = subprocess.run([sys.executable, str(CHECKER), "--registry"],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0
    assert "declared entries" in proc.stdout
    assert "deliberately NOT registered" in proc.stdout, \
        "the registry should surface what it deliberately does NOT exempt"
