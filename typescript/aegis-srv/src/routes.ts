import { Request, Response, Router } from 'express';
import { query, withTransaction } from './db';
import { checkModel, MCModel } from './model-checker';
import { runTlc, stageSpec, TlcRunOpts, TlcResult } from './tlc-runner';
import { digestJson, mapCheckerOutcome } from './phase-a';
import { buildWindCompilationPlan } from './wind-compiler';

const router = Router();

// ── Helpers ──────────────────────────────────────────────────────
const isUuid = (v: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);

const error = (res: Response, status: number, message: string) => {
  res.status(status).json({ error: message, message });
};

/** Assert the registry exists (or 404). Returns its id. */
async function requireRegistry(id: string | string[], res: Response): Promise<string | null> {
  const idStr = String(id);
  if (!isUuid(idStr)) { error(res, 400, 'invalid registry id'); return null; }
  const { rows } = await query('SELECT id FROM aegis.registry WHERE id = $1', [idStr]);
  if (rows.length === 0) { error(res, 404, 'registry not found'); return null; }
  return rows[0].id as string;
}

/** Assert a child row exists under a registry (or 404). */
async function requireChild(
  table: string, registryId: string, childId: string | string[], res: Response,
): Promise<boolean> {
  const childIdStr = String(childId);
  if (!isUuid(childIdStr)) { error(res, 400, 'invalid child id'); return false; }
  const { rows } = await query(
    `SELECT id FROM aegis.${table} WHERE id = $1 AND registry_id = $2`, [childIdStr, registryId],
  );
  if (rows.length === 0) { error(res, 404, `${table} not found`); return false; }
  return true;
}

/** Pick only the allowed columns out of a request body. */
function pick(body: any, allowed: string[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const k of allowed) {
    if (body[k] !== undefined) out[k] = body[k];
  }
  return out;
}

/** Columns stored as JSONB — JS objects/arrays/scalars must be JSON.stringify'd for pg. */
const JSONB_COLS = new Set([
  'metadata', 'value', 'initial_value', 'domain', 'variable_assignments',
  'action', 'default_value', 'trace', 'errors', 'warnings', 'suggestions', 'context',
]);

/** Coerce jsonb values to valid JSON text (pg accepts objects, but arrays/bare scalars need stringify). */
function jsonbCoerce(body: Record<string, any>, cols: string[]): any[] {
  return cols.map((c) => {
    const v = body[c];
    if (JSONB_COLS.has(c) && v !== null && v !== undefined) {
      return JSON.stringify(v);
    }
    return v;
  });
}

/** Handle DB unique-violation / check violations into 409/400. */
function pgError(res: Response, err: any) {
  const code = err?.code;
  if (code === '23505') return error(res, 409, `duplicate key: ${err?.detail || err?.constraint || 'conflict'}`);
  if (code === '23503') return error(res, 400, `foreign key violation: ${err?.detail || 'referenced row missing'}`);
  if (code === '23514') return error(res, 400, `check constraint violation: ${err?.detail || err?.constraint || ''}`);
  if (code === '22P02') return error(res, 400, `invalid value: ${err?.message || ''}`);
  console.error('[aegis-srv] DB error:', err?.message);
  return error(res, 500, 'internal server error');
}

interface RegistryRevisionRow {
  id: string;
  registry_id: string;
  revision_number: number;
  source: string;
  source_digest: string;
  model: Record<string, any>;
  model_digest: string;
}

async function createRegistryRevision(registryId: string, createdBy: string | null): Promise<RegistryRevisionRow> {
  const created = await query(
    'SELECT aegis.create_registry_revision($1, $2) AS revision_id',
    [registryId, createdBy],
  );
  const revisionId = created.rows[0]?.revision_id;
  const revision = await query(
    'SELECT * FROM aegis.registry_revision WHERE id = $1', [revisionId],
  );
  return revision.rows[0] as RegistryRevisionRow;
}

async function getRegistryRevision(
  registryId: string,
  requestedRevisionId?: unknown,
  createdBy: string | null = null,
): Promise<RegistryRevisionRow> {
  if (requestedRevisionId !== undefined && requestedRevisionId !== null) {
    const revisionId = String(requestedRevisionId);
    if (!isUuid(revisionId)) throw Object.assign(new Error('invalid revision id'), { code: '22P02' });
    const revision = await query(
      'SELECT * FROM aegis.registry_revision WHERE id = $1 AND registry_id = $2',
      [revisionId, registryId],
    );
    if (revision.rows.length === 0) throw Object.assign(new Error('revision not found'), { code: 'ENOENT' });
    return revision.rows[0] as RegistryRevisionRow;
  }
  return createRegistryRevision(registryId, createdBy);
}

