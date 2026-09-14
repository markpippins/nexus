import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { pool, withTransaction } from '../db.js';
import { badRequest, notFound, conflict } from '../errors.js';

export const stereotypesRouter = Router();

const stereotypesReadLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100,
  standardHeaders: true,
  legacyHeaders: false
});

// ── helpers (exported for unit tests) ───────────────────────────────────

export function intParam(value, label) {
  // Reject null/undefined/'' explicitly: Number() coerces them to 0, which
  // would otherwise pass the integer check.
  if (value === null || value === undefined || value === '') {
    throw badRequest(`${label} must be an integer`);
  }
  const n = Number(value);
  if (!Number.isInteger(n)) throw badRequest(`${label} must be an integer`);
  return n;
}

export function stringArray(value, label) {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value) || value.some((x) => typeof x !== 'string' || x.trim() === '')) {
    throw badRequest(`${label} must be an array of non-empty strings`);
  }
  return value.map((x) => x.trim());
}

// Translate PG errors raised by the 0005 API functions into HTTP semantics.
// (error-handler.js already maps 23505/23503; the stereotype functions raise
// 23514 check_violation and P0001 business-rule exceptions.)
export function mapPgError(err) {
  if (err && err.code === '23514') {
    return conflict(err.message, err.detail || undefined);
  }
  if (err && err.code === 'P0001') {
    return conflict(err.message);
  }
  return null;
}

async function resolveHeadRevision(client, name) {
  const r = await client.query(
    `SELECT stereotype_id, head_revision_id, version FROM shrapnel.stereotype_resolve($1)`,
    [name]
  );
  if (r.rowCount === 0) throw notFound(`stereotype '${name}' not found`);
  return r.rows[0];
}

// ── introspection ────────────────────────────────────────────────────────

// GET /api/stereotypes — all stereotype identities with their head revision
stereotypesRouter.get('/', async (_req, res, next) => {
  try {
    const r = await pool.query(
      `SELECT s.id    AS stereotype_id,
              s.name,
              s.description,
              r.id    AS head_revision_id,
              r.version,
              r.depth,
              r.contract_fingerprint,
              r.created_at
         FROM shrapnel.stereotype s
         JOIN shrapnel.stereotype_revision r ON r.stereotype_id = s.id
        WHERE r.version = (SELECT max(version) FROM shrapnel.stereotype_revision
                            WHERE stereotype_id = s.id)
        ORDER BY s.name`
    );
    res.json({ stereotypes: r.rows });
  } catch (err) {
    next(err);
  }
});

// GET /api/stereotypes/:name — head revision detail
stereotypesRouter.get('/:name', stereotypesReadLimiter, async (req, res, next) => {
  try {
    const head = await resolveHeadRevision(pool, req.params.name);
    const d = await pool.query(
      `SELECT r.id, r.stereotype_id, r.version, r.parent_revision_id,
              r.parent_stereotype_id, r.extends_rationale, r.depth,
              r.contract_fingerprint, r.created_at
         FROM shrapnel.stereotype_revision r
        WHERE r.id = $1`,
      [head.head_revision_id]
    );
    res.json({ revision: d.rows[0] });
  } catch (err) {
    next(mapPgError(err) ?? err);
  }
});

// GET /api/stereotypes/:name/chain — lineage of the head revision
stereotypesRouter.get('/:name/chain', async (req, res, next) => {
  try {
    const head = await resolveHeadRevision(pool, req.params.name);
    const r = await pool.query(
      `SELECT name, version, hop FROM shrapnel.stereotype_chain($1)`,
      [head.head_revision_id]
    );
    res.json({ stereotype: req.params.name, head_revision_id: head.head_revision_id, chain: r.rows });
  } catch (err) {
    next(mapPgError(err) ?? err);
  }
});

// GET /api/stereotypes/:name/contract — compiled effective contract
// (parent ∪ child, origin provenance per field)
stereotypesRouter.get('/:name/contract', async (req, res, next) => {
  try {
    const head = await resolveHeadRevision(pool, req.params.name);
    const r = await pool.query(
      `SELECT property_name, required, origin_revision, origin_stereotype, origin_version
         FROM shrapnel.stereotype_effective_contract($1)
        ORDER BY required DESC, property_name`,
      [head.head_revision_id]
    );
    res.json({
      stereotype: req.params.name,
      head_revision_id: head.head_revision_id,
      contract: r.rows,
    });
  } catch (err) {
    next(mapPgError(err) ?? err);
  }
});

// ── construction ─────────────────────────────────────────────────────────

// POST /api/stereotypes/revisions — create a revision (root or child)
// Body: { name, extends_revision?, rationale?, required_fields[], optional_fields? }
stereotypesRouter.post('/revisions', async (req, res, next) => {
  try {
    const body = req.body ?? {};
    const name = typeof body.name === 'string' ? body.name.trim() : '';
    if (name === '') throw badRequest('name is required');

    const requiredFields = stringArray(body.required_fields, 'required_fields');
    if (requiredFields === null) throw badRequest('required_fields is required');
    const optionalFields = stringArray(body.optional_fields, 'optional_fields');

    const extendsRevision = body.extends_revision;
    if (extendsRevision !== undefined && extendsRevision !== null && !Number.isInteger(Number(extendsRevision))) {
      throw badRequest('extends_revision must be an integer revision id');
    }

    let rationale = typeof body.rationale === 'string' ? body.rationale.trim() : null;
    if (extendsRevision != null && (rationale === null || rationale === '')) {
      throw badRequest('rationale is required when extends_revision is provided');
    }
    if (extendsRevision == null) rationale = null;

    const out = await withTransaction(async (client) => {
      const r = await client.query(
        `SELECT shrapnel.stereotype_create_revision($1, $2, $3, $4, $5) AS revision_id`,
        [name, extendsRevision ?? null, rationale, requiredFields, optionalFields]
      );
      const revisionId = r.rows[0].revision_id;
      const d = await client.query(
        `SELECT r.id AS revision_id, s.name, r.version, r.depth, r.contract_fingerprint
           FROM shrapnel.stereotype_revision r
           JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
          WHERE r.id = $1`,
        [revisionId]
      );
      return d.rows[0];
    });

    res.status(201).json(out);
  } catch (err) {
    next(mapPgError(err) ?? err);
  }
});

// GET /api/objects/:id/conformance and POST /api/objects/:id/classify live on
// the objects router (they are object-scoped); see routes/objects.js.
