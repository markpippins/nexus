#!/usr/bin/env python3
"""Guards for the Lilac W1-L6 cutover gate (plan 8261672).

Plan 8261672 discharges parent 8261639 AC10/AC11. Its six acceptance criteria
are pinned here against bin/lilac-parity-check.py:

  AC1  dual-read parity is MEASURED over a bounded window: matched, mismatched
       and unreadable are separate counts, an unreadable row is counted rather
       than skipped, and any unexplained class fails the gate closed.
  AC2  zero direct legacy writes is proven by a pg_stat_user_tables delta that
       NAMES every legacy table that took a write — and treats a missing or
       regressed counter as unreadable evidence, not as proof of silence.
  AC3  replaying an identical mutation stream yields identical folded state and
       adds no duplicate canonical row.
  AC4  rollback at the cutover boundary is rehearsed on a throwaway database:
       the cutover swaps the legacy table for a view over the canonical surface,
       and the rollback restores the pre-cutover read path leaving nothing.
  AC5  the legacy drop is a SEPARATE operator-gated step: this tool emits no
       drop DDL at all, and asking it to is a documented refusal.
  AC6  scope exclusion: the change set grows no WorkRequest representation, no
       request_kind, and no peb.decisions authority.

The AC4 half needs a real PostgreSQL and runs in the `guards-db` tier. The other
five are hermetic and run anywhere.

DB tier: bin/tests-ci-manifest.json -> tiers.db
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bin"))
if str(REPO_ROOT / "bin" / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bin" / "tests"))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

TOOL = REPO_ROOT / "bin" / "lilac-parity-check.py"


def load_tool():
    """Import bin/lilac-parity-check.py by path (the name is not importable)."""
    spec = importlib.util.spec_from_file_location("lilac_parity_check", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pc = load_tool()


def legacy_row(**over):
    row = {"id": "r-1", "type": "IMPLEMENTATION", "plan_id": "8261672",
           "agent_role": "builder", "session_id": "s-1", "ticket_id": None,
           "summary": "did the thing", "created_at": 1_700_000_000_000}
    row.update(over)
    return row


def twin(**over):
    payload = {"receipt_type": "IMPLEMENTATION", "plan_id": "8261672",
               "agent_role": "builder", "session_id": "s-1", "ticket_id": None,
               "summary": "did the thing"}
    out = {"kind": "implementation", "payload": payload, "contract_version": 1}
    out.update(over)
    return out


KINDS = {"IMPLEMENTATION": "implementation", "REVIEW_PASS": "review_pass"}


# ── AC1: measured parity, fail closed ────────────────────────────────────────

class TestParityMeasurement:
    def test_matched_row_is_parity_and_not_counted_as_mismatch(self):
        report = pc.parity_report([legacy_row()], lambda r: twin(), KINDS.get)
        assert report["counts"]["parity"] == 1
        assert report["matched"] == 1
        assert report["mismatched"] == 0
        assert report["fail_closed"] is False

    def test_counts_are_split_not_ratioed(self):
        """AC1 asks for matched AND mismatched AND unreadable — never a percentage."""
        rows = [
            legacy_row(id="ok"),
            legacy_row(id="no-twin"),
            legacy_row(id="", type="IMPLEMENTATION"),
        ]
        twins = {"ok": twin()}
        report = pc.parity_report(rows, lambda r: twins.get(r["id"]), KINDS.get)
        assert report["measured"] == 3
        assert report["counts"]["parity"] == 1
        assert report["counts"]["missing_twin"] == 1
        assert report["counts"]["unreadable"] == 1
        assert report["mismatched"] == 2
        assert report["unreadable"] == 1
        assert report["fail_closed"] is True

    def test_every_input_row_is_accounted_for(self):
        rows = [legacy_row(id="a"), legacy_row(id="b", type="PROPOSED"),
                legacy_row(id="c")]
        report = pc.parity_report(rows, lambda r: twin() if r["id"] != "c" else None,
                                  KINDS.get)
        assert sum(report["counts"].values()) == len(rows)

    def test_unmapped_legacy_type_is_explained_not_drift(self):
        report = pc.parity_report([legacy_row(type="PROPOSED")], lambda r: None,
                                  KINDS.get)
        assert report["counts"]["unmapped_type"] == 1
        assert report["fail_closed"] is False

    def test_explained_and_unexplained_classes_are_disjoint(self):
        assert not (pc.EXPLAINED_CLASSES & pc.UNEXPLAINED_CLASSES)
        assert set(pc.ALL_CLASSES) == pc.EXPLAINED_CLASSES | pc.UNEXPLAINED_CLASSES

    def test_payload_drift_names_the_row(self):
        drifted = twin()
        drifted["payload"]["summary"] = "something else"
        report = pc.parity_report([legacy_row()], lambda r: drifted, KINDS.get)
        assert report["counts"]["payload_drift"] == 1
        assert [f["receipt_id"] for f in report["unexplained_findings"]] == ["r-1"]

    def test_kind_drift_and_contract_version_are_unexplained(self):
        report = pc.parity_report([legacy_row(id="k")],
                                  lambda r: twin(kind="review_pass"), KINDS.get)
        assert report["counts"]["kind_drift"] == 1
        report = pc.parity_report([legacy_row(id="v")],
                                  lambda r: twin(contract_version=2), KINDS.get)
        assert report["counts"]["payload_drift"] == 1

    def test_raised_read_is_unreadable_not_a_crash(self):
        def boom(row):
            raise RuntimeError("connection reset")

        report = pc.parity_report([legacy_row()], boom, KINDS.get)
        assert report["counts"]["unreadable"] == 1
        assert "connection reset" in report["unexplained_findings"][0]["error"]

    def test_window_excludes_rows_outside_it(self):
        old = legacy_row(id="old", created_at=1_000_000)
        now = 1_700_000_000_000
        report = pc.parity_report([old, legacy_row(id="new", created_at=now)],
                                  lambda r: twin(), KINDS.get,
                                  window_hours=24, now_ms=now)
        assert report["measured"] == 1
        assert report["window_start_ms"] == now - 24 * 3600 * 1000

    def test_check_mode_exits_one_on_unexplained_and_zero_when_clean(self, tmp_path):
        clean = tmp_path / "clean.json"
        clean.write_text(json.dumps({"legacy": [legacy_row()],
                                     "twins": {"r-1": twin()},
                                     "kinds": KINDS}))
        assert pc.main(["--from-json", str(clean), "--check"]) == 0

        dirty = tmp_path / "dirty.json"
        dirty.write_text(json.dumps({"legacy": [legacy_row()], "twins": {},
                                     "kinds": KINDS}))
        assert pc.main(["--from-json", str(dirty), "--check"]) == 1

    def test_no_input_is_a_config_error_not_a_pass(self):
        assert pc.main([]) == 2


# ── AC2: zero direct legacy writes ───────────────────────────────────────────

def snapshot(overrides=None):
    """A pg_stat_user_tables snapshot with per-(schema, table) counter overrides."""
    rows = [{"schemaname": s, "relname": t, "inserts": 0, "updates": 0, "deletes": 0}
            for s, t in pc.LEGACY_TABLES]
    for (schema, table), delta in (overrides or {}).items():
        for row in rows:
            if (row["schemaname"], row["relname"]) == (schema, table):
                row.update(delta)
    return rows


class TestWriteAudit:
    def test_flat_counters_prove_no_direct_writer(self):
        audit = pc.write_audit_delta(snapshot(), snapshot())
        assert audit["clean"] is True
        assert audit["writers"] == []
        assert audit["audited"] == ["%s.%s" % t for t in pc.LEGACY_TABLES]

    def test_a_write_is_named_not_summarised(self):
        after = snapshot({("vision", "receipts"): {"inserts": 3}})
        audit = pc.write_audit_delta(snapshot(), after)
        assert audit["clean"] is False
        assert [(w["schema"], w["table"]) for w in audit["writers"]] == [("vision", "receipts")]
        assert audit["writers"][0]["total"] == 3

    def test_deletes_and_updates_also_count_as_writes(self):
        after = snapshot({("wind", "receipts"): {"deletes": 1},
                          ("kernel", "receipts"): {"updates": 2}})
        audit = pc.write_audit_delta(snapshot(), after)
        assert {w["table"] for w in audit["writers"]} == {"receipts"}
        assert {w["schema"] for w in audit["writers"]} == {"wind", "kernel"}

    def test_missing_table_is_unreadable_not_clean(self):
        before = snapshot()
        after = [r for r in snapshot()
                 if (r["schemaname"], r["relname"]) != ("nebula", "receipts_unified")]
        audit = pc.write_audit_delta(before, after)
        assert audit["clean"] is False
        assert audit["unreadable"][0]["table"] == "receipts_unified"

    def test_regressed_counter_is_not_evidence_of_silence(self):
        before = snapshot({("vision", "receipts"): {"inserts": 5}})
        after = snapshot()
        audit = pc.write_audit_delta(before, after)
        assert audit["clean"] is False
        assert "regressed" in audit["unreadable"][0]["reason"]

    def test_audit_sql_targets_only_legacy_tables(self):
        for schema, table in pc.LEGACY_TABLES:
            assert "('%s', '%s')" % (schema, table) in pc.WRITE_AUDIT_SQL
        assert pc.WRITE_AUDIT_SQL.split().count("SELECT") == 1  # read-only


# ── AC3: replay equivalence ──────────────────────────────────────────────────

class TestReplay:
    def stream(self):
        return [
            {"idempotency_key": "k1", "state": {"status": "open"}},
            {"idempotency_key": "k2", "state": {"status": "open"}},
            {"idempotency_key": "k1", "state": {"status": "closed"}},
        ]

    def test_replay_of_the_same_stream_folds_identically(self):
        result = pc.replay_equivalence(self.stream())
        assert result["identical"] is True
        assert result["folded_keys"] == 2
        # k1 appears twice in the stream by design (a supersession, last-write
        # wins), so duplicate detection is about extra ROWS, not about keys.
        assert result["duplicates_in_stream"] == ["k1"]

    def test_fold_is_last_write_wins(self):
        state = pc.replay_fold(self.stream())
        assert state["k1"] == {"status": "closed"}

    def test_missing_idempotency_key_is_refused(self):
        with pytest.raises(ValueError):
            pc.replay_fold([{"state": {"status": "open"}}])

    def test_replay_adds_no_row(self):
        rows = [{"idempotency_key": "k%d" % i, "state": {"n": i}} for i in range(5)]
        first = pc.replay_fold(rows)
        second = pc.replay_fold(list(rows))
        assert first == second
        assert len(second) == 5


# ── AC5: the drop stays operator-gated ───────────────────────────────────────

class TestLegacyDropStaysGated:
    def test_tool_emits_no_drop_ddl(self, capsys):
        assert pc.main(["--print-sql", "legacy-drop"]) == 2
        out = capsys.readouterr()
        assert "DROP" not in out.out          # nothing on stdout
        assert "OPERATOR-GATED" in out.err

    def test_rollback_drops_only_the_view_it_created(self):
        assert "DROP VIEW vision.receipts;" in pc.ROLLBACK_SQL
        assert "DROP TABLE" not in pc.ROLLBACK_SQL
        assert "TRUNCATE" not in pc.CUTOVER_SQL + pc.ROLLBACK_SQL

    def test_cutover_preserves_the_legacy_table_under_a_new_name(self):
        assert "RENAME TO receipts_pre_cutover" in pc.CUTOVER_SQL
        assert "DROP" not in pc.CUTOVER_SQL

    def test_no_cli_mode_drops_a_legacy_table(self, capsys):
        for mode in ("cutover", "rollback", "write-audit"):
            pc.main(["--print-sql", mode])
            assert "DROP TABLE" not in capsys.readouterr().out


# ── AC6: scope exclusion ─────────────────────────────────────────────────────

# The implementation surface of this plan. This test file is deliberately NOT
# scanned: the detector's fixtures have to contain the forbidden tokens, so
# including it would make the guard unsatisfiable by construction.
CHANGE_SET = (
    "bin/lilac-parity-check.py",
    "docs/lilac/cutover-rehearsal.md",
)


def code_only(path: Path) -> str:
    """Identifiers in `path`, with docstrings, comments and string literals gone.

    The exclusion is about code that USES these surfaces. This module has to
    NAME them in order to exclude them, so scanning prose would fail on the
    exclusion notice itself. Names and attribute chains survive; quotes do not.
    """
    if path.suffix != ".py":
        return path.read_text(encoding="utf-8")
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts, cur = [], node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            tokens.append(".".join(reversed(parts)))
    return "\n".join(tokens)


class TestScopeExclusion:
    def test_change_set_carries_no_forbidden_surface(self):
        texts = {}
        for rel in CHANGE_SET:
            path = REPO_ROOT / rel
            assert path.is_file(), rel
            texts[rel] = code_only(path)
        assert pc.scope_violations(texts) == []

    def test_detector_actually_detects(self):
        hits = pc.scope_violations({
            "x.py": "row = work_request(kind)\nrequest_kind = 'build'\n"
                    "GRANT peb.decisions TO r;\n"})
        assert {h["surface"] for h in hits} == {"work_request", "request_kind", "peb.decisions"}


# ── AC4: rollback rehearsal on a throwaway database ──────────────────────────

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w", "-v", "ON_ERROR_STOP=1", "-qAt")

SKELETON = """
CREATE SCHEMA vision;
CREATE SCHEMA resolution;
CREATE TABLE vision.receipts (
  id text PRIMARY KEY,
  type text,
  plan_id text,
  agent_role text,
  session_id text,
  ticket_id text,
  summary text,
  created_at timestamptz
);
CREATE TABLE resolution.receipt (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_receipt_id text UNIQUE,
  payload jsonb,
  created_at timestamptz
);
INSERT INTO vision.receipts VALUES
  ('legacy-1', 'IMPLEMENTATION', '8261672', 'builder', 's-1', NULL, 'did the thing', now()),
  ('legacy-2', 'REVIEW_PASS',    '8261672', 'reviewer', 's-2', NULL, 'looked at it',  now());
