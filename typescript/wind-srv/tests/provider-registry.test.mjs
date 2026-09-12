import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolveEnvironmentReferenceMetadata } from '../src/provider-dispatch.js';

const migration = readFileSync(new URL('../../../sql/V154__wind_provider_registry_lifecycle.sql', import.meta.url), 'utf8');

test('V154 defines an append-only revision stream and latest-state projection', () => {
  assert.match(migration, /CREATE TABLE IF NOT EXISTS wind\.provider_contract_revisions/);
  assert.match(migration, /UNIQUE \(adapter_id, revision_number\)/);
  assert.match(migration, /CREATE OR REPLACE VIEW wind\.v_active_provider_contracts/);
  assert.match(migration, /DISTINCT ON \(adapter_id\)/);
  assert.match(migration, /ORDER BY adapter_id, revision_number DESC/);
  assert.match(migration, /trg_wind_provider_contract_revisions_immutable/);
  assert.match(migration, /BEFORE UPDATE OR DELETE ON wind\.provider_contract_revisions/);
});

test('V154 encodes dual control for deactivation and retirement', () => {
  assert.match(migration, /lifecycle_state\s+text NOT NULL CHECK \(lifecycle_state IN \('ACTIVE', 'DEACTIVATED', 'RETIRED'\)\)/);
  assert.match(migration, /lifecycle_action\s+text NOT NULL CHECK \(lifecycle_action IN \('REGISTERED', 'DEACTIVATED', 'RETIRED'\)\)/);
  assert.match(migration, /approved_by_role <> confirmed_by_role/);
  assert.match(migration, /approval_record_ref IS NOT NULL/);
  assert.match(migration, /acknowledged_by_role IN \('architect', 'operator'\)/);
});

test('V154 stores only credential references and fingerprints', () => {
  assert.match(migration, /CREATE TABLE IF NOT EXISTS wind\.provider_credential_rotations/);
  assert.match(migration, /credential_env_ref\s+text NOT NULL/);
  assert.match(migration, /credential_fingerprint\s+text NOT NULL CHECK/);
  assert.match(migration, /trg_wind_provider_credential_rotations_immutable/);
  assert.doesNotMatch(migration, /credential_value|secret_material\s+text|credential_secret\s+text/);
});

test('credential metadata fingerprints the resolved value without returning it', () => {
  const metadata = resolveEnvironmentReferenceMetadata('TEST_PROVIDER_SECRET', 'credential_env_ref', {
    TEST_PROVIDER_SECRET: 'rotation-secret-value',
  });
  assert.deepEqual(Object.keys(metadata).sort(), ['fingerprint', 'reference']);
  assert.equal(metadata.reference, 'TEST_PROVIDER_SECRET');
  assert.match(metadata.fingerprint, /^sha256:[0-9a-f]{64}$/);
  assert.doesNotMatch(JSON.stringify(metadata), /rotation-secret-value/);
});

test('credential metadata fails closed when the environment boundary is empty', () => {
  assert.throws(
    () => resolveEnvironmentReferenceMetadata('TEST_PROVIDER_SECRET', 'credential_env_ref', {}),
    /not configured in the secret boundary/,
  );
});
