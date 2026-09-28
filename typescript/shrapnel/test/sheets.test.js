import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  cellBody,
  gridOrder,
  idParam,
  mapSheetError,
  rankParam,
  shapeGrid,
  typeCodeParam,
  VALUE_TYPE_CODES,
  SCHEMA_MISSING_MESSAGE,
} from '../src/routes/sheets.js';
import { ApiError } from '../src/errors.js';

describe('sheets router helpers', () => {
  describe('idParam', () => {
    it('accepts non-negative integers as strings or numbers', () => {
      assert.equal(idParam('42', 'id'), 42);
      assert.equal(idParam(0, 'id'), 0);
      assert.equal(idParam(7, 'id'), 7);
    });
    it('rejects non-integers, negatives, and empty values with 400', () => {
      for (const bad of ['abc', '1.5', '', null, undefined, -1, 'NaN']) {
        assert.throws(() => idParam(bad, 'id'), (err) => {
          assert.ok(err instanceof ApiError, `${bad} should be an ApiError`);
          assert.equal(err.status, 400);
          return true;
        });
      }
    });
  });

  describe('rankParam', () => {
    it('returns null for absent/null so the SQL can default to append', () => {
      assert.equal(rankParam(undefined, 'row_index'), null);
      assert.equal(rankParam(null, 'row_index'), null);
    });
    it('accepts fractional ranks — the reorder primitive is a real, not an int', () => {
      assert.equal(rankParam(0.5, 'row_index'), 0.5);
      assert.equal(rankParam(1.5, 'column_index'), 1.5);
      assert.equal(rankParam(0, 'row_index'), 0);
    });
    it('rejects non-finite and non-numeric input', () => {
      for (const bad of ['1.5', NaN, Infinity, -Infinity, {}, true]) {
        assert.throws(() => rankParam(bad, 'row_index'), (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          return true;
        });
      }
    });
  });

  describe('typeCodeParam', () => {
    it('accepts the 1..7 registry', () => {
      for (const code of [1, 2, 3, 4, 5, 6, 7]) {
        assert.equal(typeCodeParam(code), code);
      }
      assert.equal(typeCodeParam('6'), 6);
    });
    it('rejects codes outside the registry as a 400, not a SHEETS-005', () => {
      // Letting 8 through would surface as a confusing SHEETS-005 from the DB
      // ("unknown value type code") when it is plainly a bad request.
      for (const bad of [0, 8, -1, 1.5, 'abc', '', null, undefined]) {
        assert.throws(() => typeCodeParam(bad), (err) => {
          assert.ok(err instanceof ApiError);
          assert.equal(err.status, 400);
          return true;
        });
      }
    });
  });

  describe('cellBody', () => {
    it('takes a text literal for a non-jsonb cell', () => {
      assert.deepEqual(cellBody({ type_code: 1, value: '42' }), {
        typeCode: 1,
        value: '42',
        json: null,
      });
    });
    it('accepts an object payload for a jsonb cell', () => {
      assert.deepEqual(cellBody({ type_code: 6, json: { a: 1 } }), {
        typeCode: 6,
        value: null,
        json: { a: 1 },
      });
    });
    it('lets the database validate a jsonb string literal (same SHEETS-005 path)', () => {
      assert.deepEqual(cellBody({ type_code: 6, json: '{"a": 1}' }), {
        typeCode: 6,
        value: '{"a": 1}',
        json: null,
      });
    });
    it('requires json for a jsonb cell rather than stringifying an absent value', () => {
      assert.throws(() => cellBody({ type_code: 6 }), (err) => {
        assert.equal(err.status, 400);
        assert.match(err.message, /json is required/);
        return true;
      });
    });
    it('requires a value for a non-jsonb cell', () => {
      assert.throws(() => cellBody({ type_code: 2 }), (err) => {
        assert.equal(err.status, 400);
        assert.match(err.message, /value is required/);
        return true;
      });
    });
    it('refuses to coerce a non-string value', () => {
      // Stringifying in JS would hide a type error behind a plausible literal.
      assert.throws(() => cellBody({ type_code: 1, value: 42 }), (err) => {
        assert.equal(err.status, 400);
        assert.match(err.message, /must be a string literal/);
        return true;
      });
    });
    it('propagates the type_code validation first', () => {
      assert.throws(() => cellBody({ type_code: 99, value: 'x' }), (err) => {
        assert.equal(err.status, 400);
        assert.match(err.message, /type_code/);
        return true;
      });
    });
    it('exposes the registry constants', () => {
      assert.equal(VALUE_TYPE_CODES.LONG, 1);
      assert.equal(VALUE_TYPE_CODES.JSONB, 6);
      assert.equal(VALUE_TYPE_CODES.UUID, 7);
    });
  });

  describe('gridOrder', () => {
    // v_sheet_grid is unordered by design ("order at the consumer"), and a
    // spreadsheet rendering rows out of order is worse than useless.
    const rows = [
      { row_index: 2, column_index: 1, id: 'r2c1' },
      { row_index: 1, column_index: 2, id: 'r1c2' },
      { row_index: 1, column_index: 1, id: 'r1c1' },
      { row_index: 2, column_index: 2, id: 'r2c2' },
    ];
    it('orders by row_index then column_index', () => {
      assert.deepEqual(gridOrder(rows).map((r) => r.id), ['r1c1', 'r1c2', 'r2c1', 'r2c2']);
    });
    it('handles fractional ranks', () => {
      const frac = [
        { row_index: 1, column_index: 0, id: 'b' },
        { row_index: 0.5, column_index: 0, id: 'a' },
      ];
      assert.deepEqual(gridOrder(frac).map((r) => r.id), ['a', 'b']);
    });
    it('does not mutate its input', () => {
      const input = [...rows];
      gridOrder(input);
      assert.deepEqual(input.map((r) => r.id), rows.map((r) => r.id));
    });
    it('returns an empty array unchanged', () => {
      assert.deepEqual(gridOrder([]), []);
    });
  });

  describe('shapeGrid', () => {
    // 2 rows x 3 columns = 6 grid lines, of which 2 are filled and 4 are empty
    // — the sparse case the doctrine cares about (D4: empty = absent OAV row,
    // not a stored NULL).
    const grid = [
      { sheet_id: 1, row_object_id: 10, row_index: 1, field_id: 100, column_index: 1,
        column_label: 'Qty', field_name: 'qty', field_type_code: 1,
        cell_type_code: null, cell_text: null, cell_jsonb: null, cell_created_at: null },
      { sheet_id: 1, row_object_id: 10, row_index: 1, field_id: 101, column_index: 2,
        column_label: 'Title', field_name: 'title', field_type_code: 2,
        cell_type_code: 2, cell_text: 'hello', cell_jsonb: null, cell_created_at: '2026-01-01T00:00:00.000Z' },
      { sheet_id: 1, row_object_id: 10, row_index: 1, field_id: 102, column_index: 3,
        column_label: 'Done', field_name: 'done', field_type_code: 4,
        cell_type_code: null, cell_text: null, cell_jsonb: null, cell_created_at: null },
      { sheet_id: 1, row_object_id: 11, row_index: 2, field_id: 100, column_index: 1,
        column_label: 'Qty', field_name: 'qty', field_type_code: 1,
        cell_type_code: 1, cell_text: '7', cell_jsonb: null, cell_created_at: '2026-01-01T00:00:01.000Z' },
      { sheet_id: 1, row_object_id: 11, row_index: 2, field_id: 101, column_index: 2,
        column_label: 'Title', field_name: 'title', field_type_code: 2,
        cell_type_code: null, cell_text: null, cell_jsonb: null, cell_created_at: null },
      { sheet_id: 1, row_object_id: 11, row_index: 2, field_id: 102, column_index: 3,
        column_label: 'Done', field_name: 'done', field_type_code: 4,
        cell_type_code: null, cell_text: null, cell_jsonb: null, cell_created_at: null },
    ];

    it('folds the flat grid into columns, row order, and cells', () => {
      const out = shapeGrid(grid);
      assert.equal(out.columns.length, 3);
      assert.deepEqual(out.row_object_ids, [10, 11]);
      assert.equal(out.cells.length, 6);
    });

    it('orders columns by column_index and rows by row_index', () => {
      const out = shapeGrid(grid);
      assert.deepEqual(out.columns.map((c) => c.field_id), [100, 101, 102]);
      assert.deepEqual(out.row_object_ids, [10, 11]);
    });

    it('carries the column label fallback chain through', () => {
      // display_label -> label -> property_name is resolved by the view; the
      // router must pass the resolved value through unchanged.
      assert.deepEqual(shapeGrid(grid).columns.map((c) => c.label), ['Qty', 'Title', 'Done']);
    });

    it('leaves an empty cell null rather than inventing a zero or empty string', () => {
      const out = shapeGrid(grid);
      const empty = out.cells.filter((c) => c.cell_text === null);
      assert.equal(empty.length, 4);
      for (const c of empty) {
        assert.equal(c.cell_type_code, null);
        assert.equal(c.cell_created_at, null);
      }
    });

    it('preserves the one populated cell', () => {
      const out = shapeGrid(grid);
      const filled = out.cells.filter((c) => c.cell_text !== null);
      assert.equal(filled.length, 2);
      assert.ok(filled.some((c) => c.cell_text === 'hello'));
      assert.ok(filled.some((c) => c.cell_text === '7'));
    });

    it('returns an empty shape for an empty sheet', () => {
      assert.deepEqual(shapeGrid([]), { columns: [], row_object_ids: [], cells: [] });
    });
  });

  describe('mapSheetError', () => {
    it('maps SHEETS-nnn rule violations (P0001) to 409 and surfaces the code', () => {
      const err = Object.assign(
        new Error('SHEETS-001: field 5 is not projected by sheet 9 — add the column before writing cells'),
        { code: 'P0001' }
      );
      const mapped = mapSheetError(err);
      assert.ok(mapped instanceof ApiError);
      assert.equal(mapped.status, 409);
      assert.equal(mapped.details.sheets_code, 'SHEETS-001');
    });

    it('maps every documented SHEETS code without mangling the number', () => {
      for (let n = 1; n <= 14; n++) {
        const code = `SHEETS-${String(n).padStart(3, '0')}`;
        const mapped = mapSheetError(Object.assign(new Error(`${code}: rule`), { code: 'P0001' }));
        assert.equal(mapped.details.sheets_code, code);
      }
    });

    it('maps a bare P0001 with no SHEETS prefix to a 409 without a code', () => {
      const mapped = mapSheetError(Object.assign(new Error('append-only; UPDATE rejected'), { code: 'P0001' }));
      assert.equal(mapped.status, 409);
      assert.equal(mapped.details, undefined);
    });

    it('maps a missing function/table to 503, not 500', () => {
      // V166 is an unapplied draft, so undefined_function is the EXPECTED
      // state on a host that has not run it — a client must be able to tell
      // "not deployed yet" from "broken".
      for (const pgCode of ['42883', '42P01']) {
        const mapped = mapSheetError(Object.assign(new Error('function does not exist'), { code: pgCode }));
        assert.ok(mapped instanceof ApiError);
        assert.equal(mapped.status, 503);
        assert.equal(mapped.details.sheets_code, 'SHEETS-SCHEMA');
        assert.equal(mapped.message, SCHEMA_MISSING_MESSAGE);
      }
    });

    it('returns null for unrelated errors so the router falls through', () => {
      assert.equal(mapSheetError(new Error('boom')), null);
      assert.equal(mapSheetError(null), null);
      // 23505/23503 are already handled by the service-wide errorHandler.
      assert.equal(mapSheetError(Object.assign(new Error('dup'), { code: '23505' })), null);
      assert.equal(mapSheetError(Object.assign(new Error('fk'), { code: '23503' })), null);
    });
  });
});
