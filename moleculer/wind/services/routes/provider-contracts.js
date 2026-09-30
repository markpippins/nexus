import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { pool, query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';
import { validateProviderInvocation } from '../provider-contract.js';
import { resolveEnvironmentReferenceMetadata } from '../provider-dispatch.js';

export const providerContractsRouter = Router();

const registryWriteLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 30,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: { error: 'provider registry write rate limit exceeded' },
});

function requiredString(body, name) {
  if (typeof body?.[name] !== 'string' || body[name].trim() === '') {
    throw new BadRequestError(`${name} is required`);
  }
  return body[name].trim();
}

function role(body, name) {
  return requiredString(body, name).toLowerCase();
}

function dualControl(body) {
  const approvalRecordRef = requiredString(body, 'approval_record_ref');
  const approvedByRole = role(body, 'approved_by_role');
  const confirmedByRole = role(body, 'confirmed_by_role');
  if (approvedByRole === confirmedByRole) {
    throw new BadRequestError('approved_by_role and confirmed_by_role must be different roles');
  }
  return { approvalRecordRef, approvedByRole, confirmedByRole };
}

function registrationControl(body) {
  const approvalRecordRef = requiredString(body, 'approval_record_ref');
  const requestedByRole = role(body, 'requested_by_role');
  const acknowledgedByRole = role(body, 'acknowledged_by_role');
  if (requestedByRole !== 'engineer') throw new BadRequestError('new adapter registration must be requested by engineer');
  if (!new Set(['architect', 'operator']).has(acknowledgedByRole)) {
    throw new BadRequestError('acknowledged_by_role must be architect or operator');
  }
  return { approvalRecordRef, requestedByRole, acknowledgedByRole };
}

function contractInput(body) {
  const providerContract = body?.provider_contract;
  const invocationContract = body?.invocation_contract;
  const failurePolicy = body?.failure_policy || {
    unavailable_outcome: 'UNAVAILABLE',
    timeout_outcome: 'FAILED',
    malformed_result_outcome: 'INVALID',
    stale_outcome: 'STALE',
  };
  try {
    return validateProviderInvocation({
      provider_contract: providerContract,
      invocation_contract: invocationContract,
      failure_policy: failurePolicy,
    });
  } catch (error) {
    throw new BadRequestError(`invalid provider contract: ${error.message}`);
  }
}

function envRef(value, name, required = false) {
  if (value === undefined || value === null || value === '') {
    if (required) throw new BadRequestError(`${name} is required`);
    return null;
  }
  if (typeof value !== 'string' || !/^[A-Z][A-Z0-9_]{0,127}$/.test(value)) {
    throw new BadRequestError(`${name} must be an environment variable reference`);
  }
  return value;
}

function registryFields(body) {
  const validated = contractInput(body);
  const provider = validated.provider_contract;
  const invocation = validated.invocation_contract;
  const credentialEnvRef = envRef(body.credential_env_ref, 'credential_env_ref');
  const endpointEnvRef = envRef(body.endpoint_env_ref, 'endpoint_env_ref', invocation.mode === 'HTTP');
  return {
    adapterId: provider.adapter_id,
    adapterVersion: provider.adapter_version,
    providerId: provider.provider_id,
    providerVersion: provider.provider_version,
    invocationMode: invocation.mode,
    inputSchemaDigest: provider.input_schema_digest,
    outputSchemaDigest: provider.output_schema_digest,
    credentialEnvRef,
    endpointEnvRef,
  };
}

async function latestRevision(adapterId, client = null) {
  const runner = client || { query: (text, params) => query(text, params) };
  const result = await runner.query(
    `SELECT * FROM wind.provider_contract_revisions
     WHERE adapter_id = $1 ORDER BY revision_number DESC LIMIT 1`,
    [adapterId],
  );
  return result.rows[0] || null;
}

async function insertLifecycleRevision(client, current, action, body) {
  const controls = dualControl(body);
  const state = action === 'DEACTIVATE' ? 'DEACTIVATED' : 'RETIRED';
  const inserted = await client.query(
    `INSERT INTO wind.provider_contract_revisions
       (adapter_id, revision_number, adapter_version, provider_id, provider_version,
        invocation_mode, input_schema_digest, output_schema_digest, credential_env_ref,
        endpoint_env_ref, schema_verification, lifecycle_state, lifecycle_action,
        supersedes_revision_id, approval_record_ref, requested_by_role,
        approved_by_role, confirmed_by_role, lifecycle_reason)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'verified', $11, $12,
             $13, $14, $15, $16, $17, $18)
     RETURNING *`,
    [current.adapter_id, current.revision_number + 1, current.adapter_version,
      current.provider_id, current.provider_version, current.invocation_mode,
      current.input_schema_digest, current.output_schema_digest,
      current.credential_env_ref, current.endpoint_env_ref, state, state === 'DEACTIVATED' ? 'DEACTIVATED' : 'RETIRED',
      current.revision_id, controls.approvalRecordRef, body.requested_by_role || 'engineer',
      controls.approvedByRole, controls.confirmedByRole, body.lifecycle_reason || null],
  );
  return inserted.rows[0];
}

