// src/routes/sheets.js — Sheet phase 1 (V166) REST surface.
//
// Sheets are *windows over the existing EAV store*, never storage of their own
// (DBA analysis 5073d217, discussion a558efc7):
//   D1  delete asymmetry — drop a sheet/row/column and the window dies;
//       objects, fields, and OAV facts survive. No cascade into user data.
//   D2  no override shadowing — set_cell IS a direct OAV write. There is no
//       sheet-scoped value fork anywhere.
//   D3  no storage duplication — the sheet tables only reference identities.
//   D4  sparse by construction — an empty cell is an ABSENT OAV row, not a NULL.
//   D5  the encode path — every write flows value -> value_<type> -> OAV, with
//       V128's assert_extension_type_matches guard firing inside the loop.
//
// Every mutating route delegates to a shrapnel.sheet_* function rather than
// writing the tables directly. That is the whole point of the DDL: the guards
// (membership, type agreement, literal validity) live in those functions, and a
// hand-written INSERT would walk straight past them.
//
// ── V166 STATUS ────────────────────────────────────────────────────────────
// sql/V166__sheet_phase1_manual_sheets.sql is a DBA DRAFT marked "DO NOT
// APPLY UNTIL THE ROUNDTABLE SETTLES THE PROPOSAL", and it is deliberately NOT
// in this service's migrations/ chain. Until it is applied, every route here
// answers 503 sheet_schema_unavailable rather than an opaque 500, so a client
// can tell "not deployed yet" from "broken". See sheetsSchemaAvailable().

import { Router } from 'express';
import { pool } from '../db.js';
import { badRequest, notFound, conflict, unavailable } from '../errors.js';
import { writeLimiter } from '../lib/rate-limit.js';

export const sheetsRouter = Router();

// ── helpers (exported for unit tests) ───────────────────────────────────

/** SHEETS-001..014 all surface as SQLSTATE P0001 (RAISE EXCEPTION). */
const SHEETS_CODE = /SHEETS-(\d{3})/;

export const SCHEMA_MISSING_MESSAGE =
  'sheet schema is not available on this database: sql/V166__sheet_phase1_manual_sheets.sql has not been applied';

/**
 * Translate a PG error raised by a shrapnel.sheet_* function into HTTP
 * semantics. Mirrors mapPgError in routes/stereotypes.js so the service has
 * one error vocabulary, and additionally surfaces the SHEETS-nnn code so a
 * client can branch on the specific rule it hit without parsing prose.
 */
export function mapSheetError(err) {
  if (err && err.code === 'P0001') {
    const m = SHEETS_CODE.exec(err.message ?? '');
    const code = m ? `SHEETS-${m[1]}` : null;
    return conflict(err.message, code ? { sheets_code: code } : undefined);
  }
  // undefined_table/function → V166 has not been applied. This is the expected
  // state on any host where the draft migration is still pending.
  if (err && (err.code === '42883' || err.code === '42P01')) {
    return unavailable(SCHEMA_MISSING_MESSAGE, { sheets_code: 'SHEETS-SCHEMA' });
  }
  return null;
}

/** Parse a required positive integer path param. */
export function idParam(value, label) {
  if (value === null || value === undefined || value === '') {
    throw badRequest(`${label} must be an integer`);
  }
  const n = Number(value);
  if (!Number.isInteger(n) || n < 0) throw badRequest(`${label} must be a non-negative integer`);
  return n;
}

/**
 * Fractional ranks are the reorder primitive (sheet_move_row takes a real, not
 * an integer), so a rank is validated as a finite number rather than an int.
 * `null` is meaningful: the SQL defaults append/max+1.
 */
export function rankParam(value, label) {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw badRequest(`${label} must be a finite number`);
  }
  return value;
}

export const VALUE_TYPE_CODES = Object.freeze({
  LONG: 1,
  STRING: 2,
  DOUBLE: 3,
  BOOLEAN: 4,
  TIMESTAMP: 5,
  JSONB: 6,
  UUID: 7,
});

/**
 * The registry is 1..7 (V128's value_type table). A code outside it cannot be
 * encoded, so refuse it here rather than letting the database raise
 * SHEETS-005 for something that is plainly a bad request.
 */
