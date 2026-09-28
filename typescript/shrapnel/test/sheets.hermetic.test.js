// Hermetic end-to-end tests for the Sheet phase-1 REST surface.
//
// These are the tests that actually prove the endpoints work: they create a
// throwaway database, apply this service's OWN migration chain plus the real
// sql/V166__sheet_phase1_manual_sheets.sql, drive the HTTP layer, and drop the
// database. The live `nexus` database is never touched. Fully order-independent.
//
// This is the same hermetic pattern python/shrapnel_sheet/tests/test_sheet_phase1.py
// uses. It is the only way to exercise this surface while V166 is still an
// unapplied DBA draft — the service's own migrations/ chain deliberately does
// NOT contain V166, because applying it is the roundtable's call, not mine.
//
// Run: SHRAPNEL_TEST_ADMIN_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
//        node --test test/sheets.hermetic.test.js
//
// Skips itself (rather than failing) when the admin DSN cannot create a
// database, so `npm test` stays useful on a host without that privilege.

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import pg from 'pg';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVICE_ROOT = join(__dirname, '..');
const REPO_ROOT = join(SERVICE_ROOT, '..', '..');
const MIGRATIONS_DIR = join(SERVICE_ROOT, 'migrations');
const V166 = join(REPO_ROOT, 'sql', 'V166__sheet_phase1_manual_sheets.sql');

const ADMIN_DSN =
  process.env.SHRAPNEL_TEST_ADMIN_DSN ||
  process.env.SHRAPNEL_PG_DSN ||
  'postgresql://pguser:pgpass@localhost:5432/postgres';

const dsnFor = (dbname) => ADMIN_DSN.replace(/\/[^/]*$/, `/${dbname}`);

/**
 * Apply the service's own migration chain (and optionally the V166 draft) with
 * psql, not the pg driver. Two reasons, both load-bearing:
 *
 *   1. 0003_candidate_state_model.sql issues TOP-LEVEL SAVEPOINT /
 *      ROLLBACK TO SAVEPOINT, which is illegal outside a transaction block.
 *   2. 0007_work_request_stereotypes.sql uses `\gset` three times — a psql
 *      meta-command, not SQL. The pg driver cannot parse it at all
 *      ("syntax error at or near \"\\\""); \gset is how that file threads one
 *      revision id into the next CREATE.
 *
 * `psql -1` supplies the outer transaction; the explicit BEGIN/COMMIT inside
 * 0004/0005/0007/V166 degrade to a harmless no-op warning.
 */
function buildSchema(dbname, { withV166 = false } = {}) {
  const script = join(tmpdir(), `sheet-build-${dbname}.sql`);
  writeFileSync(
    script,
    [
      'CREATE SCHEMA IF NOT EXISTS shrapnel AUTHORIZATION pguser;',
      ...readdirSync(MIGRATIONS_DIR)
        .filter((f) => f.endsWith('.sql'))
        .sort()
        .map((f) => readFileSync(join(MIGRATIONS_DIR, f), 'utf8')),
      ...(withV166 ? [readFileSync(V166, 'utf8')] : []),
    ].join('\n')
  );
  try {
    const psql = spawnSync(
      'psql',
      [dsnFor(dbname), '-q', '-v', 'ON_ERROR_STOP=1', '-1', '-f', script],
      { encoding: 'utf8' }
    );
    if (psql.status !== 0) {
      throw new Error(
        `schema build failed (psql exit ${psql.status}):\n${psql.stderr || psql.stdout}`
      );
    }
  } finally {
    rmSync(script, { force: true });
  }
}