async function lifecycle(req, res, next, action) {
  const client = await pool.connect();
  try {
    const adapterId = requiredString(req.params, 'adapterId');
    await client.query('BEGIN');
    const current = await latestRevision(adapterId, client);
    if (!current) throw new NotFoundError('Provider adapter is not registered');
    if (current.lifecycle_state === 'RETIRED') {
      throw new BadRequestError('provider adapter is already retired');
    }
    if (action === 'DEACTIVATE' && current.lifecycle_state !== 'ACTIVE') {
      throw new BadRequestError('only an active provider adapter can be deactivated');
    }
    const revision = await insertLifecycleRevision(client, current, action, req.body || {});
    await client.query('COMMIT');
    return res.status(201).json({ ...revision, lifecycle: action.toLowerCase() });
  } catch (error) {
    await client.query('ROLLBACK');
    if (error?.code === '23505') return res.status(409).json({ error: 'registry conflict', message: error.detail || 'registry revision conflict' });
    return next(error);
  } finally {
    client.release();
  }
}

// List the latest immutable lifecycle revision for each adapter.
providerContractsRouter.get('/', async (_req, res, next) => {
  try {
    const result = await query('SELECT * FROM wind.v_active_provider_contracts ORDER BY adapter_id');
    res.json(result.rows);
  } catch (error) { next(error); }
});

providerContractsRouter.get('/:adapterId', async (req, res, next) => {
  try {
    const current = await latestRevision(requiredString(req.params, 'adapterId'));
    if (!current) throw new NotFoundError('Provider adapter is not registered');
    const history = await query(
      'SELECT * FROM wind.provider_contract_revisions WHERE adapter_id = $1 ORDER BY revision_number DESC',
      [current.adapter_id],
    );
    res.json({ current, history: history.rows });
  } catch (error) { next(error); }
});

// Register a genuinely new adapter. Existing adapters require lifecycle
// endpoints so the immutable revision stream and dual-control checks apply.
providerContractsRouter.post('/', registryWriteLimiter, async (req, res, next) => {
  const client = await pool.connect();
  try {
    const fields = registryFields(req.body || {});
    const control = registrationControl(req.body || {});
    await client.query('BEGIN');
    const existing = await latestRevision(fields.adapterId, client);
    if (existing) {
      await client.query('ROLLBACK');
      return res.status(409).json({ error: 'provider adapter already registered', message: 'use a new adapter id or a lifecycle operation' });
    }
    const inserted = await client.query(
      `INSERT INTO wind.provider_contract_revisions
         (adapter_id, revision_number, adapter_version, provider_id, provider_version,
          invocation_mode, input_schema_digest, output_schema_digest, credential_env_ref,
          endpoint_env_ref, schema_verification, lifecycle_state, lifecycle_action,
          approval_record_ref, requested_by_role, acknowledged_by_role)
       VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8, $9, 'verified', 'ACTIVE',
               'REGISTERED', $10, $11, $12)
       RETURNING *`,
      [fields.adapterId, fields.adapterVersion, fields.providerId, fields.providerVersion,
        fields.invocationMode, fields.inputSchemaDigest, fields.outputSchemaDigest,
        fields.credentialEnvRef, fields.endpointEnvRef, control.approvalRecordRef,
        control.requestedByRole, control.acknowledgedByRole],
    );
    await client.query('COMMIT');
    res.status(201).json(inserted.rows[0]);
  } catch (error) {
    await client.query('ROLLBACK');
    if (error?.code === '23505') return res.status(409).json({ error: 'registry conflict', message: error.detail || 'provider adapter already registered' });
    return next(error);
  } finally { client.release(); }
});

providerContractsRouter.post('/:adapterId/deactivate', registryWriteLimiter, (req, res, next) => lifecycle(req, res, next, 'DEACTIVATE'));
providerContractsRouter.post('/:adapterId/retire', registryWriteLimiter, (req, res, next) => lifecycle(req, res, next, 'RETIRE'));

// Record a rotation after the operator has rotated the environment boundary
// and restarted the service. Wind stores only the env reference and digest.
providerContractsRouter.post('/:adapterId/credential-rotations', registryWriteLimiter, async (req, res, next) => {
  try {
    const adapterId = requiredString(req.params, 'adapterId');
    const rotationRecordRef = requiredString(req.body || {}, 'rotation_record_ref');
    const credentialEnvRef = envRef(req.body?.credential_env_ref, 'credential_env_ref', true);
    const current = await latestRevision(adapterId);
    if (!current) throw new NotFoundError('Provider adapter is not registered');
    if (current.lifecycle_state !== 'ACTIVE') throw new BadRequestError('credential rotation requires an active provider adapter');
    if (current.credential_env_ref !== credentialEnvRef) {
      throw new BadRequestError('credential_env_ref must match the active registry revision');
    }
    const metadata = resolveEnvironmentReferenceMetadata(credentialEnvRef, 'credential_env_ref');
    const inserted = await query(
      `INSERT INTO wind.provider_credential_rotations
         (adapter_id, revision_id, credential_env_ref, credential_fingerprint, rotation_record_ref)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING rotation_id, adapter_id, revision_id, credential_env_ref,
                 credential_fingerprint, rotation_record_ref, rotated_at`,
      [adapterId, current.revision_id, metadata.reference, metadata.fingerprint, rotationRecordRef],
    );
    res.status(201).json(inserted.rows[0]);
  } catch (error) { next(error); }
});

providerContractsRouter.get('/:adapterId/credential-rotations', async (req, res, next) => {
  try {
    const adapterId = requiredString(req.params, 'adapterId');
    const result = await query(
      `SELECT rotation_id, adapter_id, revision_id, credential_env_ref,
              credential_fingerprint, rotation_record_ref, rotated_at
       FROM wind.provider_credential_rotations
       WHERE adapter_id = $1 ORDER BY rotated_at DESC`,
      [adapterId],
    );
    res.json(result.rows);
  } catch (error) { next(error); }
});