INSERT INTO resolution.receipt (source_receipt_id, payload, created_at) VALUES
  ('legacy-1', '{"receipt_type":"IMPLEMENTATION","plan_id":"8261672","agent_role":"builder","session_id":"s-1","ticket_id":null,"summary":"did the thing"}', now());
"""


def psql(db: str, sql: str) -> str:
    return subprocess.run(
        PSQL + ("-d", db, "-c", sql), env=ENV, check=True,
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _pg_reachable() -> bool:
    """True only when an AUTHENTICATED query works.

    An open port proves nothing here (the same lesson the honest Mongo probe
    records): a server that answers and then refuses auth would fail the
    rehearsal for the wrong reason. The guards-db tier provides passwordless
    access; a developer machine may need PGPASSWORD set.
    """
    try:
        return subprocess.run(
            ("psql", "-w", "-qAt", "-d", "postgres", "-c", "select 1"),
            env=ENV, capture_output=True, stdin=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        return False


@skip_unless_host_tools(
    "createdb", "dropdb", "psql",
    doing="the cutover rollback rehearsal creates a throwaway database",
)
class TestRollbackRehearsal:
    @pytest.fixture()
    def db(self):
        if not _pg_reachable():
            pytest.skip("no authenticated PostgreSQL at PGHOST=%s PGPORT=%s "
                        "(the guards-db tier provides one; set PGPASSWORD locally)"
                        % (ENV["PGHOST"], ENV["PGPORT"]))
        name = "lilac_cutover_%s" % uuid.uuid4().hex[:12]
        subprocess.run(("createdb", "-w", name), env=ENV, check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        try:
            psql(name, SKELETON)
            yield name
        finally:
            subprocess.run(("dropdb", "-w", "--if-exists", name), env=ENV,
                           capture_output=False, stdin=subprocess.DEVNULL)

    def test_cutover_serves_canonical_then_rollback_restores_legacy(self, db):
        # Pre-cutover read path: two legacy rows.
        assert psql(db, "SELECT count(*) FROM vision.receipts;") == "2"

        psql(db, pc.CUTOVER_SQL)
        # Post-cutover: the same name reads the canonical surface (one twin).
        assert psql(db, "SELECT count(*) FROM vision.receipts;") == "1"
        assert psql(db, "SELECT type, plan_id FROM vision.receipts;") == (
            "IMPLEMENTATION|8261672")
        # The legacy copy is retained, not dropped.
        assert psql(db, "SELECT count(*) FROM vision.receipts_pre_cutover;") == "2"

        psql(db, pc.ROLLBACK_SQL)
        # Pre-cutover read path restored, byte-for-byte row identity.
        assert psql(db, "SELECT count(*) FROM vision.receipts;") == "2"
        assert psql(db, "SELECT string_agg(id, ',' ORDER BY id) FROM vision.receipts;") == (
            "legacy-1,legacy-2")
        # Nothing left behind: no view, no retained copy.
        assert psql(db, "SELECT count(*) FROM pg_views "
                        "WHERE schemaname='vision' AND viewname='receipts';") == "0"
        assert psql(db, "SELECT count(*) FROM pg_tables "
                        "WHERE tablename='receipts_pre_cutover';") == "0"

    def test_rollback_is_idempotent_at_the_boundary(self, db):
        psql(db, pc.CUTOVER_SQL)
        psql(db, pc.ROLLBACK_SQL)
        psql(db, pc.ROLLBACK_SQL)  # DROP VIEW IF EXISTS keeps the second run clean
        assert psql(db, "SELECT count(*) FROM vision.receipts;") == "2"