let admin;
let dbName;
let pool;
let server;
let baseUrl;
let skipReason = null;
const api = async (method, path, body) => {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: {
      // No keep-alive: undici otherwise holds a client socket open past
      // server.closeAllConnections(), and that lingering TCPSocketWrap is the
      // only thing keeping the event loop (and so the run) alive forever.
      connection: 'close',
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { status: res.status, body: parsed };
};

before(async () => {
  dbName = `nexus_sheet_test_${process.pid}_${randomUUID().slice(0, 6)}`;
  try {
    admin = new pg.Client({ connectionString: dsnFor('postgres') });
    await admin.connect();
    await admin.query(`DROP DATABASE IF EXISTS "${dbName}"`);
    await admin.query(`CREATE DATABASE "${dbName}"`);
  } catch (err) {
    skipReason = `cannot create a throwaway database (${err.message})`;
    if (admin) await admin.end().catch(() => {});
    return;
  }

  try {
    buildSchema(dbName, { withV166: true });

    // Point the service at the throwaway database BEFORE importing it:
    // src/db.js builds its pool at module load from the environment.
    process.env.SHRAPNEL_PG_DSN = dsnFor(dbName);
    // Likewise for the rate limiters: src/lib/rate-limit.js reads its window
    // and limit at module load, and the production defaults (60 writes/min,
    // 300 reads/min) are well below what a 40-test suite issuing a few
    // hundred fixture writes needs. Without this the suite 429s partway
    // through and reports it as a dozen unrelated assertion failures.
    process.env.SHRAPNEL_RATE_LIMIT = '100000';
    process.env.SHRAPNEL_WRITE_RATE_LIMIT = '100000';
    const [{ routes }, { pool: servicePool }, expressMod] = await Promise.all([
      import('../src/routes/index.js'),
      import('../src/db.js'),
      import('express'),
    ]);
    // express is CJS: the dynamic import gives a namespace whose callable
    // lives on .default.
    const express = expressMod.default ?? expressMod;
    const { errorHandler, notFoundHandler } = await import('../src/error-handler.js');
    const { resetSchemaCache } = await import('../src/routes/sheets.js');

    pool = servicePool;
    resetSchemaCache();

    // Mount the router directly rather than importing src/index.js, which
    // would bind PORT 3110 and start a heartbeat as an import side effect.
    const app = express();
    app.use(express.json({ limit: '4mb' }));
    app.use('/api', routes);
    app.use(notFoundHandler);
    app.use(errorHandler);
    await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', resolve); });
    baseUrl = `http://127.0.0.1:${server.address().port}`;
  } catch (err) {
    // A broken schema build is a real failure, not an environmental skip —
    // swallowing it would let a green run hide a broken harness. But make sure
    // the admin client is closed first: an open session on the throwaway
    // database makes after()'s DROP DATABASE block forever.
    if (admin) await admin.end().catch(() => {});
    throw err;
  }
});

after(async () => {
  if (server) {
    // close() alone waits for keep-alive sockets to drain, and undici (fetch)
    // holds them open — that hang, not a DB problem, is why the run never
    // printed its summary. closeAllConnections() reaps them first.
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
  // pool.end() MUST succeed before the DROP, or the drop blocks on the still-open
  // sessions it is waiting to reap.
  if (pool) await pool.end().catch(() => {});
  if (admin) {
    await admin.query(`DROP DATABASE IF EXISTS "${dbName}"`).catch(() => {});
    await admin.end().catch(() => {});
  }
});

// ── fixtures ─────────────────────────────────────────────────────────────

const makeField = async (name, typeCode) => {
  const r = await pool.query(
    `INSERT INTO shrapnel.field (name, property_name, label, field_type_code, is_calculated, field_index)
     VALUES ($1, $1 || '_' || $3, $1, $2, false, 0) RETURNING id`,
    [name, typeCode, randomUUID().slice(0, 8)]
  );
  return r.rows[0].id;
};
const makeObject = async () => {
  const r = await pool.query(`INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id`);
  return r.rows[0].id;
};

/** One sheet, 3 columns (long/string/boolean), 2 rows. */
async function setupSheet(name = `sheet-${randomUUID().slice(0, 8)}`) {
  const created = await api('POST', '/api/sheets', { name, description: 'fixture' });
  assert.equal(created.status, 201, JSON.stringify(created.body));
  const sheetId = created.body.sheet.id;

  const fLong = await makeField('qty', 1);
  const fStr = await makeField('title', 2);
  const fBool = await makeField('done', 4);

  const withLabel = await api('POST', `/api/sheets/${sheetId}/columns`, {
    field_id: fLong, display_label: 'Qty',
  });
  assert.equal(withLabel.status, 201, JSON.stringify(withLabel.body));
  for (const fieldId of [fStr, fBool]) {
    const r = await api('POST', `/api/sheets/${sheetId}/columns`, { field_id: fieldId });
    assert.equal(r.status, 201, JSON.stringify(r.body));
  }
  const o1 = await makeObject();
  const o2 = await makeObject();
  for (const objectId of [o1, o2]) {
    const r = await api('POST', `/api/sheets/${sheetId}/rows`, { object_id: objectId });
    assert.equal(r.status, 201, JSON.stringify(r.body));
  }
  return { sheetId, fLong, fStr, fBool, o1, o2 };
}

/**
 * Runtime skip. It has to be runtime, not registration-time: before() is what
 * discovers whether a throwaway database can be created, and that runs *after*
 * the describe() callbacks are evaluated.
 */
const itSkip = (name, fn) =>
  it(name, async (t) => {
    if (skipReason) return t.skip(skipReason);
    return fn(t);
  });

// ── reads ────────────────────────────────────────────────────────────────

describe('sheets REST — reads', () => {
  itSkip('lists sheets with projected counts', async () => {
    const { sheetId } = await setupSheet();
    const r = await api('GET', '/api/sheets');
    assert.equal(r.status, 200);
    const sheet = r.body.sheets.find((s) => s.id === sheetId);
    assert.ok(sheet, 'created sheet should be listed');
    assert.equal(sheet.column_count, 3);
    assert.equal(sheet.row_count, 2);
  });

  itSkip('fetches one sheet and 404s an unknown id', async () => {
    const { sheetId } = await setupSheet();
    const one = await api('GET', `/api/sheets/${sheetId}`);
    assert.equal(one.status, 200);
    assert.equal(one.body.sheet.id, sheetId);

    const missing = await api('GET', '/api/sheets/99999999');
    assert.equal(missing.status, 404);
    assert.match(missing.body.error.message, /not found/);
  });

  itSkip('rejects a non-integer sheet id as a 400, not a 500', async () => {
    const r = await api('GET', '/api/sheets/abc');
    // The :id(\\d+) constraint means this does not match the route at all, so
    // it lands on notFoundHandler — either way it must not be a 500.
    assert.ok([400, 404].includes(r.status), `unexpected ${r.status}`);
  });

  itSkip('returns columns in display order with the label fallback applied', async () => {
    const { sheetId, fLong, fStr, fBool } = await setupSheet();
    const r = await api('GET', `/api/sheets/${sheetId}/columns`);
    assert.equal(r.status, 200);
    assert.deepEqual(r.body.columns.map((c) => c.field_id), [fLong, fStr, fBool]);
    // display_label wins; the others fall back to label/property_name.
    assert.equal(r.body.columns[0].display_label, 'Qty');
    assert.equal(r.body.columns[0].label, 'qty');
  });

  itSkip('returns rows in rank order', async () => {
    const { sheetId, o1, o2 } = await setupSheet();
    const r = await api('GET', `/api/sheets/${sheetId}/rows`);
    assert.equal(r.status, 200);
    assert.deepEqual(r.body.rows.map((x) => x.row_object_id), [o1, o2]);
    assert.deepEqual(r.body.rows.map((x) => x.row_index), [1, 2]);
  });
});

// ── D5 encode path + D2/D4 cell semantics ───────────────────────────────

describe('sheets REST — cells', () => {
  itSkip('writes a cell through the encode path and reads it back', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    const set = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, {
      type_code: 2, value: 'hello',
    });
    assert.equal(set.status, 200, JSON.stringify(set.body));
    assert.ok(set.body.value_id);

    // D5: the write is a real EAV fact — value + typed extension, not a shadow.
    const oav = await pool.query(
      `SELECT oav.value_id, v.value_type_code, vs.value
         FROM shrapnel.object_attribute_value oav
         JOIN shrapnel.value v ON v.id = oav.value_id
         JOIN shrapnel.value_string vs ON vs.id = oav.value_id
        WHERE oav.object_id = $1 AND oav.field_id = $2`,
      [o1, fStr]
    );
    assert.equal(oav.rowCount, 1);
    assert.equal(oav.rows[0].value_type_code, 2);
    assert.equal(oav.rows[0].value, 'hello');

    const grid = await api('GET', `/api/sheets/${sheetId}/grid`);
    const cell = grid.body.cells.find((c) => c.row_object_id === o1 && c.field_id === fStr);
    assert.equal(cell.cell_text, 'hello');
  });

  itSkip('updates a cell in place — same OAV row, no duplication', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, { type_code: 2, value: 'first' });
    const before = await pool.query(
      `SELECT id, value_id FROM shrapnel.object_attribute_value WHERE object_id=$1 AND field_id=$2`,
      [o1, fStr]
    );
    await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, { type_code: 2, value: 'second' });
    const after = await pool.query(
      `SELECT id, value_id FROM shrapnel.object_attribute_value WHERE object_id=$1 AND field_id=$2`,
      [o1, fStr]
    );
    assert.equal(before.rowCount, 1);
    assert.deepEqual(after.rows[0].id, before.rows[0].id, 'the OAV row is updated, not replaced');
  });

  itSkip('clears a cell to sparse absence, and the value row is reference-counted away', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    const set = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, {
      type_code: 2, value: 'temp',
    });
    const valueId = set.body.value_id;

    const cleared = await api('DELETE', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`);
    assert.equal(cleared.status, 200, JSON.stringify(cleared.body));

    // D4: an empty cell is an ABSENT binding, not a stored NULL.
    const oav = await pool.query(
      `SELECT count(*)::int AS n FROM shrapnel.object_attribute_value WHERE object_id=$1 AND field_id=$2`,
      [o1, fStr]
    );
    assert.equal(oav.rows[0].n, 0);
    const val = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.value WHERE id=$1`, [valueId]);
    assert.equal(val.rows[0].n, 0, 'unreferenced value row is GCd');
    const ext = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.value_string WHERE id=$1`, [valueId]);
    assert.equal(ext.rows[0].n, 0, 'extension cascades with the value row');
    // The object itself survives.
    const obj = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.object_instance WHERE id=$1`, [o1]);
    assert.equal(obj.rows[0].n, 1);
  });

  itSkip('clearing an already-empty cell is an idempotent no-op, not an error', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    const r = await api('DELETE', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`);
    assert.equal(r.status, 200);
  });

  itSkip('leaves a shared value alone when clearing one of its bindings', async () => {
    // Reference-counted GC, not a blind cascade: two objects bound to one
    // value; clearing one must not destroy the other's data.
    const { sheetId, fStr, o1, o2 } = await setupSheet();
    const set = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, {
      type_code: 2, value: 'shared',
    });
    const valueId = set.body.value_id;
    await pool.query(
      `INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES ($1,$2,$3)`,
      [o2, fStr, valueId]
    );
    await api('DELETE', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`);

    const still = await pool.query(
      `SELECT vs.value FROM shrapnel.object_attribute_value oav
         JOIN shrapnel.value_string vs ON vs.id = oav.value_id
        WHERE oav.object_id=$1 AND oav.field_id=$2`,
      [o2, fStr]
    );
    assert.equal(still.rowCount, 1);
    assert.equal(still.rows[0].value, 'shared');
  });

  itSkip('encodes a standalone literal without binding a cell', async () => {
    const r = await api('POST', '/api/sheets/encode', { type_code: 1, value: '42' });
    assert.equal(r.status, 201, JSON.stringify(r.body));
    assert.ok(r.body.value_id);
  });
});

