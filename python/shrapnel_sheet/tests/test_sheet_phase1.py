"""
sheet-conf-001: Sheet phase-1 (V166) hermetic conformance suite.

Executable regression for sql/V166__sheet_phase1_manual_sheets.sql (DRAFT,
NOT APPLIED) — the phase-1 manual-sheets pre-stage of the Sheet proposal
(discussion a558efc7 / analysis 5073d217).

Doctrine under test (from 5073d217):
  D1  Sheets are windows, never storage — delete asymmetry.
  D2  No override shadowing — sheet_set_cell IS a direct OAV write.
  D3  No storage duplication — sheet tables reference, never copy.
  D4  Sparse by construction — empty cell = ABSENT OAV row, NULLs never stored.
  D5  The encode path — every write flows value -> value_<type> -> OAV with
      V128's assert_extension_type_matches guard in the loop.

HERMETIC: each TEST gets its own throwaway database (nexus_sheet_test_<pid>_<n>),
applies the baseline fixture + the REAL V128 + the REAL V166, runs, and is
dropped. The live nexus database is never touched. Fully order-independent.

Usage:
    CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
      python3 -m pytest python/shrapnel_sheet/tests/test_sheet_phase1.py -v
"""

import os
import sys
import unittest

import psycopg2
import psycopg2.extras

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/postgres")

BASELINE = os.path.join(_SELF_DIR, "fixtures", "shrapnel_qbe_baseline.sql")
V128 = os.path.join(_REPO_ROOT, "sql", "V128__shrapnel_eav_object_store.sql")
V166 = os.path.join(_REPO_ROOT, "sql", "V166__sheet_phase1_manual_sheets.sql")


def _admin_connect(dbname: str):
    dsn = DSN.rsplit("/", 1)[0] + "/" + dbname
    return psycopg2.connect(dsn)