function modelFromRevision(snapshot: Record<string, any>): MCModel {
  return {
    states: snapshot.states || [],
    transitions: (snapshot.transitions || []).map((t: any) => ({
      ...t,
      from_state_id: t.from_state_id || null,
      to_state_id: t.to_state_id || null,
      guard_expression: t.guard_expression || null,
    })),
    invariants: snapshot.invariants || [],
    properties: snapshot.properties || [],
    temporal_properties: snapshot.temporal_properties || [],
    variables: (snapshot.variables || []).map((v: any) => v.name),
    constants: (snapshot.constants || []).map((c: any) => c.name),
  };
}

// ══════════════════════════════════════════════════════════════════
// Registry-scoped child handlers (shared CRUD logic per table).
// Each route is declared with a literal path so the TypeSpec↔source
// reconciler can statically prove coverage (contract-first convention).
// ══════════════════════════════════════════════════════════════════
interface ChildHandlers {
  list: (req: Request, res: Response) => Promise<void>;
  create: (req: Request, res: Response) => Promise<void>;
  get: (req: Request, res: Response) => Promise<void>;
  update: (req: Request, res: Response) => Promise<void>;
  remove: (req: Request, res: Response) => Promise<void>;
}

function childHandlers(table: string, createCols: string[], updateCols: string[]): ChildHandlers {
  return {
    async list(req, res) {
      try {
        const registryId = await requireRegistry(req.params.id, res);
        if (!registryId) return;
        const { rows } = await query(
          `SELECT * FROM aegis.${table} WHERE registry_id = $1 ORDER BY created_at`, [registryId],
        );
        res.json({ items: rows });
      } catch (e) { pgError(res, e); }
    },
    async create(req, res) {
      try {
        const registryId = await requireRegistry(req.params.id, res);
        if (!registryId) return;
        const body = pick(req.body, createCols);
        const cols = Object.keys(body);
        if (cols.length === 0) { error(res, 400, 'no fields provided'); return; }
        const values = cols.map((_, i) => `$${i + 2}`);
        const { rows } = await query(
          `INSERT INTO aegis.${table} (registry_id, ${cols.join(', ')})
           VALUES ($1, ${values.join(', ')})
           RETURNING *`,
          [registryId, ...jsonbCoerce(body, cols)],
        );
        res.status(201).json(rows[0]);
      } catch (e) { pgError(res, e); }
    },
    async get(req, res) {
      try {
        const registryId = await requireRegistry(req.params.id, res);
        if (!registryId) return;
        if (!(await requireChild(table, registryId, req.params.cid, res))) return;
        const { rows } = await query(
          `SELECT * FROM aegis.${table} WHERE id = $1 AND registry_id = $2`,
          [String(req.params.cid), registryId],
        );
        res.json(rows[0]);
      } catch (e) { pgError(res, e); }
    },
    async update(req, res) {
      try {
        const registryId = await requireRegistry(req.params.id, res);
        if (!registryId) return;
        if (!(await requireChild(table, registryId, req.params.cid, res))) return;
        const body = pick(req.body, updateCols);
        const cols = Object.keys(body);
        if (cols.length === 0) { error(res, 400, 'no fields provided'); return; }
        const set = cols.map((c, i) => `${c} = $${i + 3}`);
        const { rows } = await query(
          `UPDATE aegis.${table}
           SET ${set.join(', ')}
           WHERE id = $1 AND registry_id = $2
           RETURNING *`,
          [String(req.params.cid), registryId, ...jsonbCoerce(body, cols)],
        );
        res.json(rows[0]);
      } catch (e) { pgError(res, e); }
    },
    async remove(req, res) {
      try {
        const registryId = await requireRegistry(req.params.id, res);
        if (!registryId) return;
        if (!(await requireChild(table, registryId, req.params.cid, res))) return;
        await query(
          `DELETE FROM aegis.${table} WHERE id = $1 AND registry_id = $2`,
          [String(req.params.cid), registryId],
        );
        res.json({ deleted: String(req.params.cid) });
      } catch (e) { pgError(res, e); }
    },
  };
}

// ══════════════════════════════════════════════════════════════════
// Health is served by index.ts at /health (not under /api).
// ══════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════
// Registries (root CRUD)
// ══════════════════════════════════════════════════════════════════
const REGISTRY_COLS = [
  'name', 'description', 'version', 'tla_plus_source', 'tla_plus_module',
  'metadata', 'tags', 'is_active', 'expires_at', 'main_concept_id',
];

