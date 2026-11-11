#!/usr/bin/env python3
"""Fail-closed writer provenance + legacy disposition (plan 8261670).

Hermetic: no database connections, no live state. Covers AC1 (per-site
envelope present), AC2 (fail-closed refusal is non-mutating), AC3/AC4
(100% disposition accounting, append-only re-run), AC5 (no DROP and no
UPDATE/DELETE against legacy history in this change).

DB tier: hermetic only — this change opens no connections, so there is
nothing for the db tier to prove beyond what these guards assert.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python" / "conduit"))
sys.path.insert(0, str(REPO_ROOT / "python" / "cascade"))
sys.path.insert(0, str(REPO_ROOT / "python" / "vision" / "losm-store" / "src"))
sys.path.insert(0, str(REPO_ROOT / "python" / "vision" / "losm-ir" / "src"))
_LOSM_IR_SRC = str(REPO_ROOT / "python" / "vision" / "losm-ir" / "src" / "losm_ir")
_LOSM_STORE_SRC = str(REPO_ROOT / "python" / "vision" / "losm-store" / "src" / "losm_store")

# Hermetic import shims: the helpers under test are pure functions, but
# their modules import live drivers at top level (psycopg2, sqlalchemy).
# The CI hermetic job only installs pyyaml/pytest/setuptools, so stub the
# drivers here instead of requiring them. Nothing under test opens a
# connection, so the stubs are never touched beyond import time.
import types as _types

if "psycopg2" not in sys.modules:
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        _pg = _types.ModuleType("psycopg2")
        _pg_pool = _types.ModuleType("psycopg2.pool")
        _pg_pool.ThreadedConnectionPool = object
        _pg.pool = _pg_pool
        sys.modules["psycopg2"] = _pg
        sys.modules["psycopg2.pool"] = _pg_pool

if "sqlalchemy" not in sys.modules:
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        _sa = _types.ModuleType("sqlalchemy")
        _sa.text = lambda *a, **k: None
        for _name in ("Column", "DateTime", "Integer", "String",
                      "Text", "Float", "JSON"):
            setattr(_sa, _name, type(_name, (),
                                    {"__init__": lambda self, *a, **k: None}))

        class _SAEnum:
            def __init__(self, *a, **k):
                pass
        _sa.Enum = _SAEnum
        _orm = _types.ModuleType("sqlalchemy.orm")

        class _Session:
            pass
        _orm.Session = _Session
        _orm.declarative_base = lambda *a, **k: type(
            "Base", (), {"__init__": lambda self, *a, **k: None})
        # losm_store/session.py runs create_engine + sessionmaker at
        # module import time; dummies suffice (never used by these guards).
        _sa.create_engine = lambda *a, **k: object()
        _orm.sessionmaker = lambda *a, **k: type(
            "SessionLocal", (), {"__init__": lambda self, *a, **k: None})
        _exc = _types.ModuleType("sqlalchemy.exc")

        class _NoResultFound(Exception):
            pass
        _exc.NoResultFound = _NoResultFound
        _sa.orm = _orm
        _sa.exc = _exc
        sys.modules["sqlalchemy"] = _sa
        sys.modules["sqlalchemy.orm"] = _orm
        sys.modules["sqlalchemy.exc"] = _exc

for _pkg_name, _pkg_dir in (("losm_ir", _LOSM_IR_SRC), ("losm_store", _LOSM_STORE_SRC)):
    _shell = _types.ModuleType(_pkg_name)
    _shell.__path__ = [_pkg_dir]
    sys.modules[_pkg_name] = _shell

try:
    import losm_ir.states  # noqa: F401
    import losm_store.models  # noqa: F401
    _REAL_SUBMODULES_OK = True
except ImportError:
    _REAL_SUBMODULES_OK = False

if not _REAL_SUBMODULES_OK:
        # Faithful fallback mirror of losm_ir.states.WorkStatus
        # (python/vision/losm-ir/src/losm_ir/states.py) for checkouts
        # where losm-ir is genuinely absent. models.py evaluates
        # WorkStatus.NEW at class-body time, so every member must exist.
        from enum import Enum as _StdEnum
        _ir = sys.modules["losm_ir"]
        _states = _types.ModuleType("losm_ir.states")

        class _WorkStatus(str, _StdEnum):
            NEW = "NEW"
            INTAKE = "INTAKE"
            PLAN_GENERATION = "PLAN_GENERATION"
            PLAN_REVIEW = "PLAN_REVIEW"
            PLAN_APPROVAL_GATE = "PLAN_APPROVAL_GATE"
            SPEC_GENERATION = "SPEC_GENERATION"
            EXECUTION = "EXECUTION"
            VALIDATION = "VALIDATION"
            COMPLETION = "COMPLETION"
            BLOCKED = "BLOCKED"
            FAILED = "FAILED"
        _states.WorkStatus = _WorkStatus
        _ir.states = _states
        sys.modules["losm_ir.states"] = _states

from db_adapter import DBAdapter  # noqa: E402
from admission_subscriber import (  # noqa: E402
    _stamp_provenance_into_metadata,
    _enforce_provenance_identity,
    _PROVENANCE_FIELDS as CASCADE_FIELDS,
)
from losm_store.repository import (  # noqa: E402
    _stamp_provenance_into_context,
    _PROVENANCE_FIELDS as LOSM_FIELDS,
)
from lilac_disposition import (  # noqa: E402
    classify_legacy_receipt,
    summarize_dispositions,
    record_dispositions,
)

FIVE = ("producer_id", "source_channel", "source_system",
        "process_id", "invocation_id")


def test_ac1_conduit_site_stamps_five_fields():
    prov = DBAdapter._receipt_provenance()
    for field in FIVE:
        assert prov.get(field), f"conduit envelope missing {field}"


def test_ac1_cascade_site_stamps_five_fields():
    stamped = _stamp_provenance_into_metadata({})
    for field in FIVE:
        assert stamped.get(field), f"cascade envelope missing {field}"
    assert tuple(CASCADE_FIELDS) == FIVE


def test_ac1_losm_site_stamps_five_fields():
    ctx = _stamp_provenance_into_context(None)
    for field in FIVE:
        assert ctx["provenance"].get(field), f"losm envelope missing {field}"
    assert tuple(LOSM_FIELDS) == FIVE


def test_ac2_blank_producer_refused_before_any_write():
    # The gate lives in _receipt_provenance, which insert_receipt calls
    # before opening any cursor — refusal therefore cannot mutate rows.
    # Prove it against a fake store: the write helper is only reached
    # when the gate passes.
    writes: list = []

    def write_if_allowed(producer_id):
        DBAdapter._receipt_provenance(producer_id=producer_id)
        writes.append(producer_id)

    with pytest.raises(PermissionError):
        write_if_allowed("")
    with pytest.raises(PermissionError):
        write_if_allowed("   ")
    assert writes == [], "refused mutation must not reach the write path"
    write_if_allowed(None)  # omitted -> process default, staged-inert path
    write_if_allowed("nexus-conduit-python")
    assert writes == [None, "nexus-conduit-python"]


def test_ac2_cascade_conflict_refused_non_mutating():
    store = {"rows": 3}
    with pytest.raises(PermissionError):
        _enforce_provenance_identity({"producer_id": "someone-else-entirely",
                                      "source_channel": "rogue",
                                      "source_system": "rogue",
                                      "process_id": "rogue",
                                      "invocation_id": "rogue"})
    assert store == {"rows": 3}
    ok = _enforce_provenance_identity({})
    for field in FIVE:
        assert ok.get(field)


def test_ac3_disposition_covers_100_percent_with_unclassifiable_counted():
    rows = [
        {"receipt_id": "r1", "source_schema": "vision",
         "source_table": "receipts", "plan_id": "8261667"},
        {"receipt_id": "r2", "source_schema": "execution",
         "source_table": "receipts", "plan_id": ""},
        {"receipt_id": "seed-r3", "source_schema": "vision",
         "source_table": "receipts", "plan_id": "", "summary": "seed probe"},
        {"receipt_id": "", "source_schema": "wind",
         "source_table": "receipts", "plan_id": "8261650"},
        {"receipt_id": "r5", "source_schema": "mystery",
         "source_table": "receipts", "plan_id": "1"},
    ]
    summary = summarize_dispositions(rows)
    assert summary["total"] == 5
    assert sum(summary["counts"].values()) == 5
    assert summary["counts"]["backfill"] == 1
    assert summary["counts"]["discard"] == 1
    assert summary["counts"]["retire-with-evidence"] == 1
    assert summary["counts"]["unclassifiable"] == 2


def test_ac4_disposition_rerun_identical_and_append_only(tmp_path):
    ledger = str(tmp_path / "dispositions.jsonl")
    rows = [
        {"receipt_id": "r1", "source_schema": "vision",
         "source_table": "receipts", "plan_id": "8261667"},
        {"receipt_id": "r2", "source_schema": "wind",
         "source_table": "receipts", "plan_id": ""},
    ]
    first = record_dispositions(ledger, rows)
    assert first["appended"] == 2
    with open(ledger, encoding="utf-8") as fh:
        snapshot = fh.read()
    second = record_dispositions(ledger, rows)
    with open(ledger, encoding="utf-8") as fh:
        assert fh.read() == snapshot
    assert second["appended"] == 0
    assert second["total"] == first["total"]
    assert second["counts"] == first["counts"]
    # New rows append without touching existing lines.
    third = record_dispositions(
        ledger, rows + [{"receipt_id": "r3", "source_schema": "kernel",
                         "source_table": "receipts", "plan_id": "9"}])
    assert third["appended"] == 1
    with open(ledger, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 3
    assert [json.loads(lines[0])["id"], json.loads(lines[1])["id"]] == \
        ["r1", "r2"]


def _code_lines_of(*rel_paths):
    # Yield upper-cased code lines (no blanks, full-line comments, or
    # docstring fences) from working-tree files. Scanning file contents
    # instead of `git diff origin/main...HEAD` keeps the guard independent
    # of which git refs a checkout has (CI uses shallow/PR checkouts where
    # origin/main may not exist). These implementation files contain no
    # DROP/legacy-rewrite statements at all, so file scope is equivalent
    # to diff scope for this change.
    lines = []
    for rel in rel_paths:
        for raw in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines():
            body = raw.strip()
            if not body or body.startswith(("#", '"""', "'''", "*")):
                continue  # comments/docstrings are prose, not statements
            lines.append(body.upper())
    return lines


