import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { NotFoundError, BadRequestError } from '../errors.js';
import {
  resolveNodeRequirements, resolveContextBundle, resolveCapability,
  verifyRoleCredential, SATISFACTION_STATES,
} from '../capability-resolver.js';

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