router.get('/registries', async (_req, res) => {
  try {
    const { rows } = await query('SELECT * FROM aegis.registry ORDER BY created_at');
    res.json({ items: rows });
  } catch (e) { pgError(res, e); }
});

router.get('/registries/name/:name', async (req, res) => {
  try {
    const { rows } = await query('SELECT * FROM aegis.registry WHERE name = $1 AND is_active = true', [req.params.name]);
    if (rows.length === 0) { error(res, 404, 'registry not found'); return; }
    res.json(rows[0]);
  } catch (e) { pgError(res, e); }
});

router.post('/registries', async (req, res) => {
  try {
    const body = pick(req.body, REGISTRY_COLS);
    const cols = Object.keys(body);
    if (cols.length === 0) { error(res, 400, 'no fields provided'); return; }
    const values = cols.map((_, i) => `$${i + 1}`);
    const { rows } = await query(
      `INSERT INTO aegis.registry (${cols.join(', ')})
       VALUES (${values.join(', ')})
       RETURNING *`,
      jsonbCoerce(body, cols),
    );
    res.status(201).json(rows[0]);
  } catch (e) { pgError(res, e); }
});

router.get('/registries/:id', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const { rows } = await query('SELECT * FROM aegis.registry WHERE id = $1', [registryId]);
    res.json(rows[0]);
  } catch (e) { pgError(res, e); }
});

router.patch('/registries/:id', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const body = pick(req.body, REGISTRY_COLS);
    const cols = Object.keys(body);
    if (cols.length === 0) { error(res, 400, 'no fields provided'); return; }
    const set = cols.map((c, i) => `${c} = $${i + 2}`);
    const { rows } = await query(
      `UPDATE aegis.registry SET ${set.join(', ')} WHERE id = $1 RETURNING *`,
      [registryId, ...jsonbCoerce(body, cols)],
    );
    res.json(rows[0]);
  } catch (e) { pgError(res, e); }
});

// Soft delete: is_active = false (schema partial unique index on active name)
router.delete('/registries/:id', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    await query('UPDATE aegis.registry SET is_active = false WHERE id = $1', [registryId]);
    res.json({ deleted: String(req.params.id) });
  } catch (e) { pgError(res, e); }
});

// ══════════════════════════════════════════════════════════════════
// Immutable registry revisions (Phase A)
// ══════════════════════════════════════════════════════════════════
router.get('/registries/:id/revisions', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const { rows } = await query(
      'SELECT * FROM aegis.registry_revision WHERE registry_id = $1 ORDER BY revision_number DESC',
      [registryId],
    );
    res.json({ items: rows });
  } catch (e) { pgError(res, e); }
});

router.post('/registries/:id/revisions', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const revision = await createRegistryRevision(registryId, req.body?.created_by || null);
    res.status(201).json(revision);
  } catch (e) { pgError(res, e); }
});

router.get('/registries/:id/revisions/:rid', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const revision = await getRegistryRevision(registryId, req.params.rid);
    res.json(revision);
  } catch (e: any) {
    if (e?.code === 'ENOENT') { error(res, 404, 'revision not found'); return; }
    pgError(res, e);
  }
});

// ══════════════════════════════════════════════════════════════════
// Action endpoints
// ══════════════════════════════════════════════════════════════════
router.post('/registries/:id/validate', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const result = await withTransaction(async (client) => {
      const { rows: reg } = await client.query('SELECT * FROM aegis.registry WHERE id = $1', [registryId]);
      const registry = reg[0];
      const errors: any[] = [];
      const warnings: any[] = [];
      const suggestions: any[] = [];
      if (!registry.name) errors.push({ code: 'missing_name', message: 'registry has no name' });
      if (!registry.version) warnings.push({ code: 'missing_version', message: 'registry has no version, defaulting to 1.0.0' });
      const validated_by = req.body?.validated_by || null;
      const { rows } = await client.query(
        `INSERT INTO aegis.validation_result (registry_id, is_valid, errors, warnings, suggestions, validated_by)
         VALUES ($1, $2, $3, $4, $5, $6) RETURNING *`,
        [registryId, errors.length === 0, JSON.stringify(errors), JSON.stringify(warnings), JSON.stringify(suggestions), validated_by],
      );
      return rows[0];
    });
    res.status(201).json(result);
  } catch (e) { pgError(res, e); }
});