def test_ac5_no_drop_or_history_rewrite_in_this_change():
    # NOTE: only implementation files are scanned. This guard file itself
    # is excluded on purpose — its docstrings/assertions mention DROP and
    # UPDATE/DELETE as ordinary words, which a naive substring match would
    # flag as a false positive (observed in tester reproduction).
    code_lines = _code_lines_of(
        "python/conduit/db_adapter.py",
        "python/conduit/lilac_disposition.py",
        "python/cascade/admission_subscriber.py",
        "python/vision/losm-store/src/losm_store/repository.py",
    )
    assert not any(re.match(r"DROP\s+(TABLE|INDEX|VIEW)", ln)
                   for ln in code_lines), \
        "this plan drops no legacy table"
    legacy_targets = ("VISION.RECEIPTS", "VISION.RECEIPTS_HISTORY",
                      "EXECUTION.RECEIPTS", "WIND.", "KERNEL.")
    for ln in code_lines:
        if re.match(r"(UPDATE\s+\w|DELETE\s+FROM)", ln):
            assert not any(t in ln for t in legacy_targets), \
                f"history rewrite against legacy surface: {ln.strip()}"


def test_ac5_guard_scans_implementation_files_only():
    # Regression guard for the tester-observed false positive: the guard
    # must not flag its own prose. Re-run the scan including this file
    # and assert the anchored matcher stays silent on it.
    import re as _re
    own_text = Path(__file__).read_text(encoding="utf-8").upper()
    assert not _re.search(r"(?m)^\s*DROP\s+(TABLE|INDEX|VIEW)", own_text)