// ── membership + type guards (SHEETS-001..005) ──────────────────────────

describe('sheets REST — guards', () => {
  itSkip('refuses a cell whose field is not projected (SHEETS-001 → 409)', async () => {
    const { sheetId, o1 } = await setupSheet();
    const unprojected = await makeField('unprojected', 2);
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${unprojected}`, {
      type_code: 2, value: 'x',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-001');
  });

  itSkip('refuses a cell whose object is not a row (SHEETS-002 → 409)', async () => {
    const { sheetId, fStr } = await setupSheet();
    const outsider = await makeObject();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${outsider}/fields/${fStr}`, {
      type_code: 2, value: 'x',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-002');
  });

  itSkip('refuses a type that disagrees with the field declaration (SHEETS-003 → 409)', async () => {
    // The field declares String(2); supplying Long(1) must be refused.
    const { sheetId, fStr, o1 } = await setupSheet();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, {
      type_code: 1, value: '42',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-003');
  });

  itSkip('refuses an invalid literal (SHEETS-005 → 409)', async () => {
    const { sheetId, fLong, o1 } = await setupSheet();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`, {
      type_code: 1, value: 'not-a-number',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-005');
  });

  itSkip('refuses a non-canonical boolean (SHEETS-005 → 409)', async () => {
    const { sheetId, fBool, o1 } = await setupSheet();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fBool}`, {
      type_code: 4, value: 'YES',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-005');
  });

  itSkip('checks type agreement before literal parsing (SHEETS-003 wins)', async () => {
    const { sheetId, fLong, o1 } = await setupSheet();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`, {
      type_code: 7, value: 'not-a-uuid',
    });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-003');
  });

  itSkip('rejects an out-of-registry type_code as a 400 before touching the database', async () => {
    const { sheetId, fLong, o1 } = await setupSheet();
    const r = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`, {
      type_code: 42, value: 'x',
    });
    assert.equal(r.status, 400);
    assert.match(r.body.error.message, /type_code/);
  });

  itSkip('refuses an empty sheet name (SHEETS-006 → 409)', async () => {
    for (const name of ['', '   ']) {
      const r = await api('POST', '/api/sheets', { name });
      assert.equal(r.status, 409, `name=${JSON.stringify(name)}`);
      assert.equal(r.body.error.details.sheets_code, 'SHEETS-006');
    }
  });

  itSkip('refuses a duplicate sheet name (SHEETS-007 → 409)', async () => {
    const name = `dupe-${randomUUID().slice(0, 8)}`;
    assert.equal((await api('POST', '/api/sheets', { name })).status, 201);
    const again = await api('POST', '/api/sheets', { name });
    assert.equal(again.status, 409);
    assert.equal(again.body.error.details.sheets_code, 'SHEETS-007');
  });

  itSkip('rejects a missing sheet name as a 400', async () => {
    const r = await api('POST', '/api/sheets', {});
    assert.equal(r.status, 400);
  });
});

// ── D1 delete asymmetry ─────────────────────────────────────────────────

describe('sheets REST — D1 delete asymmetry', () => {
  itSkip('deleting a sheet kills the window but the data survives', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, { type_code: 2, value: 'durable' });

    const del = await api('DELETE', `/api/sheets/${sheetId}`);
    assert.equal(del.status, 200, JSON.stringify(del.body));

    const cols = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.sheet_column WHERE sheet_id=$1`, [sheetId]);
    assert.equal(cols.rows[0].n, 0, 'window columns die');
    const rows = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.sheet_row WHERE sheet_id=$1`, [sheetId]);
    assert.equal(rows.rows[0].n, 0, 'window rows die');
    const anchor = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.object_instance WHERE id=$1`, [sheetId]);
    assert.equal(anchor.rows[0].n, 0, 'the anchor object is reaped by the trigger');

    // ...and the actual data is untouched.
    const field = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.field WHERE id=$1`, [fStr]);
    assert.equal(field.rows[0].n, 1, 'the field survives');
    const obj = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.object_instance WHERE id=$1`, [o1]);
    assert.equal(obj.rows[0].n, 1, 'the row object survives');
    const cell = await pool.query(
      `SELECT vs.value FROM shrapnel.object_attribute_value oav
         JOIN shrapnel.value_string vs ON vs.id = oav.value_id
        WHERE oav.object_id=$1 AND oav.field_id=$2`,
      [o1, fStr]
    );
    assert.equal(cell.rowCount, 1, 'the OAV fact survives');
    assert.equal(cell.rows[0].value, 'durable');
  });

  itSkip('removing a column drops the projection and keeps the cells', async () => {
    const { sheetId, fLong, fStr, o1 } = await setupSheet();
    await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, { type_code: 2, value: 'kept' });

    const del = await api('DELETE', `/api/sheets/${sheetId}/columns/${fLong}`);
    assert.equal(del.status, 200, JSON.stringify(del.body));

    const proj = await pool.query(
      `SELECT count(*)::int AS n FROM shrapnel.sheet_column WHERE sheet_id=$1 AND field_id=$2`,
      [sheetId, fLong]
    );
    assert.equal(proj.rows[0].n, 0, 'the projection is gone');
    const stillProjected = await pool.query(
      `SELECT count(*)::int AS n FROM shrapnel.sheet_column WHERE sheet_id=$1 AND field_id=$2`,
      [sheetId, fStr]
    );
    assert.equal(stillProjected.rows[0].n, 1, 'other columns are untouched');
    const cell = await pool.query(
      `SELECT vs.value FROM shrapnel.object_attribute_value oav
         JOIN shrapnel.value_string vs ON vs.id = oav.value_id
        WHERE oav.object_id=$1 AND oav.field_id=$2`,
      [o1, fStr]
    );
    assert.equal(cell.rows[0].value, 'kept');
  });

  itSkip('removing a row drops the junction and keeps the object', async () => {
    const { sheetId, o1, o2 } = await setupSheet();
    const del = await api('DELETE', `/api/sheets/${sheetId}/rows/${o2}`);
    assert.equal(del.status, 200, JSON.stringify(del.body));
    const junction = await pool.query(
      `SELECT count(*)::int AS n FROM shrapnel.sheet_row WHERE sheet_id=$1 AND row_object_id=$2`,
      [sheetId, o2]
    );
    assert.equal(junction.rows[0].n, 0);
    const obj = await pool.query(`SELECT count(*)::int AS n FROM shrapnel.object_instance WHERE id=$1`, [o2]);
    assert.equal(obj.rows[0].n, 1, 'the object survives — that is the asymmetry');
  });

  itSkip('404s removing a projection or row that is not there', async () => {
    const { sheetId } = await setupSheet();
    const stranger = await makeField('stranger', 2);
    const outsider = await makeObject();
    assert.equal((await api('DELETE', `/api/sheets/${sheetId}/columns/${stranger}`)).status, 404);
    assert.equal((await api('DELETE', `/api/sheets/${sheetId}/rows/${outsider}`)).status, 404);
  });

  itSkip('refuses a duplicate column projection (SHEETS-010 → 409)', async () => {
    const { sheetId, fLong } = await setupSheet();
    const r = await api('POST', `/api/sheets/${sheetId}/columns`, { field_id: fLong });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-010');
  });

  itSkip('refuses a duplicate row membership (SHEETS-012 → 409)', async () => {
    const { sheetId, o1 } = await setupSheet();
    const r = await api('POST', `/api/sheets/${sheetId}/rows`, { object_id: o1 });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-012');
  });

  itSkip('refuses an unknown field (SHEETS-009 → 409) and unknown object (SHEETS-011 → 409)', async () => {
    const { sheetId } = await setupSheet();
    const noField = await api('POST', `/api/sheets/${sheetId}/columns`, { field_id: 99999999 });
    assert.equal(noField.status, 409);
    assert.equal(noField.body.error.details.sheets_code, 'SHEETS-009');
    const noObject = await api('POST', `/api/sheets/${sheetId}/rows`, { object_id: 99999999 });
    assert.equal(noObject.status, 409);
    assert.equal(noObject.body.error.details.sheets_code, 'SHEETS-011');
  });
});

// ── fractional ranks ────────────────────────────────────────────────────

describe('sheets REST — fractional rank ordering', () => {
  itSkip('moves a row to a fractional rank in O(1)', async () => {
    const { sheetId, o1, o2 } = await setupSheet();
    const before = await api('GET', `/api/sheets/${sheetId}/rows`);
    assert.deepEqual(before.body.rows.map((r) => r.row_object_id), [o1, o2]);

    const move = await api('POST', `/api/sheets/${sheetId}/rows/${o2}/move`, { row_index: 0.5 });
    assert.equal(move.status, 200, JSON.stringify(move.body));
    assert.equal(move.body.row.row_index, 0.5);

    const after = await api('GET', `/api/sheets/${sheetId}/rows`);
    assert.deepEqual(after.body.rows.map((r) => r.row_object_id), [o2, o1], 'o2 is now first');
  });

  itSkip('inserts a column at a fractional index', async () => {
    const { sheetId, fLong } = await setupSheet();
    const extra = await makeField('extra', 2);
    const r = await api('POST', `/api/sheets/${sheetId}/columns`, {
      field_id: extra, column_index: 1.5,
    });
    assert.equal(r.status, 201, JSON.stringify(r.body));
    const cols = await api('GET', `/api/sheets/${sheetId}/columns`);
    assert.deepEqual(cols.body.columns.slice(0, 2).map((c) => c.field_id), [fLong, extra]);
  });

  itSkip('refuses to move a non-member (SHEETS-014 → 409)', async () => {
    const { sheetId } = await setupSheet();
    const outsider = await makeObject();
    const r = await api('POST', `/api/sheets/${sheetId}/rows/${outsider}/move`, { row_index: 1.5 });
    assert.equal(r.status, 409);
    assert.equal(r.body.error.details.sheets_code, 'SHEETS-014');
  });

  itSkip('rejects a move with no index as a 400 rather than letting it become an append', async () => {
    const { sheetId, o1 } = await setupSheet();
    const r = await api('POST', `/api/sheets/${sheetId}/rows/${o1}/move`, {});
    assert.equal(r.status, 400);
    assert.match(r.body.error.message, /row_index is required/);
  });
});

// ── sparse grid + all seven types ───────────────────────────────────────

describe('sheets REST — grid', () => {
  itSkip('renders a sparse grid: 6 lines for 2x3, empty cells are null', async () => {
    const { sheetId, fLong, o1 } = await setupSheet();
    await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`, { type_code: 1, value: '7' });

    const r = await api('GET', `/api/sheets/${sheetId}/grid`);
    assert.equal(r.status, 200);
    assert.equal(r.body.cells.length, 6, '2 rows x 3 columns');
    const filled = r.body.cells.filter((c) => c.cell_text !== null);
    assert.equal(filled.length, 1);
    assert.equal(filled[0].cell_text, '7');
    assert.equal(filled[0].cell_type_code, 1);
    for (const c of r.body.cells.filter((x) => x.cell_text === null)) {
      assert.equal(c.cell_type_code, null, 'an empty cell has no type — it is absent, not zeroed');
    }
    assert.ok(r.body.columns.some((c) => c.label === 'Qty'), 'display_label is carried through');
  });

  itSkip('round-trips all seven value types', async () => {
    const created = await api('POST', '/api/sheets', { name: `types-${randomUUID().slice(0, 8)}` });
    const sheetId = created.body.sheet.id;
    const specs = [
      ['f_long', 1, '123', '123'],
      ['f_str', 2, 's', 's'],
      ['f_double', 3, '2.5', '2.5'],
      ['f_bool', 4, 'false', 'false'],
      ['f_ts', 5, '2026-09-16T00:00:00Z', null],
      ['f_uuid', 7, '0f33a779-1e34-4980-bfdc-619e610a5d9f', null],
    ];
    const fields = {};
    for (const [name, code] of specs) {
      fields[name] = await makeField(name, code);
      const r = await api('POST', `/api/sheets/${sheetId}/columns`, { field_id: fields[name] });
      assert.equal(r.status, 201, JSON.stringify(r.body));
    }
    const jsonField = await makeField('f_jsonb', 6);
    fields.f_jsonb = jsonField;
    assert.equal((await api('POST', `/api/sheets/${sheetId}/columns`, { field_id: jsonField })).status, 201);

    const obj = await makeObject();
    assert.equal((await api('POST', `/api/sheets/${sheetId}/rows`, { object_id: obj })).status, 201);

    for (const [name, code, lit] of specs) {
      const r = await api('PUT', `/api/sheets/${sheetId}/rows/${obj}/fields/${fields[name]}`, {
        type_code: code, value: lit,
      });
      assert.equal(r.status, 200, `${name}: ${JSON.stringify(r.body)}`);
    }
    // A jsonb cell carries its payload as JSON, not as a text literal.
    const j = await api('PUT', `/api/sheets/${sheetId}/rows/${obj}/fields/${jsonField}`, {
      type_code: 6, json: { a: 1 },
    });
    assert.equal(j.status, 200, JSON.stringify(j.body));

    const grid = await api('GET', `/api/sheets/${sheetId}/grid`);
    const read = (fieldId) =>
      grid.body.cells.find((c) => c.row_object_id === obj && c.field_id === fieldId);
    for (const [name, , , expect] of specs) {
      const cell = read(fields[name]);
      assert.ok(cell, `${name}: missing from the grid`);
      if (expect !== null) {
        assert.equal(cell.cell_text, expect, `${name}: rendered value`);
      } else {
        assert.notEqual(cell.cell_text, null, `${name}: expected a non-null rendering`);
      }
    }
    // jsonb survives the round trip as structured data.
    const jCell = read(jsonField);
    assert.deepEqual(jCell.cell_jsonb, { a: 1 });
  });

  itSkip('scopes the /cells read to the sheet window', async () => {
    const { sheetId, fStr, o1 } = await setupSheet();
    const set = await api('PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fStr}`, {
      type_code: 2, value: 'in',
    });
    const valueId = set.body.value_id;

    // A real fact, bound to an object and field that this sheet neither rows
    // nor projects. It must not leak into the sheet-scoped read.
    const otherField = await makeField('other', 2);
    const otherObj = await makeObject();
    const value = await pool.query(
      `INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id`
    );
    await pool.query(`INSERT INTO shrapnel.value_string (id, value) VALUES ($1,'out')`, [
      value.rows[0].id,
    ]);
    await pool.query(
      `INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES ($1,$2,$3)`,
      [otherObj, otherField, value.rows[0].id]
    );
    assert.notEqual(value.rows[0].id, valueId);

    const cells = await api('GET', `/api/sheets/${sheetId}/cells`);
    assert.equal(cells.status, 200);
    assert.equal(cells.body.cells.length, 1, 'only the projected/rowed cell is in scope');
    assert.equal(cells.body.cells[0].cell_text, 'in');
  });
});