// Model-check: run the authoritative model check.
//   - If the registry has tla_plus_source, run real TLC (tla2tools.jar) over
//     the TLA+ module with invariants/properties as cfg checks.
//   - Otherwise fall back to the deterministic structural state-space checker
//     (model-checker.ts) over the structured aegis graph.
// Persists the result to aegis.model_check_result. TLC is failure-isolated:
// a checker crash/timeout yields an `error` status, never a failed request.
// Optional request body: { property_id, checked_by }.
router.post('/registries/:id/model-check', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const started = Date.now();
    const body = pick(req.body || {}, [
      'property_id', 'checked_by', 'revision_id', 'input_snapshot', 'input_snapshot_digest', 'checker_config',
    ]);
    const revision = await getRegistryRevision(registryId, body.revision_id, body.checked_by || null);
    const snapshot = body.input_snapshot === undefined ? {} : body.input_snapshot;
    const inputSnapshotDigest = digestJson(snapshot);
    if (body.input_snapshot_digest !== undefined && body.input_snapshot_digest !== inputSnapshotDigest) {
      error(res, 400, 'input_snapshot_digest does not match input_snapshot');
      return;
    }

    const model = modelFromRevision(revision.model);
    const checkerConfig = body.checker_config || {
      init: 'Init',
      next: 'Next',
      invariants: model.invariants.map((i: any) => i.name),
      properties: model.properties.map((p: any) => p.name),
    };
    const tlaSource: string = revision.source || '';
    const effectiveCheckerConfig = {
      ...checkerConfig,
      init: checkerConfig.init || 'Init',
      next: checkerConfig.next || 'Next',
      invariants: model.invariants.map((i: any) => i.name),
      properties: model.properties.map((p: any) => p.name),
    };
    const checkerConfigDigest = digestJson(effectiveCheckerConfig);
    const hasTlaSource = Boolean(tlaSource.trim());
    const engine: 'tlc' | 'structural' = hasTlaSource ? 'tlc' : 'structural';
    const engineVersion = hasTlaSource ? 'tla2tools.jar' : 'aegis-structural-checker-v1';
    let trace: any = null;
    let checkedProperties: string[] = [];
    let checkerStatus: 'success' | 'failure' | 'error';
    let reason: string;

    if (hasTlaSource) {
      const tlcOpts: TlcRunOpts = effectiveCheckerConfig as TlcRunOpts;
      const staged = stageSpec(tlaSource, tlcOpts);
      let result: TlcResult;
      try { result = await runTlc(staged.specDir, staged.moduleName, tlcOpts); }
      finally { staged.cleanup(); }
      checkerStatus = result.status;
      trace = result.trace ? { engine, steps: result.trace, violated: result.violated } : null;
      checkedProperties = result.verdicts.map((v) => `${v.kind}:${v.name}=${v.result}: ${v.detail}`);
      if (result.violated) checkedProperties.push(`violated:${result.violated}`);
      reason = [...result.errors, ...result.warnings, result.violated || ''].filter(Boolean).join('; ') ||
        (result.status === 'success' ? 'TLC completed without a safety violation' : 'TLC did not establish a verified result');
    } else {
      const report = checkModel(model);
      checkerStatus = report.status === 'success' ? 'success' : 'failure';
      checkedProperties = report.verdicts.map((v) => `${v.kind}:${v.name}=${v.result}${v.type ? `(${v.type})` : ''}: ${v.detail}`);
      if (report.unreachableStates.length > 0) checkedProperties.push(`unreachable:${report.unreachableStates.join(',')}`);
      trace = report.deadlockTrace ? { engine, deadlock: report.deadlockTrace, errors: report.errors } : null;
      reason = [...report.errors, ...report.warnings].join('; ') || 'Structural analysis is not a formal proof';
    }

    const outcome = mapCheckerOutcome(engine, checkerStatus, reason);
    checkedProperties.push(`engine:${engine}`);
    checkedProperties.push(`truthful_status:${outcome.status}`);
    checkedProperties.push(`liveness_status:${outcome.liveness_status}`);
    const resultMaterial = {
      registry_revision_id: revision.id,
      engine,
      engine_version: engineVersion,
      checker_config_digest: checkerConfigDigest,
      source_digest: revision.source_digest,
      model_digest: revision.model_digest,
      input_snapshot_digest: inputSnapshotDigest,
      status: outcome.status,
      safety_status: outcome.safety_status,
      liveness_status: outcome.liveness_status,
      trace,
      checked_properties: checkedProperties,
    };
    const resultDigest = digestJson(resultMaterial);
    const checkedBy = body.checked_by && isUuid(String(body.checked_by)) ? String(body.checked_by) : null;

    const { rows } = await query(
      `INSERT INTO aegis.model_check_result
         (registry_id, registry_revision_id, property_id, status, trace, checked_properties,
          execution_time_ms, checked_by, engine, engine_version, checker_config_digest,
          source_digest, model_digest, input_snapshot_digest, result_digest,
          safety_status, liveness_status, authority_level, reason)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
       RETURNING *`,
      [registryId, revision.id, body.property_id || null, outcome.status,
       trace ? JSON.stringify(trace) : null, checkedProperties, Date.now() - started, checkedBy,
       engine, engineVersion, checkerConfigDigest, revision.source_digest, revision.model_digest,
       inputSnapshotDigest, resultDigest, outcome.safety_status, outcome.liveness_status,
       outcome.authority_level, outcome.reason],
    );
    res.status(201).json(rows[0]);
  } catch (e: any) {
    if (e?.code === 'ENOENT') { error(res, 404, 'revision not found'); return; }
    pgError(res, e);
  }
});

