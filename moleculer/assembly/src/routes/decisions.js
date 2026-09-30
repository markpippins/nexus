import { Router } from 'express';
import { pool } from '../db.js';
import { BadRequestError } from '../errors.js';

/**
 * Decision-card persistence — Assembly "Agreed selection:" submissions
 * stored as derived artifacts in the shrapnel EAV object store
 * (the `shrapnel` portion of the nexus/resolution schema).
 *
 * One shrapnel object_instance per submitted decision; typed fields via
 * field_type codes 1..7 with physical values in the value_<type>
 * extension tables, joined through object_attribute_value. This mirrors
 * the canonical write path in typescript/shrapnel (lib/encode.js) so the
 * same rows stay decodable by the shrapnel REST surface.
 */
export const decisionsRouter = Router();

const EXT_BY_TYPE = {
  1: 'value_long',
  2: 'value_string',
  3: 'value_double',
  4: 'value_boolean',
  5: 'value_timestamp',
  6: 'value_jsonb',
  7: 'value_uuid',
};

// Field upsert (idempotent by property_name — mirrors encode.js).
async function ensureField(client, propertyName, typeCode, fieldIndex) {
  const res = await client.query(
    `INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
     VALUES (false, $1, $2, $2, $2, $3)
     ON CONFLICT (property_name) DO UPDATE SET name = EXCLUDED.name
     RETURNING id`,
    [fieldIndex, propertyName, typeCode],
  );
  return res.rows[0].id;
}

// Insert a value base row + its typed extension row; return value id.
async function insertTypedValue(client, typeCode, sqlValue) {
  const vRes = await client.query(
    'INSERT INTO shrapnel.value (value_type_code) VALUES ($1) RETURNING id',
    [typeCode],
  );
  const valueId = vRes.rows[0].id;
  const ext = EXT_BY_TYPE[typeCode];
  if (!ext) throw new Error(`unsupported shrapnel field_type_code ${typeCode}`);
  await client.query(
    `INSERT INTO shrapnel.${ext} (id, value) VALUES ($1, $2)`,
    [valueId, sqlValue],
  );
  return valueId;
}

// ── POST /api/decisions — persist one submitted decision card ──────
decisionsRouter.post('/', async (req, res, next) => {
  let client;
  try {
    const {
      threadId,
      sourceId,        // 'thread' | comment id
      mode,            // 'tasks' | 'choices'
      blockIdx,
      selections,      // [{ itemIdx, label, selected }]
      replyCommentId,  // comment id of the posted "Agreed selection:" reply
      submittedBy,     // user name
      submittedAt,     // ISO string
    } = req.body || {};

    if (!threadId || !sourceId || mode == null || blockIdx == null) {
      throw new BadRequestError('threadId, sourceId, mode and blockIdx are required');
    }
    if (!['tasks', 'choices'].includes(mode)) {
      throw new BadRequestError(`mode must be 'tasks' or 'choices' (got ${mode})`);
    }
    if (!Array.isArray(selections)) {
      throw new BadRequestError('selections must be an array');
    }

    // Order-deterministic field set for the decision object.
    const entries = [
      ['source_id', 2, 1, String(sourceId)],
      ['thread_id', 2, 2, String(threadId)],
      ['mode', 2, 3, mode],
      ['block_idx', 1, 4, String(blockIdx)],
      ['selections', 6, 5, JSON.stringify(selections)],
      ['reply_comment_id', 2, 6, replyCommentId ? String(replyCommentId) : ''],
      ['submitted_by', 2, 7, submittedBy ? String(submittedBy) : ''],
      ['submitted_at', 5, 8, submittedAt ? new Date(submittedAt).toISOString() : new Date().toISOString()],
    ];

    client = await pool.connect();
    await client.query('BEGIN');

    const oi = await client.query(
      'INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id',
    );
    const objectId = oi.rows[0].id;

    for (const [propertyName, typeCode, fieldIndex, rawValue] of entries) {
      const fieldId = await ensureField(client, propertyName, typeCode, fieldIndex);
      const valueId = await insertTypedValue(client, typeCode, rawValue);
      await client.query(
        'INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES ($1, $2, $3)',
        [objectId, fieldId, valueId],
      );
    }

    await client.query('COMMIT');
    res.status(201).json({
      id: String(objectId),
      threadId,
      sourceId,
      mode,
      blockIdx: Number(blockIdx),
      submittedAt: submittedAt || new Date().toISOString(),
    });
  } catch (err) {
    if (client) {
      try { await client.query('ROLLBACK'); } catch { /* already closed */ }
    }
    next(err);
  } finally {
    if (client) client.release();
  }
});