export function typeCodeParam(value) {
  if (value === null || value === undefined || value === '') {
    throw badRequest('type_code is required');
  }
  const n = Number(value);
  if (!Number.isInteger(n) || n < VALUE_TYPE_CODES.LONG || n > VALUE_TYPE_CODES.UUID) {
    throw badRequest('type_code must be an integer in 1..7');
  }
  return n;
}

/**
 * Cells are addressed as (row object, projected field). A jsonb cell carries
 * its payload in `json` rather than as a text literal, so the two are kept
 * distinct here instead of being stringified into one.
 */
export function cellBody(body) {
  const b = body ?? {};
  const typeCode = typeCodeParam(b.type_code);
  const isJsonb = typeCode === VALUE_TYPE_CODES.JSONB;
  let value = null;
  let json = null;

  if (isJsonb) {
    if (b.json === undefined || b.json === null) {
      throw badRequest('json is required for a jsonb cell (type_code 6)');
    }
    if (typeof b.json === 'string') {
      // Let the database validate it as JSON so the error is SHEETS-005 and
      // travels the same path as every other bad literal.
      value = b.json;
    } else {
      json = b.json;
    }
  } else {
    if (b.value === undefined || b.value === null) {
      throw badRequest('value is required');
    }
    if (typeof b.value !== 'string') {
      // The SQL takes text and casts per type; coercing here would hide
      // type errors behind JS stringification.
      throw badRequest('value must be a string literal');
    }
    value = b.value;
  }
  return { typeCode, value, json };
}

/** v_sheet_grid is deliberately unordered; the view says "order at the consumer". */
export function gridOrder(rows) {
  return [...rows].sort(
    (a, b) => a.row_index - b.row_index || a.column_index - b.column_index
  );
}

/** Fold the sparse grid into { columns, rows, cells } for a spreadsheet client. */
export function shapeGrid(rows) {
  const ordered = gridOrder(rows);
  const columns = [];
  const seenColumn = new Map();
  const rowIds = [];
  const seenRow = new Map();
  const cells = [];

  for (const r of ordered) {
    if (!seenColumn.has(r.field_id)) {
      seenColumn.set(r.field_id, columns.length);
      columns.push({
        field_id: r.field_id,
        column_index: r.column_index,
        label: r.column_label,
        field_name: r.field_name,
        field_type_code: r.field_type_code,
      });
    }
    if (!seenRow.has(r.row_object_id)) {
      seenRow.set(r.row_object_id, rowIds.length);
      rowIds.push(r.row_object_id);
    }
    cells.push({
      row_object_id: r.row_object_id,
      field_id: r.field_id,
      // D4: a cell is empty when the OAV row is absent, which the view renders
      // as all-null. Do not invent a zero or an empty string for it.
      cell_text: r.cell_text,
      cell_type_code: r.cell_type_code,
      cell_jsonb: r.cell_jsonb ?? null,
      cell_created_at: r.cell_created_at ?? null,
    });
  }
  return { columns, row_object_ids: rowIds, cells };
}

// ── schema availability ─────────────────────────────────────────────────

let schemaCache = null;

/** True when the V166 sheet functions and views are present in the database. */
export async function sheetsSchemaAvailable(client = pool) {
  const r = await client.query(
    `SELECT
       (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'shrapnel' AND p.proname LIKE 'sheet\_%') AS functions,
       (SELECT count(*) FROM pg_views
         WHERE schemaname = 'shrapnel'
           AND viewname IN ('v_sheet', 'v_sheet_grid', 'v_sheet_cell')) AS views`
  );
  const row = r.rows[0] ?? {};
  // 9 API functions (the anchor-cleanup trigger is named trg_sheet_*, so it is
  // outside the sheet_% LIKE) and 3 views. See V166 §7 postconditions.
  return Number(row.functions) === 9 && Number(row.views) === 3;
}

/** Guard every route: 503 while V166 is pending, never an opaque 500. */
async function requireSheetSchema(req, _res, next) {
  try {
    if (schemaCache === null) {
      schemaCache = await sheetsSchemaAvailable();
    }
    if (!schemaCache) {
      return next(unavailable(SCHEMA_MISSING_MESSAGE, { sheets_code: 'SHEETS-SCHEMA' }));
    }
    next();
  } catch (err) {
    next(err);
  }
}