/** Load the structured aegis model (states/transitions/invariants/properties/temporals + names) for a registry. */
async function loadModel(registryId: string): Promise<MCModel> {
  const [states, transitions, invariants, properties, temporalProps] = await Promise.all([
    query('SELECT id, name, is_initial, is_terminal, variable_assignments FROM aegis.state WHERE registry_id = $1', [registryId]),
    query('SELECT id, name, from_state_id, to_state_id, guard_expression, action, weak_fairness, strong_fairness, priority FROM aegis.transition WHERE registry_id = $1', [registryId]),
    query('SELECT id, name, expression, is_type_invariant FROM aegis.invariant WHERE registry_id = $1', [registryId]),
    query('SELECT id, name, type, expression FROM aegis.property WHERE registry_id = $1', [registryId]),
    query('SELECT id, name, operator, expression FROM aegis.temporal_property WHERE registry_id = $1', [registryId]),
  ]);
  const vars = await query('SELECT name FROM aegis.variable WHERE registry_id = $1', [registryId]);
  const consts = await query('SELECT name FROM aegis.constant WHERE registry_id = $1', [registryId]);

  return {
    states: states.rows,
    transitions: transitions.rows.map((t) => ({
      ...t,
      from_state_id: t.from_state_id || null,
      to_state_id: t.to_state_id || null,
      guard_expression: t.guard_expression || null,
    })),
    invariants: invariants.rows,
    properties: properties.rows,
    temporal_properties: temporalProps.rows,
    variables: vars.rows.map((r) => r.name),
    constants: consts.rows.map((r) => r.name),
  };
}

router.get('/registries/:id/validation-results', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const { rows } = await query(
      'SELECT * FROM aegis.validation_result WHERE registry_id = $1 ORDER BY validated_at DESC', [registryId],
    );
    res.json({ items: rows });
  } catch (e) { pgError(res, e); }
});router.get('/registries/:id/model-check-results', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const { rows } = await query(
      'SELECT * FROM aegis.model_check_result WHERE registry_id = $1 ORDER BY checked_at DESC',
      [registryId],
    );
    res.json({ items: rows });
  } catch (e) { pgError(res, e); }
});

// ══════════════════════════════════════════════════════════════════
// Aegis revision -> Wind compilation
// ══════════════════════════════════════════════════════════════════
router.get('/registries/:id/wind-compilations', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    const { rows } = await query(
      `SELECT * FROM aegis.wind_compilation
        WHERE registry_id = $1
        ORDER BY compiled_at DESC`, [registryId],
    );
    res.json({ items: rows });
  } catch (e) { pgError(res, e); }
});

router.get('/registries/:id/wind-compilations/:cid', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;
    if (!isUuid(String(req.params.cid))) { error(res, 400, 'invalid compilation id'); return; }
    const { rows } = await query(
      `SELECT * FROM aegis.wind_compilation
        WHERE id = $1 AND registry_id = $2`, [String(req.params.cid), registryId],
    );
    if (rows.length === 0) { error(res, 404, 'Wind compilation not found'); return; }
    res.json(rows[0]);
  } catch (e) { pgError(res, e); }
});

