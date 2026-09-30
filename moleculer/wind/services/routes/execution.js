import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { pool, query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';
import { assertOutcome, canonicalJson, digest, DIGEST, requestMaterial } from '../execution-contract.js';
import { validateProviderInvocation } from '../provider-contract.js';
import {
  assertRegisteredContract,
  classifyDispatchFailure,
  dispatchInputDigest,
  invokeRegisteredAdapter,
  resolveEnvironmentReferenceMetadata,
  resolveEnvironmentReference,
} from '../provider-dispatch.js';

export const executionRouter = Router();

// Execution writes perform database transactions and are intentionally bounded
// independently of the read APIs. This protects the request/attempt/receipt
// evidence surface from resource-exhaustion floods while leaving normal Wind
// reads available for operators.
const executionWriteLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 60,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: { error: 'execution write rate limit exceeded' },
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function requiredString(body, name) {
  if (typeof body?.[name] !== 'string' || body[name].trim() === '') {
    throw new BadRequestError(`${name} is required`);
  }
  return body[name].trim();
}

function uuidField(body, name, required = false) {
  const value = body?.[name];
  if (value === undefined || value === null || value === '') {
    if (required) throw new BadRequestError(`${name} is required`);
    return null;
  }
  if (typeof value !== 'string' || !UUID.test(value)) throw new BadRequestError(`${name} must be a UUID`);
  return value;
}

function digestField(body, name) {
  const value = requiredString(body, name);
  if (!DIGEST.test(value)) throw new BadRequestError(`${name} must be a sha256 digest`);
  return value;
}

function outcomeStatus(body) {
  try { return assertOutcome(requiredString(body, 'outcome_status')); }
  catch (err) { throw new BadRequestError(err.message); }
}

function jsonObject(value, name, fallback) {
  if (value === undefined) return fallback;
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new BadRequestError(`${name} must be a JSON object`);
  }
  return value;
}

function jsonArray(value, name) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) throw new BadRequestError(`${name} must be a JSON array`);
  return value;
}

function pgConflict(err, res, next) {
  if (err?.code === '23505') {
    return res.status(409).json({ error: 'idempotency conflict', message: err.detail || 'duplicate idempotency key' });
  }
  return next(err);
}

function dispatchInput(body) {
  if (body === undefined) return null;
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new BadRequestError('input must be a JSON object');
  }
  return body;
}

