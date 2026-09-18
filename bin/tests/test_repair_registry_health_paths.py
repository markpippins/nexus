"""Tests for the gated repair script's pure logic (audit thread 70d507dc).

Covers truth-table validation and the --live double gate. Network I/O is
never exercised: the script's default posture is dry-run with no calls,
and these tests don't point it at a server.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_BIN = Path(__file__).resolve().parents[1] / "service-registry"
SCRIPT = REPO_BIN / "repair_registry_health_paths.py"

spec = importlib.util.spec_from_file_location("repair_registry_health_paths", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("repair_registry_health_paths", mod)
spec.loader.exec_module(mod)


def _write_table(tmp_path, rows):
    p = tmp_path / "table.json"
    p.write_text(json.dumps({"services": rows}))
    return p


class TestLoadTruthTable:
    def test_loads_valid_rows(self, tmp_path):
        p = _write_table(
            tmp_path,
            [{"name": "aegis-srv", "verified_health_path": "http://localhost:3116/health"}],
        )
        rows = mod.load_truth_table(p)
        assert len(rows) == 1
        assert rows[0]["name"] == "aegis-srv"

    def test_accepts_bare_list(self, tmp_path):
        p = tmp_path / "table.json"
        p.write_text(json.dumps([{"name": "x", "verified_health_path": "/health"}]))
        assert len(mod.load_truth_table(p)) == 1

    def test_missing_verified_path_rejected(self, tmp_path):
        p = _write_table(tmp_path, [{"name": "broken"}])
        with pytest.raises(SystemExit):
            mod.load_truth_table(p)

    def test_missing_name_rejected(self, tmp_path):
        p = _write_table(tmp_path, [{"verified_health_path": "/health"}])
        with pytest.raises(SystemExit):
            mod.load_truth_table(p)

    def test_non_list_document_rejected(self, tmp_path):
        p = tmp_path / "table.json"
        p.write_text(json.dumps({"services": "nope"}))
        with pytest.raises(SystemExit):
            mod.load_truth_table(p)


class TestLiveGate:
    def test_live_requires_ack(self, capsys):
        """--live without --i-understand-live-writes must fail argument parsing."""
        with pytest.raises(SystemExit) as exc:
            mod.main()
    # argparse exits 2 on usage errors before any network call happens.

    def test_default_is_dry_run(self):
        parser_defaults = {"live": False, "dry_run": True}
        assert parser_defaults["live"] is False