router.post('/registries/:id/wind-compilations', async (req, res) => {
  try {
    const registryId = await requireRegistry(req.params.id, res);
    if (!registryId) return;

    const body = req.body || {};
    const workflowId = String(body.wind_workflow_id || '');
    if (!isUuid(workflowId)) { error(res, 400, 'wind_workflow_id is required and must be a UUID'); return; }
    const compilerVersion = typeof body.compiler_version === 'string' && body.compiler_version.trim()
      ? body.compiler_version.trim() : 'aegis-wind-compiler-v1';
    const compilerConfig = body.compiler_config === undefined ? {} : body.compiler_config;
    const compilerConfigDigest = digestJson(compilerConfig);
    const compiledBy = body.compiled_by && isUuid(String(body.compiled_by)) ? String(body.compiled_by) : null;

    const result = await withTransaction(async (client) => {
      const revisionId = body.revision_id === undefined || body.revision_id === null
        ? (await client.query('SELECT aegis.create_registry_revision($1, $2) AS revision_id', [registryId, compiledBy])).rows[0].revision_id
        : String(body.revision_id);
      if (!isUuid(revisionId)) throw Object.assign(new Error('invalid revision id'), { code: '22P02' });

      const revisionResult = await client.query(
        'SELECT id, registry_id, model, source_digest, model_digest FROM aegis.registry_revision WHERE id = $1 AND registry_id = $2',
        [revisionId, registryId],
      );
      if (revisionResult.rows.length === 0) throw Object.assign(new Error('revision not found'), { code: 'ENOENT' });
      const revision = revisionResult.rows[0];

      const workflowResult = await client.query('SELECT id FROM wind.workflows WHERE id = $1', [workflowId]);
      if (workflowResult.rows.length === 0) throw Object.assign(new Error('Wind workflow not found'), { code: 'WIND_NOT_FOUND' });

      const [taskMappings, outcomeMappings, windTasks, windOutcomes] = await Promise.all([
        client.query(
          `SELECT state_id, wind_task_id, is_check_only
             FROM aegis.wind_task_mapping
            WHERE registry_id = $1 AND registry_revision_id = $2
            ORDER BY state_id`, [registryId, revisionId],
        ),
        client.query(
          `SELECT transition_id, wind_task_id, wind_outcome_id
             FROM aegis.wind_outcome_mapping
            WHERE registry_id = $1 AND registry_revision_id = $2
            ORDER BY transition_id`, [registryId, revisionId],
        ),
        client.query('SELECT id, name, tackle_task_id FROM wind.tasks WHERE id IN (SELECT wind_task_id FROM aegis.wind_task_mapping WHERE registry_id = $1 AND registry_revision_id = $2 UNION SELECT wind_task_id FROM aegis.wind_outcome_mapping WHERE registry_id = $1 AND registry_revision_id = $2)', [registryId, revisionId]),
        client.query('SELECT id, task_id, code FROM wind.task_outcomes WHERE id IN (SELECT wind_outcome_id FROM aegis.wind_outcome_mapping WHERE registry_id = $1 AND registry_revision_id = $2)', [registryId, revisionId]),
      ]);

      const plan = buildWindCompilationPlan(
        revisionId, revision.model, taskMappings.rows, outcomeMappings.rows,
        windTasks.rows, windOutcomes.rows,
      );
      if (plan.errors.length > 0) {
        const validationError: any = new Error(`Wind compilation refused: ${plan.errors.join('; ')}`);
        validationError.code = 'WIND_COMPILATION_INVALID';
        validationError.details = plan.errors;
        throw validationError;
      }

      // Serialize version allocation per workflow; MAX()+1 is otherwise
      // vulnerable to two concurrent compilers selecting the same number.
      await client.query('SELECT pg_advisory_xact_lock(hashtextextended($1, 150))', [workflowId]);
      const versionResult = await client.query(
        `INSERT INTO wind.workflow_versions (workflow_id, version_number)
         SELECT $1, COALESCE(MAX(version_number), 0) + 1
           FROM wind.workflow_versions
         WHERE workflow_id = $1
         RETURNING id, workflow_id, version_number`,
        [workflowId],
      );
      const version = versionResult.rows[0];
      const nodeIds = new Map<string, string>();
      const nodeRows: any[] = [];
      for (const node of plan.nodes) {
        const inserted = await client.query(
          `INSERT INTO wind.workflow_nodes
             (workflow_version_id, task_id, name, is_entrypoint, is_terminal)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id, workflow_version_id, task_id, name, is_entrypoint, is_terminal`,
          [version.id, node.wind_task_id, node.name, node.is_entrypoint, node.is_terminal],
        );
        nodeIds.set(node.state_id, inserted.rows[0].id);
        nodeRows.push({ ...inserted.rows[0], state_id: node.state_id });
      }

      const edgeRows: any[] = [];
      for (const edge of plan.edges) {
        const inserted = await client.query(
          `INSERT INTO wind.workflow_edges
             (workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id, workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id`,
          [version.id, nodeIds.get(edge.from_node_state_id), edge.from_task_id, edge.outcome_id, nodeIds.get(edge.to_node_state_id)],
        );
        edgeRows.push({ ...inserted.rows[0], transition_id: edge.transition_id });
      }

      const compilationResult = await client.query(
        `INSERT INTO aegis.wind_compilation
           (registry_id, registry_revision_id, wind_workflow_id, wind_workflow_version_id,
            wind_workflow_version_number, source_digest, model_digest, wind_graph_digest,
            compiler_version, compiler_config_digest, compiled_by, status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'succeeded')
         RETURNING *`,
        [registryId, revisionId, workflowId, version.id, version.version_number,
          revision.source_digest, revision.model_digest, plan.graph_digest,
          compilerVersion, compilerConfigDigest, compiledBy],
      );
      const compilation = compilationResult.rows[0];

      const compiledNodes: any[] = [];
      for (const node of nodeRows) {
        const lineage = await client.query(
          `INSERT INTO aegis.compiled_node
             (compilation_id, registry_id, registry_revision_id, state_id,
              wind_workflow_version_id, wind_node_id)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *`,
          [compilation.id, registryId, revisionId, node.state_id, version.id, node.id],
        );
        compiledNodes.push(lineage.rows[0]);
      }
      const compiledEdges: any[] = [];
      for (const edge of edgeRows) {
        const lineage = await client.query(
          `INSERT INTO aegis.compiled_edge
             (compilation_id, registry_id, registry_revision_id, transition_id,
              wind_workflow_version_id, wind_edge_id)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING *`,
          [compilation.id, registryId, revisionId, edge.transition_id, version.id, edge.id],
        );
        compiledEdges.push(lineage.rows[0]);
      }

      return { compilation, workflow_version: version, nodes: compiledNodes, edges: compiledEdges };
    });

    res.status(201).json(result);
  } catch (e: any) {
    if (e?.code === 'ENOENT') { error(res, 404, 'revision not found'); return; }
    if (e?.code === 'WIND_NOT_FOUND') { error(res, 404, 'Wind workflow not found'); return; }
    if (e?.code === 'WIND_COMPILATION_INVALID') {
      res.status(422).json({ error: 'wind compilation refused', message: e.message, details: e.details });
      return;
    }
    pgError(res, e);
  }
});