// List immutable execution requests.
executionRouter.get('/', async (req, res, next) => {
  try {
    const { correlation_id, idempotency_key, limit = 50 } = req.query;
    const conditions = [];
    const params = [];
    let index = 1;
    if (correlation_id) { conditions.push(`correlation_id = $${index++}`); params.push(correlation_id); }
    if (idempotency_key) { conditions.push(`idempotency_key = $${index++}`); params.push(idempotency_key); }
    params.push(Math.min(Math.max(parseInt(String(limit), 10) || 50, 1), 100));
    const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
    const result = await query(
      `SELECT * FROM wind.execution_requests ${where} ORDER BY requested_at DESC LIMIT $${index}`,
      params,
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Create or replay an immutable request. Same idempotency key + same digest is
// a replay; same key + different material is a conflict, never an overwrite.
executionRouter.post('/', executionWriteLimiter, async (req, res, next) => {
  try {
    const body = req.body || {};
    const idempotencyKey = requiredString(body, 'idempotency_key');
    const artifactType = requiredString(body, 'artifact_type');
    const artifactRef = requiredString(body, 'artifact_ref');
    const artifactRevision = requiredString(body, 'artifact_revision');
    const artifactFingerprint = digestField(body, 'artifact_fingerprint');
    const readSetDigest = digestField(body, 'read_set_digest');
    const evaluatorContractDigest = digestField(body, 'evaluator_contract_digest');
    const correlationId = uuidField(body, 'correlation_id', true);
    const causationId = uuidField(body, 'causation_id');
    const workflowVersionId = uuidField(body, 'workflow_version_id');
    const nodeId = uuidField(body, 'node_id');
    if ((workflowVersionId === null) !== (nodeId === null)) {
      throw new BadRequestError('workflow_version_id and node_id must be supplied together');
    }
    const providerContract = jsonObject(body.provider_contract, 'provider_contract', {});
    const invocationContract = jsonObject(body.invocation_contract, 'invocation_contract', {});
    const failurePolicy = jsonObject(body.failure_policy, 'failure_policy', {});
    let normalizedInvocation;
    try {
      normalizedInvocation = validateProviderInvocation({
        provider_contract: providerContract,
        invocation_contract: invocationContract,
        failure_policy: failurePolicy,
      });
    } catch (err) {
      throw new BadRequestError(`invalid provider invocation contract: ${err.message}`);
    }
    const material = requestMaterial({
      artifact_type: artifactType,
      artifact_ref: artifactRef,
      artifact_revision: artifactRevision,
      artifact_fingerprint: artifactFingerprint,
      read_set_digest: readSetDigest,
      evaluator_contract_digest: evaluatorContractDigest,
      causation_id: causationId,
      workflow_version_id: workflowVersionId,
      node_id: nodeId,
      correlation_id: correlationId,
      provider_contract: normalizedInvocation.provider_contract,
      invocation_contract: normalizedInvocation.invocation_contract,
      failure_policy: normalizedInvocation.failure_policy,
    });
    const requestDigest = digest(material);
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const inserted = await client.query(
        `INSERT INTO wind.execution_requests
          (workflow_version_id, node_id, artifact_type, artifact_ref, artifact_revision,
           artifact_fingerprint, read_set_digest, evaluator_contract_digest, causation_id,
           correlation_id, idempotency_key, provider_contract, invocation_contract,
           failure_policy, request_digest)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
         ON CONFLICT (idempotency_key) DO NOTHING
         RETURNING *`,
        [workflowVersionId, nodeId, artifactType, artifactRef, artifactRevision, artifactFingerprint, readSetDigest, evaluatorContractDigest,
          causationId, correlationId, idempotencyKey,
          normalizedInvocation.provider_contract, normalizedInvocation.invocation_contract,
          normalizedInvocation.failure_policy, requestDigest],
      );
      if (inserted.rows.length === 0) {
        const existing = await client.query(
          'SELECT * FROM wind.execution_requests WHERE idempotency_key = $1 FOR SHARE', [idempotencyKey],
        );
        await client.query('COMMIT');
        const row = existing.rows[0];
        if (!row || row.request_digest !== requestDigest) {
          return res.status(409).json({ error: 'idempotency conflict', message: 'idempotency_key is already bound to different request material' });
        }
        return res.status(200).json({ ...row, replay: true });
      }
      await client.query('COMMIT');
      res.status(201).json({ ...inserted.rows[0], replay: false });

    } catch (err) {
      await client.query('ROLLBACK');
      pgConflict(err, res, next);
    } finally { client.release(); }
  } catch (err) { next(err); }
});

executionRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query('SELECT * FROM wind.execution_requests WHERE id = $1', [req.params.id]);
    if (!result.rows.length) throw new NotFoundError('Execution request not found');
    const attempts = await query(
      'SELECT * FROM wind.execution_attempts WHERE request_id = $1 ORDER BY attempt_number', [req.params.id],
    );
    const receipts = await query(
      'SELECT * FROM wind.execution_receipts WHERE request_id = $1 ORDER BY issued_at', [req.params.id],
    );
    res.json({ ...result.rows[0], attempts: attempts.rows, receipts: receipts.rows });
  } catch (err) { next(err); }
});

