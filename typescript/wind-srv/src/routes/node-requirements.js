import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { NotFoundError, BadRequestError } from '../errors.js';
import {
  resolveNodeRequirements, resolveContextBundle, resolveCapability,
  verifyRoleCredential, SATISFACTION_STATES,
} from '../capability-resolver.js';
import {
  normalizeRequirements, insertRequirement, replaceRequirement, attachVerdicts,
} from '../requirements.js';
import { query } from '../db.js';

export const nodeRequirementsRouter = Router();

// CodeQL js/missing-rate-limiting (alert 695): the resolve routes perform
// authorization/credential verification, so every handler is rate-limited.
// Read-heavy: generous window, house shape mirrors provider-contracts.js.
const resolverReadLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 60,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: { error: 'capability resolver read rate limit exceeded' },
});

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function assertUuid(id, label) {
  if (!UUID_RE.test(id)) throw new BadRequestError(`${label} must be a UUID`);
}

// GET /api/nodes/{id}/requirements — per-requirement verdicts (warn-mode)
nodeRequirementsRouter.get('/:id/requirements', resolverReadLimiter, async (req, res, next) => {
  try {
    assertUuid(req.params.id, 'node id');
    const resolved = await resolveNodeRequirements(req.params.id);
    if (!resolved) throw new NotFoundError('Node not found');
    res.json(resolved);
  } catch (err) { next(err); }
});

// GET /api/nodes/{id}/resolve — the ResolvedContextBundle (refs only)
nodeRequirementsRouter.get('/:id/resolve', resolverReadLimiter, async (req, res, next) => {
  try {
    assertUuid(req.params.id, 'node id');
    const bundle = await resolveContextBundle(req.params.id);
    if (!bundle) throw new NotFoundError('Node not found');
    res.json(bundle);
  } catch (err) { next(err); }
});

// POST /api/nodes/{id}/requirements — register demands on an existing node
// (the seeding path for nodes created before V181). Body: array of
// capability keys / {capabilityKey} / {roleCredential}, or a single one.
// Re-registering an identical OPEN demand is idempotent-by-conflict:
// surfaced as `duplicate: true`, not a 500.
nodeRequirementsRouter.post('/:id/requirements', resolverReadLimiter, async (req, res, next) => {
  try {
    assertUuid(req.params.id, 'node id');
    const node = await query('SELECT id FROM wind.workflow_nodes WHERE id = $1', [req.params.id]);
    if (node.rows.length === 0) throw new NotFoundError('Node not found');
    const demands = normalizeRequirements(req.body?.requirements ?? req.body);
    if (demands.length === 0) throw new BadRequestError('no valid demands in request body');
    const inserted = [];
    const duplicates = [];
    const failed = [];
    for (const demand of demands) {
      try {
        inserted.push(await insertRequirement(req.params.id, demand));
      } catch (e) {
        if (String(e.code) === '23505' || /node_requirements_open/.test(String(e.detail || e.message || e))) {
          duplicates.push(demand);
        } else {
          failed.push({ demand, error: String(e.message || e) });
        }
      }
    }
    res.status(201).json({
      node_id: req.params.id, inserted, duplicates, failed,
      mode: 'warn',
    });
  } catch (err) { next(err); }
});

// PUT /api/nodes/{id}/requirements/:reqId — close-then-insert replacement
// of one open demand (V175 convention; never in-place history mutation).
nodeRequirementsRouter.put('/:id/requirements/:reqId', resolverReadLimiter, async (req, res, next) => {
  try {
    assertUuid(req.params.id, 'node id');
    assertUuid(req.params.reqId, 'requirement id');
    const demands = normalizeRequirements(req.body?.requirements ?? req.body);
    if (demands.length !== 1) {
      throw new BadRequestError('body must name exactly one replacement demand');
    }
    const opened = await replaceRequirement(req.params.id, req.params.reqId, demands[0]);
    res.json({ replaced: true, previous_id: req.params.reqId, requirement: opened, mode: 'warn' });
  } catch (err) {
    if (err instanceof Error && /not found/.test(err.message)) {
      return next(new NotFoundError(err.message));
    }
    next(err);
  }
});

// GET /api/nodes/{id}/requirements/current — stored rows + live verdicts
// attached (the seed-verification view; distinct from the resolver's
// contract route above, which resolves from scratch).
nodeRequirementsRouter.get('/:id/requirements/current', resolverReadLimiter, async (req, res, next) => {
  try {
    assertUuid(req.params.id, 'node id');
    const node = await query('SELECT id FROM wind.workflow_nodes WHERE id = $1', [req.params.id]);
    if (node.rows.length === 0) throw new NotFoundError('Node not found');
    const rows = await query(
      `SELECT id, capability_key, role_credential, last_verdict, valid_from
       FROM wind.node_requirements
       WHERE node_id = $1 AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       ORDER BY created_at`,
      [req.params.id]);
    const withVerdicts = await attachVerdicts(rows.rows);
    res.json({ node_id: req.params.id, requirements: withVerdicts, mode: 'warn' });
  } catch (err) { next(err); }
});

// GET /api/capabilities/{key}/resolve — resolve a bare capability demand
nodeRequirementsRouter.get('/capability/:key/resolve', resolverReadLimiter, async (req, res, next) => {
  try {
    const key = String(req.params.key || '').trim();
    if (!key) throw new BadRequestError('capability key is required');
    res.json(await resolveCapability(key));
  } catch (err) { next(err); }
});

// GET /api/roles/{name}/credential — bitemporal credential check (V175 shape)
nodeRequirementsRouter.get('/credential/:role', resolverReadLimiter, async (req, res, next) => {
  try {
    const name = String(req.params.role || '').trim();
    if (!name) throw new BadRequestError('role name is required');
    res.json(await verifyRoleCredential(name));
  } catch (err) { next(err); }
});

export { SATISFACTION_STATES };