// ══════════════════════════════════════════════════════════════════
// Child resources (constants, variables, states, transitions, invariants,
// properties, temporal-properties, mappings, execution-log)
// ══════════════════════════════════════════════════════════════════
const C = {
  constants: childHandlers('constant', ['name', 'type', 'value', 'description', 'constraints'], ['name', 'type', 'value', 'description', 'constraints']),
  variables: childHandlers('variable', ['name', 'type', 'initial_value', 'domain', 'description', 'constraints', 'attribute_id'], ['name', 'type', 'initial_value', 'domain', 'description', 'constraints', 'attribute_id']),
  states: childHandlers('state', ['name', 'description', 'variable_assignments', 'constraints', 'is_initial', 'is_terminal', 'concept_id', 'attribute_value_id'], ['name', 'description', 'variable_assignments', 'constraints', 'is_initial', 'is_terminal', 'concept_id', 'attribute_value_id']),
  transitions: childHandlers('transition', ['name', 'description', 'guard_expression', 'action', 'weak_fairness', 'strong_fairness', 'temporal_conditions', 'priority', 'from_state_id', 'to_state_id', 'guard_rule_id', 'transition_rule_id', 'state_transition_id'], ['name', 'description', 'guard_expression', 'action', 'weak_fairness', 'strong_fairness', 'temporal_conditions', 'priority', 'from_state_id', 'to_state_id', 'guard_rule_id', 'transition_rule_id', 'state_transition_id']),
  invariants: childHandlers('invariant', ['name', 'expression', 'description', 'is_type_invariant', 'rule_id', 'expression_id'], ['name', 'expression', 'description', 'is_type_invariant', 'rule_id', 'expression_id']),
  properties: childHandlers('property', ['name', 'type', 'expression', 'description', 'is_verified', 'verified_at', 'verified_by'], ['name', 'type', 'expression', 'description', 'is_verified', 'verified_at', 'verified_by']),
  temporalProps: childHandlers('temporal_property', ['name', 'operator', 'expression', 'description'], ['name', 'operator', 'expression', 'description']),
  conceptMappings: childHandlers('concept_mapping', ['tla_name', 'concept_id', 'mapping_type', 'mapping_expression', 'cardinality'], ['tla_name', 'concept_id', 'mapping_type', 'mapping_expression', 'cardinality']),
  attributeMappings: childHandlers('attribute_mapping', ['tla_variable', 'attribute_id', 'conversion_function', 'default_value'], ['tla_variable', 'attribute_id', 'conversion_function', 'default_value']),
  relationshipMappings: childHandlers('relationship_mapping', ['tla_relationship', 'relationship_id', 'mapping_type', 'constraints'], ['tla_relationship', 'relationship_id', 'mapping_type', 'constraints']),
  executionLog: childHandlers('execution_log', ['entity_id', 'from_state_id', 'to_state_id', 'transition_id', 'trigger_event', 'trigger_user', 'context'], ['entity_id', 'from_state_id', 'to_state_id', 'transition_id', 'trigger_event', 'trigger_user', 'context']),
};