// ── schema availability ─────────────────────────────────────────────────

describe('sheets REST — schema availability', () => {
  itSkip('reports the V166 objects it depends on as present', async () => {
    const { sheetsSchemaAvailable, resetSchemaCache } = await import('../src/routes/sheets.js');
    resetSchemaCache();
    assert.equal(await sheetsSchemaAvailable(), true, '9 sheet_* functions and 3 views must exist');
  });

  itSkip('answers 503 (not 500) when the sheet schema is absent', async () => {
    // Point a fresh router at a database with the service chain but NO V166,
    // which is the state of every host until the DBA applies the draft.
    const bare = `nexus_sheet_nov166_${process.pid}_${randomUUID().slice(0, 6)}`;
    const client = new pg.Client({ connectionString: dsnFor('postgres') });
    await client.connect();
    await client.query(`DROP DATABASE IF EXISTS "${bare}"`);
    await client.query(`CREATE DATABASE "${bare}"`);
    await client.end();

    // The bare chain, no V166 — the state of every host until the DBA applies
    // the draft. (The pg driver cannot apply this chain at all; see buildSchema.)
    buildSchema(bare, { withV166: false });
    const { Pool } = pg;
    const barePool = new Pool({ connectionString: dsnFor(bare) });
    const { sheetsSchemaAvailable, resetSchemaCache } = await import('../src/routes/sheets.js');
    resetSchemaCache();
    assert.equal(await sheetsSchemaAvailable(barePool), false, 'no sheet functions without V166');

    // And the guard produces 503, not 500, when the query itself fails.
    const { default: express } = await import('express');
    const { errorHandler, notFoundHandler } = await import('../src/error-handler.js');
    const app = express();
    // Four parameters: the arity is how Express 4 knows this is an error
    // handler, and a `throw` inside an async handler is an unhandled rejection
    // it never sees. next() is the only way through.
    app.get('/probe', async (_req, res, next) => {
      const { mapSheetError } = await import('../src/routes/sheets.js');
      try {
        await barePool.query('SELECT shrapnel.sheet_create($1,$2)', ['x', null]);
        res.json({ ok: true });
      } catch (err) {
        next(mapSheetError(err) ?? err);
      }
    });
    app.use(notFoundHandler);
    app.use(errorHandler);
    const s = app.listen(0, '127.0.0.1');
    await new Promise((r) => s.once('listening', r));
    const port = s.address().port;

    const res = await fetch(`http://127.0.0.1:${port}/probe`, {
      headers: { connection: 'close' },
    });
    assert.equal(res.status, 503, 'a client must be able to tell "not applied" from "broken"');
    const body = await res.json();
    assert.equal(body.error.details.sheets_code, 'SHEETS-SCHEMA');
    assert.match(body.error.message, /V166/);

    s.closeAllConnections();
    await new Promise((r) => s.close(r));
    await barePool.end();
    const cleanup = new pg.Client({ connectionString: dsnFor('postgres') });
    await cleanup.connect();
    await cleanup.query(`DROP DATABASE IF EXISTS "${bare}"`);
    await cleanup.end();
    resetSchemaCache();
  });
});

