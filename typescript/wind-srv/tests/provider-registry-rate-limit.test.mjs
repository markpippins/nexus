import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/routes/provider-contracts.js', import.meta.url), 'utf8');

test('provider registry write handlers use a bounded rate limiter', () => {
  assert.match(source, /registryWriteLimiter\s*=\s*rateLimit\(\{/);
  assert.match(source, /windowMs:\s*60 \* 1000/);
  assert.match(source, /max:\s*30/);
  assert.equal((source.match(/registryWriteLimiter/g) || []).length, 5);
});