// ── GET /api/decisions?threadId=… — decisions persisted in a thread ────
decisionsRouter.get('/', async (req, res, next) => {
  try {
    const { threadId } = req.query;
    if (!threadId) {
      throw new BadRequestError('threadId query param is required');
    }

    const objectRows = await pool.query(
      `SELECT oav.object_id
         FROM shrapnel.object_attribute_value oav
         JOIN shrapnel.field f ON f.id = oav.field_id AND f.property_name = 'thread_id'
         JOIN shrapnel.value v ON v.id = oav.value_id
         JOIN shrapnel.value_string vs ON vs.id = v.id
        WHERE vs.value = $1
        ORDER BY oav.object_id DESC`,
      [String(threadId)],
    );
    const objectIds = objectRows.rows.map((r) => r.object_id);
    if (objectIds.length === 0) return res.json({ items: [] });

    const decodeRes = await pool.query(
      `SELECT oav.object_id,
              f.property_name,
              (SELECT vl.value::text  FROM shrapnel.value_long      vl WHERE vl.id = oav.value_id) AS v_long,
              (SELECT vs.value       FROM shrapnel.value_string    vs WHERE vs.id = oav.value_id) AS v_string,
              (SELECT vd.value::text FROM shrapnel.value_double    vd WHERE vd.id = oav.value_id) AS v_double,
              (SELECT vb.value::text FROM shrapnel.value_boolean   vb WHERE vb.id = oav.value_id) AS v_bool,
              (SELECT vt.value::text FROM shrapnel.value_timestamp vt WHERE vt.id = oav.value_id) AS v_ts,
              (SELECT vj.value::text FROM shrapnel.value_jsonb     vj WHERE vj.id = oav.value_id) AS v_json,
              (SELECT vu.value::text FROM shrapnel.value_uuid      vu WHERE vu.id = oav.value_id) AS v_uuid
       FROM shrapnel.object_attribute_value oav
       JOIN shrapnel.field f ON f.id = oav.field_id
       WHERE oav.object_id = ANY($1::bigint[])
       ORDER BY oav.object_id, f.field_index, f.id`,
      [objectIds],
    );
    const rows = decodeRes.rows;

    const objects = new Map();
    for (const row of rows) {
      const oid = row.object_id;
      if (!objects.has(oid)) objects.set(oid, {});
      const obj = objects.get(oid);
      obj[row.property_name] =
        row.v_long ?? row.v_string ?? row.v_double ?? row.v_bool ?? row.v_ts ?? row.v_json ?? row.v_uuid;
    }

    const items = objectIds
      .filter((oid) => objects.has(oid))
      .map((oid) => {
        const o = objects.get(oid);
        let selections = [];
        try { selections = o.selections ? JSON.parse(o.selections) : []; } catch { /* keep [] */ }
        return {
          id: String(oid),
          threadId: o.thread_id ?? null,
          sourceId: o.source_id ?? null,
          mode: o.mode ?? null,
          blockIdx: o.block_idx != null ? Number(o.block_idx) : null,
          selections,
          replyCommentId: o.reply_comment_id || null,
          submittedBy: o.submitted_by || null,
          submittedAt: o.submitted_at || null,
        };
      });

    res.json({ items });
  } catch (err) {
    next(err);
  }
});