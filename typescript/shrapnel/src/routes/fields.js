import { Router } from 'express';
import { pool } from '../db.js';
import { assertKnownTypeName } from '../lib/types.js';
import { normaliseFieldSpec } from '../lib/encode.js';
import { badRequest } from '../errors.js';
import { writeLimiter } from '../lib/rate-limit.js';

export const fieldsRouter = Router();

fieldsRouter.get('/', async (req, res, next) => {
  try {
    const limit  = Math.min(500, Math.max(1,  parseInt(req.query.limit  ?? '100', 10) || 100));
    const offset = Math.max(0, parseInt(req.query.offset ?? '0', 10) || 0);
    const typeCode = req.query.type_code ? Number(req.query.type_code) : null;
    const args = [limit, offset];
    let q = `SELECT id, is_calculated, field_index, label, name, property_name, field_type_code, created_at, updated_at
             FROM shrapnel.field`;
    if (typeCode != null && Number.isInteger(typeCode)) {
      q += ` WHERE field_type_code = $3`;
      args.push(typeCode);
    }
    q += ` ORDER BY field_index, id LIMIT $1 OFFSET $2`;
    const r = await pool.query(q, args);
    res.json({ fields: r.rows });
  } catch (err) {
    next(err);
  }
});

fieldsRouter.get('/:id', async (req, res, next) => {
  try {
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return next(badRequest('id must be integer'));
    const r = await pool.query(
      `SELECT id, is_calculated, field_index, label, name, property_name, field_type_code, created_at, updated_at
       FROM shrapnel.field WHERE id = $1`,
      [id]
    );
    if (r.rowCount === 0) return res.status(404).json({ error: { message: 'not_found' } });
    res.json({ field: r.rows[0] });
  } catch (err) {
    next(err);
  }
});

// POST /api/fields
// Body: { "is_calculated": false, "field_index": 1, "label": "Full Name",
//         "name": "Name", "property_name": "name",
//         "type": "String" | "field_type_code": 2 }
fieldsRouter.post('/', writeLimiter, async (req, res, next) => {
  try {
    const spec = normaliseFieldSpec(req.body);
    const r = await pool.query(
      `INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
       VALUES ($1, $2, $3, $4, $5, $6)
       ON CONFLICT (property_name) DO UPDATE SET name = EXCLUDED.name
       RETURNING *`,
      [spec.is_calculated, spec.field_index, spec.label, spec.name, spec.property_name, spec.field_type_code]
    );
    res.status(201).json({ field: r.rows[0] });
  } catch (err) {
    // Defensive: assertKnownTypeName throws {status:400} Error objects (S3923:
    // the old `err && err.status ? err : err` ternary was a no-op — both
    // branches returned err, so just forward it).
    next(err);
  }
});