// constants
router.get('/registries/:id/constants', C.constants.list);
router.post('/registries/:id/constants', C.constants.create);
router.get('/registries/:id/constants/:cid', C.constants.get);
router.patch('/registries/:id/constants/:cid', C.constants.update);
router.delete('/registries/:id/constants/:cid', C.constants.remove);

// variables
router.get('/registries/:id/variables', C.variables.list);
router.post('/registries/:id/variables', C.variables.create);
router.get('/registries/:id/variables/:cid', C.variables.get);
router.patch('/registries/:id/variables/:cid', C.variables.update);
router.delete('/registries/:id/variables/:cid', C.variables.remove);

// states
router.get('/registries/:id/states', C.states.list);
router.post('/registries/:id/states', C.states.create);
router.get('/registries/:id/states/:cid', C.states.get);
router.patch('/registries/:id/states/:cid', C.states.update);
router.delete('/registries/:id/states/:cid', C.states.remove);

// transitions
router.get('/registries/:id/transitions', C.transitions.list);
router.post('/registries/:id/transitions', C.transitions.create);
router.get('/registries/:id/transitions/:cid', C.transitions.get);
router.patch('/registries/:id/transitions/:cid', C.transitions.update);
router.delete('/registries/:id/transitions/:cid', C.transitions.remove);

// invariants
router.get('/registries/:id/invariants', C.invariants.list);
router.post('/registries/:id/invariants', C.invariants.create);
router.get('/registries/:id/invariants/:cid', C.invariants.get);
router.patch('/registries/:id/invariants/:cid', C.invariants.update);
router.delete('/registries/:id/invariants/:cid', C.invariants.remove);

// properties
router.get('/registries/:id/properties', C.properties.list);
router.post('/registries/:id/properties', C.properties.create);
router.get('/registries/:id/properties/:cid', C.properties.get);
router.patch('/registries/:id/properties/:cid', C.properties.update);
router.delete('/registries/:id/properties/:cid', C.properties.remove);

// temporal-properties
router.get('/registries/:id/temporal-properties', C.temporalProps.list);
router.post('/registries/:id/temporal-properties', C.temporalProps.create);
router.get('/registries/:id/temporal-properties/:cid', C.temporalProps.get);
router.patch('/registries/:id/temporal-properties/:cid', C.temporalProps.update);
router.delete('/registries/:id/temporal-properties/:cid', C.temporalProps.remove);

// concept-mappings
router.get('/registries/:id/concept-mappings', C.conceptMappings.list);
router.post('/registries/:id/concept-mappings', C.conceptMappings.create);
router.get('/registries/:id/concept-mappings/:cid', C.conceptMappings.get);
router.patch('/registries/:id/concept-mappings/:cid', C.conceptMappings.update);
router.delete('/registries/:id/concept-mappings/:cid', C.conceptMappings.remove);

// attribute-mappings
router.get('/registries/:id/attribute-mappings', C.attributeMappings.list);
router.post('/registries/:id/attribute-mappings', C.attributeMappings.create);
router.get('/registries/:id/attribute-mappings/:cid', C.attributeMappings.get);
router.patch('/registries/:id/attribute-mappings/:cid', C.attributeMappings.update);
router.delete('/registries/:id/attribute-mappings/:cid', C.attributeMappings.remove);

// relationship-mappings
router.get('/registries/:id/relationship-mappings', C.relationshipMappings.list);
router.post('/registries/:id/relationship-mappings', C.relationshipMappings.create);
router.get('/registries/:id/relationship-mappings/:cid', C.relationshipMappings.get);
router.patch('/registries/:id/relationship-mappings/:cid', C.relationshipMappings.update);
router.delete('/registries/:id/relationship-mappings/:cid', C.relationshipMappings.remove);

// execution-log
router.get('/registries/:id/execution-log', C.executionLog.list);
router.post('/registries/:id/execution-log', C.executionLog.create);
router.get('/registries/:id/execution-log/:cid', C.executionLog.get);
router.patch('/registries/:id/execution-log/:cid', C.executionLog.update);
router.delete('/registries/:id/execution-log/:cid', C.executionLog.remove);

export default router;