/** Test/shutdown hook: forget the cached availability answer. */
export function resetSchemaCache() {
  schemaCache = null;
}

sheetsRouter.use(requireSheetSchema);

// ── reads ───────────────────────────────────────────────────────────────

// GET /api/sheets?limit=&offset=
sheetsRouter.get('/', async (req, res, next) => {
  try {
    const limit = Math.min(500, Math.max(1, parseInt(req.query.limit ?? '100', 10) || 100));
    const offset = Math.max(0, parseInt(req.query.offset ?? '0', 10) || 0);
    const r = await pool.query(
      `SELECT id, name, description, created_at, updated_at, object_created_at,
              column_count::int, row_count::int
         FROM shrapnel.v_sheet
        ORDER BY id
        LIMIT $1 OFFSET $2`,
      [limit, offset]
    );
    res.json({ sheets: r.rows, limit, offset });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// GET /api/sheets/:id
sheetsRouter.get('/:id(\\d+)', async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(
      `SELECT id, name, description, created_at, updated_at, object_created_at,
              column_count::int, row_count::int
         FROM shrapnel.v_sheet WHERE id = $1`,
      [id]
    );
    if (r.rowCount === 0) throw notFound(`sheet ${id} not found`);
    res.json({ sheet: r.rows[0] });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// GET /api/sheets/:id/columns — the projection, in display order
sheetsRouter.get('/:id(\\d+)/columns', async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(
      `SELECT c.field_id, c.column_index, c.display_label,
              f.name AS field_name, f.property_name, f.label,
              f.field_type_code
         FROM shrapnel.sheet_column c
         JOIN shrapnel.field f ON f.id = c.field_id
        WHERE c.sheet_id = $1
        ORDER BY c.column_index`,
      [id]
    );
    res.json({ sheet_id: id, columns: r.rows });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// GET /api/sheets/:id/rows — membership, in rank order
sheetsRouter.get('/:id(\\d+)/rows', async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(
      `SELECT r.row_object_id, r.row_index, o.created_at AS object_created_at
         FROM shrapnel.sheet_row r
         LEFT JOIN shrapnel.object_instance o ON o.id = r.row_object_id
        WHERE r.sheet_id = $1
        ORDER BY r.row_index`,
      [id]
    );
    res.json({ sheet_id: id, rows: r.rows });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// GET /api/sheets/:id/grid — the whole window, sparse and ordered
sheetsRouter.get('/:id(\\d+)/grid', async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(
      `SELECT * FROM shrapnel.v_sheet_grid WHERE sheet_id = $1`,
      [id]
    );
    // The view is unordered by design; a spreadsheet that renders rows out of
    // order is worse than useless, so ordering happens here, at the consumer.
    res.json({ sheet_id: id, ...shapeGrid(r.rows) });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// GET /api/sheets/:id/cells — readable cells scoped to the sheet's window
sheetsRouter.get('/:id(\\d+)/cells', async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(
      `SELECT c.object_id, c.field_id, c.value_id, c.value_type_code,
              c.cell_created_at, c.cell_text
         FROM shrapnel.v_sheet_cell c
         JOIN shrapnel.sheet_column sc ON sc.sheet_id = $1 AND sc.field_id = c.field_id
         JOIN shrapnel.sheet_row sr     ON sr.sheet_id = $1 AND sr.row_object_id = c.object_id
        ORDER BY c.object_id, c.field_id`,
      [id]
    );
    res.json({ sheet_id: id, cells: r.rows });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// ── shape operations ────────────────────────────────────────────────────

// POST /api/sheets  { name, description? }
sheetsRouter.post('/', writeLimiter, async (req, res, next) => {
  try {
    const body = req.body ?? {};
    // Distinguish a MALFORMED body (no name, or a non-string) from a
    // well-formed request carrying a blank name. The former is a 400. The
    // latter is the domain rule SHEETS-006, which the DDL enforces and
    // mapSheetError turns into a 409 — pre-empting it here would make the
    // documented error unreachable over HTTP, and would split the SHEETS-0xx
    // family across two status codes (007 duplicate → 409, 006 blank → 400)
    // for what is one rule: "a sheet needs a real name".
    if (body.name === undefined || body.name === null || typeof body.name !== 'string') {
      throw badRequest('name is required and must be a string');
    }
    const name = body.name.trim();
    const description =
      body.description === undefined || body.description === null
        ? null
        : String(body.description);

    const r = await pool.query(`SELECT shrapnel.sheet_create($1, $2) AS id`, [name, description]);
    const d = await pool.query(
      `SELECT id, name, description, created_at, updated_at,
              column_count::int, row_count::int
         FROM shrapnel.v_sheet WHERE id = $1`,
      [r.rows[0].id]
    );
    res.status(201).json({ sheet: d.rows[0] });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// DELETE /api/sheets/:id — D1 delete asymmetry.
// The window dies (columns + rows cascade); the objects, fields, and OAV facts
// do NOT. trg_sheet_anchor_cleanup reaps the anchor object_instance.
sheetsRouter.delete('/:id(\\d+)', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const r = await pool.query(`DELETE FROM shrapnel.sheet WHERE id = $1 RETURNING id`, [id]);
    if (r.rowCount === 0) throw notFound(`sheet ${id} not found`);
    res.json({ deleted: true, sheet_id: id });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// POST /api/sheets/:id/columns  { field_id, column_index?, display_label? }
sheetsRouter.post('/:id(\\d+)/columns', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const body = req.body ?? {};
    const fieldId = idParam(body.field_id, 'field_id');
    const columnIndex = rankParam(body.column_index, 'column_index');
    const displayLabel =
      body.display_label === undefined || body.display_label === null
        ? null
        : String(body.display_label);

    const r = await pool.query(
      `SELECT shrapnel.sheet_add_column($1, $2, $3, $4) AS id`,
      [id, fieldId, columnIndex, displayLabel]
    );
    const d = await pool.query(
      `SELECT c.field_id, c.column_index, c.display_label, f.name AS field_name,
              f.property_name, f.label, f.field_type_code
         FROM shrapnel.sheet_column c
         JOIN shrapnel.field f ON f.id = c.field_id
        WHERE c.sheet_id = $1 AND c.field_id = $2`,
      [id, fieldId]
    );
    res.status(201).json({ column: d.rows[0], column_id: r.rows[0].id });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// DELETE /api/sheets/:id/columns/:fieldId — projection only; cells survive.
sheetsRouter.delete('/:id(\\d+)/columns/:fieldId', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const fieldId = idParam(req.params.fieldId, 'fieldId');
    const r = await pool.query(
      `SELECT 1 FROM shrapnel.sheet_column WHERE sheet_id = $1 AND field_id = $2`,
      [id, fieldId]
    );
    if (r.rowCount === 0) throw notFound(`sheet ${id} does not project field ${fieldId}`);
    await pool.query(`SELECT shrapnel.sheet_remove_column($1, $2)`, [id, fieldId]);
    res.json({ removed: true, sheet_id: id, field_id: fieldId });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// POST /api/sheets/:id/rows  { object_id, row_index? }
sheetsRouter.post('/:id(\\d+)/rows', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const body = req.body ?? {};
    const objectId = idParam(body.object_id, 'object_id');
    const rowIndex = rankParam(body.row_index, 'row_index');

    const r = await pool.query(`SELECT shrapnel.sheet_add_row($1, $2, $3) AS id`, [
      id,
      objectId,
      rowIndex,
    ]);
    const d = await pool.query(
      `SELECT row_object_id, row_index FROM shrapnel.sheet_row
        WHERE sheet_id = $1 AND row_object_id = $2`,
      [id, objectId]
    );
    res.status(201).json({ row: d.rows[0], row_id: r.rows[0].id });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// DELETE /api/sheets/:id/rows/:objectId — junction only; object + cells survive.
sheetsRouter.delete('/:id(\\d+)/rows/:objectId', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const objectId = idParam(req.params.objectId, 'objectId');
    const r = await pool.query(
      `SELECT 1 FROM shrapnel.sheet_row WHERE sheet_id = $1 AND row_object_id = $2`,
      [id, objectId]
    );
    if (r.rowCount === 0) throw notFound(`object ${objectId} is not a row of sheet ${id}`);
    await pool.query(`SELECT shrapnel.sheet_remove_row($1, $2)`, [id, objectId]);
    res.json({ removed: true, sheet_id: id, object_id: objectId });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// POST /api/sheets/:id/rows/:objectId/move  { row_index }
// Fractional rank, O(1) reorder. No index → the SQL refuses with SHEETS-013
// ("use sheet_add_row for append"), which is a deliberate guard against
// silently turning a move into an append.
sheetsRouter.post('/:id(\\d+)/rows/:objectId/move', writeLimiter, async (req, res, next) => {
  try {
    const id = idParam(req.params.id, 'id');
    const objectId = idParam(req.params.objectId, 'objectId');
    const body = req.body ?? {};
    if (body.row_index === undefined || body.row_index === null) {
      // Would be a 409 (SHEETS-013) from the database. Say so as a 400 instead:
      // omitting the field is a malformed request, not a rule violation.
      throw badRequest('row_index is required (a fractional rank; use POST /rows to append)');
    }
    const rowIndex = rankParam(body.row_index, 'row_index');
    await pool.query(`SELECT shrapnel.sheet_move_row($1, $2, $3)`, [id, objectId, rowIndex]);
    const d = await pool.query(
      `SELECT row_object_id, row_index FROM shrapnel.sheet_row
        WHERE sheet_id = $1 AND row_object_id = $2`,
      [id, objectId]
    );
    res.json({ row: d.rows[0] });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});

// ── cells ───────────────────────────────────────────────────────────────

// PUT /api/sheets/:id/rows/:objectId/fields/:fieldId
//   { type_code, value }  or  { type_code: 6, json }
// D2: this is a direct OAV write, not a sheet-scoped override. D5: the value
// flows through sheet_encode_value, so the V128 type guard fires.
sheetsRouter.put(
  '/:id(\\d+)/rows/:objectId/fields/:fieldId',
  writeLimiter,
  async (req, res, next) => {
    try {
      const id = idParam(req.params.id, 'id');
      const objectId = idParam(req.params.objectId, 'objectId');
      const fieldId = idParam(req.params.fieldId, 'fieldId');
      const { typeCode, value, json } = cellBody(req.body);

      const r = await pool.query(`SELECT shrapnel.sheet_set_cell($1,$2,$3,$4,$5,$6) AS value_id`, [
        id,
        objectId,
        fieldId,
        typeCode,
        value,
        json,
      ]);
      res.json({ sheet_id: id, object_id: objectId, field_id: fieldId, value_id: r.rows[0].value_id });
    } catch (err) {
      next(mapSheetError(err) ?? err);
    }
  }
);

// DELETE /api/sheets/:id/rows/:objectId/fields/:fieldId
// D4: empties the cell by removing the OAV binding. Sparse, idempotent, and it
// reference-counts the value row rather than cascading blindly — a value shared
// by another object is left alone.
sheetsRouter.delete(
  '/:id(\\d+)/rows/:objectId/fields/:fieldId',
  writeLimiter,
  async (req, res, next) => {
    try {
      const id = idParam(req.params.id, 'id');
      const objectId = idParam(req.params.objectId, 'objectId');
      const fieldId = idParam(req.params.fieldId, 'fieldId');
      await pool.query(`SELECT shrapnel.sheet_clear_cell($1, $2, $3)`, [id, objectId, fieldId]);
      res.json({ cleared: true, sheet_id: id, object_id: objectId, field_id: fieldId });
    } catch (err) {
      next(mapSheetError(err) ?? err);
    }
  }
);

// POST /api/sheets/encode  { type_code, value } or { type_code: 6, json }
// Standalone literal preflight: validates and encodes without binding a cell.
// Useful for a client that wants to know whether "not-a-number" is acceptable
// for a Long column before it commits to a write.
sheetsRouter.post('/encode', writeLimiter, async (req, res, next) => {
  try {
    const { typeCode, value, json } = cellBody(req.body);
    const r = await pool.query(`SELECT shrapnel.sheet_encode_value($1, $2, $3) AS value_id`, [
      typeCode,
      value,
      json,
    ]);
    res.status(201).json({ value_id: r.rows[0].value_id, type_code: typeCode });
  } catch (err) {
    next(mapSheetError(err) ?? err);
  }
});