// ── route inventory ─────────────────────────────────────────────────────

describe('sheets REST — route inventory', () => {
  // notFoundHandler answers {error:{message:'not_found'}}; a route that MATCHED
  // answers anything else — a 400, a 409, or a route-level 404 whose message
  // names the thing. That difference is the whole discriminator, so this
  // inventory cannot be fooled by a 404-for-wrong-reasons.
  const unmatched = (r) => r.status === 404 && r.body?.error?.message === 'not_found';

  itSkip('every documented sheet route is mounted', async () => {
    const { sheetId, o1, fLong } = await setupSheet();
    const sheet = [
      ['GET', `/api/sheets`],
      ['GET', `/api/sheets/${sheetId}`],
      ['GET', `/api/sheets/${sheetId}/columns`],
      ['GET', `/api/sheets/${sheetId}/rows`],
      ['GET', `/api/sheets/${sheetId}/cells`],
      ['GET', `/api/sheets/${sheetId}/grid`],
      ['POST', `/api/sheets`],
      ['POST', `/api/sheets/encode`],
      ['DELETE', `/api/sheets/${sheetId}`],
      ['POST', `/api/sheets/${sheetId}/columns`],
      ['DELETE', `/api/sheets/${sheetId}/columns/${fLong}`],
      ['POST', `/api/sheets/${sheetId}/rows`],
      ['DELETE', `/api/sheets/${sheetId}/rows/${o1}`],
      ['POST', `/api/sheets/${sheetId}/rows/${o1}/move`],
      ['PUT', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`],
      ['DELETE', `/api/sheets/${sheetId}/rows/${o1}/fields/${fLong}`],
    ];
    for (const [method, path] of sheet) {
      const r = await api(method, path, method === 'GET' || method === 'DELETE' ? undefined : {});
      assert.ok(!unmatched(r), `${method} ${path} is not mounted: ${JSON.stringify(r.body)}`);
    }
  });
});

