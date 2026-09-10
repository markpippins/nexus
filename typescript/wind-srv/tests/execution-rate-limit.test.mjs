import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/routes/execution.js', import.meta.url), 'utf8');

test('execution write handlers use a bounded rate limiter', () => {
  assert.match(source, /rateLimit\(\{/);
  assert.match(source, /windowMs:\s*60 \* 1000/);
  assert.match(source, /max:\s*60/);
  assert.equal((source.match(/executionWriteLimiter/g) || []).length, 4);
});