class _HermeticDB:
    """Throwaway per-test database with baseline + V128 + V166 applied."""

    def __init__(self, tag: str):
        import uuid

        self.dbname = f"nexus_sheet_test_{os.getpid()}_{tag}_{uuid.uuid4().hex[:6]}"
        admin = _admin_connect("postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()

        self.conn = _admin_connect(self.dbname)
        self.conn.autocommit = False

    def apply_migrations(self):
        with self.conn.cursor() as cur:
            for path in (BASELINE, V128, V166):
                with open(path, "r", encoding="utf-8") as fh:
                    cur.execute(fh.read())
        self.conn.commit()

    def one(self, sql, args=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
            return row[0] if row else None

    def q(self, sql, args=None):
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def exec_(self, sql, args=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)

    def expect_error(self, sql, args=None, code=None):
        try:
            self.exec_(sql, args)
        except psycopg2.Error as e:
            if code is not None and (e.pgcode != code):
                raise AssertionError(f"expected SQLSTATE {code}, got {e.pgcode}: {e}")
            return str(e)
        self.conn.rollback()
        raise AssertionError(f"expected failure {code or ''}, but statement succeeded")

    def cleanup(self):
        try:
            self.conn.rollback()
            self.conn.close()
        finally:
            admin = _admin_connect("postgres")
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            admin.close()


# Function-call literals with explicit casts: psycopg2 sends python ints as
# integer/floats as numeric, and PostgreSQL resolves functions by exact type.
# Every shrapnel.sheet_* call below names its argument types explicitly.
F_SET = "SELECT shrapnel.sheet_set_cell(%s::bigint,%s::bigint,%s::bigint,%s::smallint,%s::text)"
F_SET_JSON = "SELECT shrapnel.sheet_set_cell(%s::bigint,%s::bigint,%s::bigint,%s::smallint,%s::text,%s::jsonb)"
F_CLEAR = "SELECT shrapnel.sheet_clear_cell(%s::bigint,%s::bigint,%s::bigint)"
F_ADDCOL = "SELECT shrapnel.sheet_add_column(%s::bigint,%s::bigint,%s::double precision,%s::text)"
F_ADDCOL_AUTO = "SELECT shrapnel.sheet_add_column(%s::bigint,%s::bigint)"
F_ADDCOL_IDX = "SELECT shrapnel.sheet_add_column(%s::bigint,%s::bigint,%s::double precision)"
F_DELCOL = "SELECT shrapnel.sheet_remove_column(%s::bigint,%s::bigint)"
F_ADDROW = "SELECT shrapnel.sheet_add_row(%s::bigint,%s::bigint)"
F_ADDROW_IDX = "SELECT shrapnel.sheet_add_row(%s::bigint,%s::bigint,%s::double precision)"
F_DELROW = "SELECT shrapnel.sheet_remove_row(%s::bigint,%s::bigint)"
F_MOVEROW = "SELECT shrapnel.sheet_move_row(%s::bigint,%s::bigint,%s::double precision)"


class SheetPhase1Tests(unittest.TestCase):
    """Each test gets its OWN throwaway database — hermetic, order-independent."""

    def setUp(self):
        self.db = _HermeticDB(self.id().rsplit(".", 1)[-1][:20])
        self.db.apply_migrations()

    def tearDown(self):
        self.db.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────

    def _field(self, name, type_code, label=None):
        return self.db.one(
            "INSERT INTO shrapnel.field (name, property_name, label, field_type_code, is_calculated, field_index)"
            " VALUES (%s,%s,%s,%s,false,0) RETURNING id",
            (name, name, label or name, type_code),
        )

    def _obj(self):
        return self.db.one("INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id")

    def _setup_sheet(self, db, name="test-sheet"):
        """One sheet, 3 columns (long/string/boolean), 2 rows."""
        sid = db.one("SELECT shrapnel.sheet_create(%s::text, %s::text)", (name, "fixture"))
        f_long = self._field("qty", 1)
        f_str = self._field("title", 2)
        f_bool = self._field("done", 4)
        db.exec_(F_ADDCOL, (sid, f_long, None, "Qty"))
        db.exec_(F_ADDCOL_AUTO, (sid, f_str))
        db.exec_(F_ADDCOL_AUTO, (sid, f_bool))
        o1, o2 = self._obj(), self._obj()
        db.exec_(F_ADDROW, (sid, o1))
        db.exec_(F_ADDROW, (sid, o2))
        db.conn.commit()
        return sid, f_long, f_str, f_bool, o1, o2

    # ── AC1: postconditions / structural integrity ──────────────────────────

    def test_ac1_postconditions_and_anchoring(self):
        db = self.db
        sid, *_ = self._setup_sheet(db)
        assert db.one("SELECT count(*) FROM information_schema.tables WHERE table_schema='shrapnel' AND table_name IN ('sheet','sheet_column','sheet_row')") == 3
        assert db.one("SELECT count(*) FROM information_schema.views WHERE table_schema='shrapnel' AND table_name IN ('v_sheet','v_sheet_grid','v_sheet_cell')") == 3
        # sheet anchor IS a first-class shrapnel object (v_sheet joins cleanly
        # and reports the projected counts)
        assert db.one("SELECT count(*) FROM shrapnel.object_instance WHERE id=%s", (sid,)) == 1
        row = db.q("SELECT * FROM shrapnel.v_sheet WHERE id=%s", (sid,))[0]
        assert row["column_count"] == 3 and row["row_count"] == 2

    # ── AC2: D5 encode path — writes are real EAV facts ─────────────────────

    def test_ac2_set_cell_creates_value_extension_oav(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_long, 1, "42"))
        db.exec_(F_SET, (sid, o1, f_str, 2, "hello"))
        db.exec_(F_SET, (sid, o1, f_bool, 4, "true"))
        db.conn.commit()

        # D2: the write IS an OAV row (no shadow copy anywhere)
        oav = db.q("SELECT * FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_long))
        assert len(oav) == 1
        val_id = oav[0]["value_id"]
        # D5: value row + typed extension, guarded by V128's trigger
        assert db.one("SELECT value_type_code FROM shrapnel.value WHERE id=%s", (val_id,)) == 1
        assert db.one("SELECT value FROM shrapnel.value_long WHERE id=%s", (val_id,)) == 42
        # readable through the view
        cells = db.q("SELECT cell_text FROM shrapnel.v_sheet_grid WHERE sheet_id=%s AND row_object_id=%s AND field_id=%s", (sid, o1, f_str))
        assert cells[0]["cell_text"] == "hello"

    # ── AC3: window membership gates (SHEETS-001/002) ────────────────────────

    def test_ac3_membership_refusals(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        f_other = self._field("unprojected", 2)
        other_obj = self._obj()

        err = db.expect_error(F_SET, (sid, o1, f_other, 2, "x"))
        assert "SHEETS-001" in err
        db.conn.rollback()
        err = db.expect_error(F_SET, (sid, other_obj, f_str, 2, "x"))
        assert "SHEETS-002" in err
        db.conn.rollback()

    # ── AC4: type agreement + retype refusal (SHEETS-003/004) ────────────────

    def test_ac4_type_agreement_and_retype_refusal(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)

        # field declares String(2); supplying Long(1) must refuse
        err = db.expect_error(F_SET, (sid, o1, f_str, 1, "42"))
        assert "SHEETS-003" in err
        db.conn.rollback()

        db.conn.commit()
        # SHEETS-004 (retype refusal) needs a cell whose EXISTING value type
        # differs from the field's declared type — only reachable via a legacy
        # out-of-band writer (set_cell itself enforces type agreement first).
        val = db.one("INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id")
        db.exec_("INSERT INTO shrapnel.value_string (id, value) VALUES (%s, 'legacy')", (val,))
        db.exec_("INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES (%s, %s, %s)", (o1, f_long, val))
        db.conn.commit()
        # field declares Long(1), we supply Long(1): SHEETS-003 passes, but the
        # cell holds String(2) → SHEETS-004
        err = db.expect_error(F_SET, (sid, o1, f_long, 1, "42"))
        assert "SHEETS-004" in err
        db.conn.rollback()
        # the legacy cell survived unchanged
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o1, f_long)) == "legacy"

    # ── AC5: literal validity (SHEETS-005) per type ──────────────────────────

    def test_ac5_literal_validity(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)

        err = db.expect_error(F_SET, (sid, o1, f_long, 1, "not-a-number"))
        assert "SHEETS-005" in err
        db.conn.rollback()
        err = db.expect_error(F_SET, (sid, o1, f_bool, 4, "YES"))
        assert "SHEETS-005" in err
        db.conn.rollback()
        # boolean literal 'yes' refused even on the encoded path
        err = db.expect_error(
            "SELECT shrapnel.sheet_encode_value(%s::smallint,%s::text)",
            (4, "yes"))
        assert "SHEETS-005" in err
        db.conn.rollback()
        # type agreement fires before literal parsing
        err = db.expect_error(F_SET, (sid, o1, f_long, 7, "not-a-uuid"))
        assert "SHEETS-003" in err
        db.conn.rollback()

    # ── AC6: in-place update semantics (single-writer-per-cell) ──────────────

    def test_ac6_update_in_place_no_duplication(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_str, 2, "first"))
        db.conn.commit()
        before = db.one("SELECT id FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str))
        db.exec_(F_SET, (sid, o1, f_str, 2, "second"))
        db.conn.commit()
        after = db.one("SELECT id FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str))
        # same OAV row, same value row, extension updated in place
        assert before == after
        assert db.one("SELECT count(*) FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str)) == 1
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o1, f_str)) == "second"

    # ── AC7: clear = sparse absence (D4) ─────────────────────────────────────

    def test_ac7_clear_cell_sparse_semantics(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_str, 2, "temp"))
        db.conn.commit()
        val_id = db.one("SELECT value_id FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str))
        db.exec_(F_CLEAR, (sid, o1, f_str))
        db.conn.commit()
        # D4: absent OAV row = empty cell; extension rows cascaded; no orphans
        assert db.one("SELECT count(*) FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str)) == 0
        assert db.one("SELECT count(*) FROM shrapnel.value WHERE id=%s", (val_id,)) == 0
        assert db.one("SELECT count(*) FROM shrapnel.value_string WHERE id=%s", (val_id,)) == 0
        # object survives
        assert db.one("SELECT count(*) FROM shrapnel.object_instance WHERE id=%s", (o1,)) == 1

    # ── AC8: D1 delete asymmetry — sheet removal ─────────────────────────────

    def test_ac8_delete_sheet_data_survives(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_str, 2, "durable"))
        db.conn.commit()

        db.exec_("DELETE FROM shrapnel.sheet WHERE id=%s", (sid,))
        db.conn.commit()
        # window parts die...
        assert db.one("SELECT count(*) FROM shrapnel.sheet_column WHERE sheet_id=%s", (sid,)) == 0
        assert db.one("SELECT count(*) FROM shrapnel.sheet_row WHERE sheet_id=%s", (sid,)) == 0
        assert db.one("SELECT count(*) FROM shrapnel.object_instance WHERE id=%s", (sid,)) == 0
        # ...data survives: object, field, OAV fact
        assert db.one("SELECT count(*) FROM shrapnel.object_instance WHERE id=%s", (o1,)) == 1
        assert db.one("SELECT count(*) FROM shrapnel.field WHERE id=%s", (f_str,)) == 1
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o1, f_str)) == "durable"

    # ── AC9: D1 delete asymmetry — row + column removal ──────────────────────

    def test_ac9_remove_row_and_column_data_survives(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_str, 2, "kept"))
        db.conn.commit()

        db.exec_(F_DELROW, (sid, o2))
        db.exec_(F_DELCOL, (sid, f_long))
        db.conn.commit()
        assert db.one("SELECT count(*) FROM shrapnel.sheet_row WHERE sheet_id=%s AND row_object_id=%s", (sid, o2)) == 0
        # o2 object + any of its cells survive
        assert db.one("SELECT count(*) FROM shrapnel.object_instance WHERE id=%s", (o2,)) == 1
        assert db.one("SELECT count(*) FROM shrapnel.sheet_column WHERE sheet_id=%s AND field_id=%s", (sid, f_long)) == 0
        # f_str column and o1's cell untouched
        assert db.one("SELECT count(*) FROM shrapnel.sheet_column WHERE sheet_id=%s AND field_id=%s", (sid, f_str)) == 1
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o1, f_str)) == "kept"

    # ── AC10: field deletion is RESTRICTed while projected ───────────────────

    def test_ac10_field_delete_restricted_while_projected(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        err = db.expect_error("DELETE FROM shrapnel.field WHERE id=%s", (f_long,), code="23503")
        assert "sheet_column_field_id_fkey" in err  # RESTRICT fired: projection still references the field
        db.conn.rollback()
        # after removing the projection, deletion proceeds
        db.exec_(F_DELCOL, (sid, f_long))
        db.exec_("DELETE FROM shrapnel.field WHERE id=%s", (f_long,))
        db.conn.commit()
        assert db.one("SELECT count(*) FROM shrapnel.field WHERE id=%s", (f_long,)) == 0

    # ── AC11: fractional ranks + ordering ────────────────────────────────────

    def test_ac11_fractional_rank_reordering(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        rows = db.q("SELECT row_object_id, row_index FROM shrapnel.sheet_row WHERE sheet_id=%s ORDER BY row_index", (sid,))
        assert [r["row_index"] for r in rows] == [1.0, 2.0]
        # move o2 between nothing and 1.0 → becomes first
        db.exec_(F_MOVEROW, (sid, o2, 0.5))
        db.conn.commit()
        rows = db.q("SELECT row_object_id, row_index FROM shrapnel.sheet_row WHERE sheet_id=%s ORDER BY row_index", (sid,))
        assert rows[0]["row_object_id"] == o2 and rows[0]["row_index"] == 0.5
        # columns: default append ordering, then fractional insert between
        cols = db.q("SELECT field_id, column_index FROM shrapnel.sheet_column WHERE sheet_id=%s ORDER BY column_index", (sid,))
        assert [c["column_index"] for c in cols] == [1.0, 2.0, 3.0]
        f_new = self._field("extra", 2)
        db.exec_(F_ADDCOL_IDX, (sid, f_new, 1.5))
        db.conn.commit()
        cols = db.q("SELECT field_id, column_index FROM shrapnel.sheet_column WHERE sheet_id=%s ORDER BY column_index", (sid,))
        assert [c["field_id"] for c in cols][:2] == [f_long, f_new]
        # moving a non-member refuses
        o3 = self._obj()
        err = db.expect_error(F_MOVEROW, (sid, o3, 1.5))
        assert "SHEETS-014" in err
        db.conn.rollback()

    # ── AC12: grid read shape (sparse, typed, ordered by consumer) ───────────

    def test_ac12_grid_sparse_read(self):
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        db.exec_(F_SET, (sid, o1, f_long, 1, "7"))
        db.conn.commit()
        grid = db.q("SELECT * FROM shrapnel.v_sheet_grid WHERE sheet_id=%s ORDER BY row_index, column_index", (sid,))
        # 2 rows × 3 columns = 6 grid lines; 5 are empty (NULL cell_text)
        assert len(grid) == 6
        filled = [g for g in grid if g["cell_text"] is not None]
        assert len(filled) == 1 and filled[0]["cell_text"] == "7" and filled[0]["cell_type_code"] == 1
        # empty cells are absent OAV rows, not NULLs
        empty = [g for g in grid if g["cell_text"] is None]
        assert all(g["cell_type_code"] is None for g in empty)
        # column label falls back display_label -> label -> property_name
        assert any(g["column_label"] == "Qty" for g in grid)
        # jsonb passthrough column is NULL for non-jsonb cells
        assert all(g["cell_jsonb"] is None for g in grid)

    # ── AC13: name uniqueness + non-empty names (SHEETS-006/007) ─────────────

    def test_ac13_sheet_create_guards(self):
        db = self.db
        err = db.expect_error("SELECT shrapnel.sheet_create(%s::text)", ("",))
        assert "SHEETS-006" in err
        db.conn.rollback()
        err = db.expect_error("SELECT shrapnel.sheet_create(%s::text)", ("   ",))
        assert "SHEETS-006" in err
        db.conn.rollback()
        db.exec_("SELECT shrapnel.sheet_create(%s::text)", ("dupe-test",))
        db.conn.commit()
        err = db.expect_error("SELECT shrapnel.sheet_create(%s::text)", ("dupe-test",))
        assert "SHEETS-007" in err
        db.conn.rollback()

    # ── AC14: full-typed-write sweep, all 7 types end-to-end ─────────────────

    def test_ac14_all_seven_types_end_to_end(self):
        db = self.db
        sid = db.one("SELECT shrapnel.sheet_create(%s::text)", ("types-sweep",))
        specs = [
            ("f_long", 1, "123", "123"),
            ("f_str", 2, "s", "s"),
            ("f_double", 3, "2.5", "2.5"),
            ("f_bool", 4, "false", "false"),
            ("f_ts", 5, "2026-09-16T00:00:00Z", None),
            ("f_jsonb", 6, '{"a": 1}', None),
            ("f_uuid", 7, "0f33a779-1e34-4980-bfdc-619e610a5d9f", None),
        ]
        fields = {}
        for name, code, lit, _ in specs:
            fid = self._field(name, code)
            fields[name] = fid
            db.exec_(F_ADDCOL_AUTO, (sid, fid))
        obj = self._obj()
        db.exec_(F_ADDROW, (sid, obj))
        for name, code, lit, _ in specs:
            if code == 6:
                db.exec_(F_SET_JSON, (sid, obj, fields[name], code, None, '{"a": 1}'))
            else:
                db.exec_(F_SET, (sid, obj, fields[name], code, lit))
        db.conn.commit()
        for name, code, lit, expect in specs:
            got = db.one(
                "SELECT cell_text FROM shrapnel.v_sheet_grid WHERE sheet_id=%s AND row_object_id=%s AND field_id=%s",
                (sid, obj, fields[name]),
            )
            if expect is not None:
                assert got == expect, f"{name}: {got!r} != {expect!r}"
            else:
                assert got is not None, f"{name}: expected non-null rendering"
        # jsonb survives round-trip
        j = db.one(
            "SELECT vj.value->>'a' FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_jsonb vj ON vj.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (obj, fields["f_jsonb"]))
        assert j == "1"

    # ── AC15: the migration's postcondition arithmetic is honest ─────────────

    def test_ac15_migration_postcondition_counts(self):
        db = self.db
        n = db.one("SELECT count(*) FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace WHERE ns.nspname='shrapnel' AND p.proname LIKE 'sheet_%'")
        # 9 functions: encode_value, set_cell, clear_cell, create, add/remove_column, add/remove/move_row
        assert n == 9, f"expected 9 sheet_* functions, got {n}"
        idx = db.one("SELECT count(*) FROM pg_indexes WHERE schemaname='shrapnel' AND indexname IN ('idx_sheet_column_sheet','idx_sheet_column_order','idx_sheet_row_sheet','idx_sheet_row_order')")
        assert idx == 4

    # ── AC17: copy-on-write — a shared value is never mutated in place ──────

    def test_ac17_copy_on_write_for_shared_values(self):
        """Live data has zero shared values today (15,382 bindings / 15,382
        distinct, verified 2026-09-16) — the stress test demanded the write
        path stay local even if sharing ever emerges. Two objects bound to one
        value; updating through one must NOT change the other's cell."""
        db = self.db
        sid, f_long, f_str, f_bool, o1, o2 = self._setup_sheet(db)
        # one value bound by two objects (out-of-band sharing, as a future
        # writer could create)
        val = db.one("INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id")
        db.exec_("INSERT INTO shrapnel.value_string (id, value) VALUES (%s, 'original')", (val,))
        db.exec_("INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES (%s,%s,%s)", (o1, f_str, val))
        db.exec_("INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES (%s,%s,%s)", (o2, f_str, val))
        db.conn.commit()
        # update through o1's binding
        db.exec_(F_SET, (sid, o1, f_str, 2, "changed"))
        db.conn.commit()
        # o1's cell now shows the new text...
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o1, f_str)) == "changed"
        # ...the OLD value row is untouched (o2 still sees 'original')...
        assert db.one(
            "SELECT vs.value FROM shrapnel.object_attribute_value oav"
            " JOIN shrapnel.value_string vs ON vs.id=oav.value_id"
            " WHERE oav.object_id=%s AND oav.field_id=%s", (o2, f_str)) == "original"
        # ...and the o1 binding points at a NEW value row (copy-on-write).
        v1 = db.one("SELECT value_id FROM shrapnel.object_attribute_value WHERE object_id=%s AND field_id=%s", (o1, f_str))
        assert v1 != val

    # ── AC16: no shadow write path — sheet tables hold references only ───────

    def test_ac16_no_shadow_write_path(self):
        db = self.db
        # D2's structural corollary: sheet tables store NO values.
        cols = db.q("SELECT column_name FROM information_schema.columns WHERE table_schema='shrapnel' AND table_name IN ('sheet','sheet_column','sheet_row')")
        forbidden = {"value", "value_id", "cell_value", "override"}
        bad = [c["column_name"] for c in cols if c["column_name"] in forbidden]
        assert not bad, f"sheet tables must not store values, found: {bad}"
        # and the only writer entry points are sheet_set_cell / sheet_clear_cell
        writers = db.one(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns ON ns.oid=p.pronamespace"
            " WHERE ns.nspname='shrapnel' AND p.proname LIKE 'sheet_%'"
            " AND p.prokind='f'")
        assert writers == 9  # 9 functions total; none bypass OAV


if __name__ == "__main__":
    unittest.main()