// List attempts for one request.
executionRouter.get('/:id/attempts', async (req, res, next) => {
  try {
    const request = await query('SELECT id FROM wind.execution_requests WHERE id = $1', [req.params.id]);
    if (!request.rows.length) throw new NotFoundError('Execution request not found');
    const result = await query(
      'SELECT * FROM wind.execution_attempts WHERE request_id = $1 ORDER BY attempt_number', [req.params.id],
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Record one final provider outcome. The route never calls the provider.
executionRouter.post('/:id/attempts', executionWriteLimiter, async (req, res, next) => {
  try {
    const body = req.body || {};
    const request = await query('SELECT id FROM wind.execution_requests WHERE id = $1', [req.params.id]);
    if (!request.rows.length) throw new NotFoundError('Execution request not found');
    const attemptNumber = Number(body.attempt_number);
    if (!Number.isInteger(attemptNumber) || attemptNumber < 1) throw new BadRequestError('attempt_number must be a positive integer');
    const attemptKey = requiredString(body, 'attempt_idempotency_key');
    const executorId = requiredString(body, 'executor_id');
    const parentAttemptId = uuidField(body, 'parent_attempt_id');
    const status = outcomeStatus(body);
    const result = jsonObject(body.result, 'result', {});
    const resultDigest = body.result_digest === undefined ? digest(result) : digestField(body, 'result_digest');
    if (body.result_digest !== undefined && resultDigest !== digest(result)) {
      throw new BadRequestError('result_digest does not match result');
    }
    const existing = await query(
      'SELECT * FROM wind.execution_attempts WHERE request_id = $1 AND attempt_idempotency_key = $2',
      [req.params.id, attemptKey],
    );
    if (existing.rows.length) {
      const row = existing.rows[0];
      const same = row.attempt_number === attemptNumber && row.executor_id === executorId && row.status === status && row.result_digest === resultDigest;
      if (!same) return res.status(409).json({ error: 'idempotency conflict', message: 'attempt key is already bound to different attempt material' });
      return res.status(200).json({ ...row, replay: true });
    }
    const numberConflict = await query(
      'SELECT id FROM wind.execution_attempts WHERE request_id = $1 AND attempt_number = $2',
      [req.params.id, attemptNumber],
    );
    if (numberConflict.rows.length) return res.status(409).json({ error: 'attempt conflict', message: 'attempt_number is already bound to another attempt' });
    const inserted = await query(
      `INSERT INTO wind.execution_attempts
         (request_id, parent_attempt_id, attempt_number, attempt_idempotency_key, executor_id,
          provider_invocation_ref, status, result, error, result_digest, started_at, completed_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
       RETURNING *`,        [req.params.id, parentAttemptId, attemptNumber, attemptKey, executorId, body.provider_invocation_ref || null,
        status, result, body.error || null, resultDigest, body.started_at || null, body.completed_at || null],

    );
    res.status(201).json({ ...inserted.rows[0], replay: false });
  } catch (err) { pgConflict(err, res, next); }
});

// Dispatch one persisted, schema-verified adapter contract. The reservation is
// recorded before any adapter call; the terminal child attempt and receipt are
// appended only after an observed result. This route never touches lifecycle,
// Resolution, or PEB state.
executionRouter.post('/:id/dispatch', executionWriteLimiter, async (req, res, next) => {
  let request;
  let reservation;
  try {
    const body = req.body || {};
    const dispatchKey = requiredString(body, 'dispatch_idempotency_key');
    const input = dispatchInput(body.input);
    const loaded = await query('SELECT * FROM wind.execution_requests WHERE id = $1', [req.params.id]);
    if (!loaded.rows.length) throw new NotFoundError('Execution request not found');
    request = loaded.rows[0];

    const existing = await query(
      `SELECT * FROM wind.execution_attempts
       WHERE request_id = $1
         AND attempt_idempotency_key IN ($2, $3)
       ORDER BY attempt_number`,
      [request.id, `dispatch:${dispatchKey}`, `dispatch:${dispatchKey}:terminal`],
    );
    if (existing.rows.length) {
      const reservationPrior = existing.rows.find((row) => row.attempt_idempotency_key === `dispatch:${dispatchKey}`);
      const terminalPrior = existing.rows.find((row) => row.attempt_idempotency_key === `dispatch:${dispatchKey}:terminal`);
      if (!terminalPrior) {
        return res.status(409).json({ error: 'dispatch in progress', attempt_id: reservationPrior?.id });
      }
      const priorReceipt = await query(
        'SELECT * FROM wind.execution_receipts WHERE attempt_id = $1', [terminalPrior.id],
      );
      return res.status(200).json({
        request,
        reservation: reservationPrior || null,
        attempt: terminalPrior,
        receipt: priorReceipt.rows[0] || null,
        replay: true,
      });
    }

    const registeredResult = await query(
      'SELECT * FROM wind.v_active_provider_contracts WHERE adapter_id = $1',
      [request.provider_contract?.adapter_id],
    );
    const registered = registeredResult.rows[0] || null;
    const startedAt = new Date().toISOString();
    const nextNumberResult = await query(
      'SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_number FROM wind.execution_attempts WHERE request_id = $1',
      [request.id],
    );
    const attemptNumber = Number(nextNumberResult.rows[0].next_number);
    const reserved = await query(
      `INSERT INTO wind.execution_attempts
         (request_id, attempt_number, attempt_idempotency_key, executor_id,
          provider_invocation_ref, status, result, result_digest, started_at)
       VALUES ($1, $2, $3, $4, $5, 'IN_FLIGHT', $6::jsonb, $7, $8)
       RETURNING *`,
      [request.id, attemptNumber, `dispatch:${dispatchKey}`, 'wind.provider-dispatch', null,
        JSON.stringify({ dispatch_input_digest: dispatchInputDigest(input) }), digest({ dispatch_input_digest: dispatchInputDigest(input) }), startedAt],
    );
    reservation = reserved.rows[0];

    let observed;
    let providerCalled = false;
    let credentialMetadata = null;
    try {
      const validated = validateProviderInvocation({
        provider_contract: request.provider_contract,
        invocation_contract: request.invocation_contract,
        failure_policy: request.failure_policy,
      });
      assertRegisteredContract(validated.provider_contract, validated.invocation_contract, registered);
      // Resolve the optional credential reference only to prove the secret
      // boundary is configured. The value is passed to the adapter environment
      // but is never persisted or returned.
      if (registered.credential_env_ref) {
        credentialMetadata = resolveEnvironmentReferenceMetadata(registered.credential_env_ref, 'credential_env_ref');
      }
      if (registered.endpoint_env_ref) {
        resolveEnvironmentReference(registered.endpoint_env_ref, 'endpoint_env_ref');
      }
      providerCalled = true;
      observed = await invokeRegisteredAdapter({
        registered,
        request,
        invocation: validated.invocation_contract,
        input,
      });
    } catch (error) {
      const mapped = classifyDispatchFailure(error, request.failure_policy || {});
      const failureResult = { code: mapped.outcome.toLowerCase(), reason: mapped.reason };
      const terminal = await query(
        `INSERT INTO wind.execution_attempts
           (request_id, parent_attempt_id, attempt_number, attempt_idempotency_key,
            executor_id, provider_invocation_ref, status, result, error,
           result_digest, started_at, completed_at, credential_env_ref,
           credential_fingerprint)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14)
       RETURNING *`,
        [request.id, reservation.id, attemptNumber + 1, `dispatch:${dispatchKey}:terminal`,
          'wind.provider-dispatch', null, mapped.outcome, JSON.stringify(failureResult), mapped.reason,
          digest(failureResult), startedAt, new Date().toISOString(), credentialMetadata?.reference || null,
          credentialMetadata?.fingerprint || null],
      );
      if (!providerCalled) {
        return res.status(mapped.outcome === 'INVALID' ? 400 : 409).json({
          request,
          reservation,
          attempt: terminal.rows[0],
          receipt: null,
          replay: false,
        });
      }
      const receipt = await query(
        `INSERT INTO wind.execution_receipts         (request_id, attempt_id, outcome_status, result_digest, evidence_refs, lineage,
          credential_env_ref, credential_fingerprint)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8)
       RETURNING *`,
        [request.id, terminal.rows[0].id, mapped.outcome, digest(failureResult),
          JSON.stringify([{ type: 'dispatch-failure', reason: mapped.reason }]),
          JSON.stringify({ request_id: request.id, reservation_id: reservation.id, advisory: true }),
          credentialMetadata?.reference || null, credentialMetadata?.fingerprint || null],
      );
      return res.status(201).json({ request, reservation, attempt: terminal.rows[0], receipt: receipt.rows[0], replay: false });
    }

    const terminal = await query(
      `INSERT INTO wind.execution_attempts
         (request_id, parent_attempt_id, attempt_number, attempt_idempotency_key,
          executor_id, provider_invocation_ref, status, result, result_digest,
         started_at, completed_at, credential_env_ref, credential_fingerprint)
       VALUES ($1, $2, $3, $4, $5, $6, 'SUCCEEDED', $7::jsonb, $8, $9, $10, $11, $12)
       RETURNING *`,
      [request.id, reservation.id, attemptNumber + 1, `dispatch:${dispatchKey}:terminal`,
        'wind.provider-dispatch', observed.provider_invocation_ref, JSON.stringify(observed.result),
        observed.result_digest, startedAt, new Date().toISOString(), credentialMetadata?.reference || null,
        credentialMetadata?.fingerprint || null],
    );
    const receipt = await query(
      `INSERT INTO wind.execution_receipts
         (request_id, attempt_id, outcome_status, result_digest, evidence_refs, lineage,
          credential_env_ref, credential_fingerprint)
       VALUES ($1, $2, 'SUCCEEDED', $3, $4::jsonb, $5::jsonb, $6, $7)
       RETURNING *`,
      [request.id, terminal.rows[0].id, observed.result_digest,
        JSON.stringify([{ type: 'provider-result', digest: observed.result_digest, input_digest: dispatchInputDigest(input) }]),
        JSON.stringify({ request_id: request.id, reservation_id: reservation.id, adapter_id: request.provider_contract.adapter_id, advisory: true }),
        credentialMetadata?.reference || null, credentialMetadata?.fingerprint || null],
    );
    return res.status(201).json({ request, reservation, attempt: terminal.rows[0], receipt: receipt.rows[0], replay: false });
  } catch (err) { pgConflict(err, res, next); }
});

// Issue exactly one advisory receipt for an attempt; receipt status/digest must
// equal the immutable attempt, so malformed or contradictory results fail closed.
executionRouter.get('/:id/receipts', async (req, res, next) => {
  try {
    const request = await query('SELECT id FROM wind.execution_requests WHERE id = $1', [req.params.id]);
    if (!request.rows.length) throw new NotFoundError('Execution request not found');
    const result = await query(
      'SELECT * FROM wind.execution_receipts WHERE request_id = $1 ORDER BY issued_at', [req.params.id],
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

executionRouter.post('/:id/receipts', executionWriteLimiter, async (req, res, next) => {
  try {
    const body = req.body || {};
    const attemptId = requiredString(body, 'attempt_id');
    const attempt = await query(
      'SELECT * FROM wind.execution_attempts WHERE id = $1 AND request_id = $2', [attemptId, req.params.id],
    );
    if (!attempt.rows.length) throw new NotFoundError('Execution attempt not found for request');
    const row = attempt.rows[0];
    const status = outcomeStatus({ outcome_status: body.outcome_status || row.status });
    const resultDigest = body.result_digest || row.result_digest;
    if (!DIGEST.test(resultDigest)) throw new BadRequestError('receipt result_digest must be a sha256 digest');
    if (status !== row.status || resultDigest !== row.result_digest) {
      throw new BadRequestError('receipt outcome_status and result_digest must match the attempt');
    }
    const evidenceRefs = jsonArray(body.evidence_refs, 'evidence_refs');
    const lineage = jsonObject(body.lineage, 'lineage', {});
    const existing = await query('SELECT * FROM wind.execution_receipts WHERE attempt_id = $1', [attemptId]);
    if (existing.rows.length) {
      const prior = existing.rows[0];
      const same = canonicalJson(prior.evidence_refs) === canonicalJson(evidenceRefs) && canonicalJson(prior.lineage) === canonicalJson(lineage);
      if (!same) return res.status(409).json({ error: 'receipt conflict', message: 'attempt already has a different immutable receipt' });
      return res.status(200).json({ ...prior, replay: true });
    }
    const inserted = await query(
      `INSERT INTO wind.execution_receipts
         (request_id, attempt_id, outcome_status, result_digest, evidence_refs, lineage)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
       RETURNING *`,
      [req.params.id, attemptId, status, resultDigest, JSON.stringify(evidenceRefs), JSON.stringify(lineage)],
    );
    res.status(201).json({ ...inserted.rows[0], replay: false });
  } catch (err) { pgConflict(err, res, next); }
});
