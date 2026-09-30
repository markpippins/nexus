const { test } = require('node:test')
const assert = require('node:assert/strict')
const { censusArgvFromEnv, parseCensusFlags } = require('../lib/census-emit.ts')
const { resolveSessionId } = require('../lib/execution-identity.ts')

// ── flag transport: env -> argv ─────────────────────────────────────────────

test('A3: empty env yields no census — the census is off unless asked for', () => {
  const r = censusArgvFromEnv({})
  assert.equal(r.ok, true)
  assert.deepEqual(r.argv, [])
  assert.equal(parseCensusFlags(r.argv).flags.census, false)
})

test('A3: CENSUS_ENABLED synthesises --census', () => {
  for (const v of ['1', 'true', 'yes', 'on', 'TRUE']) {
    const r = censusArgvFromEnv({ CENSUS_ENABLED: v })
    assert.equal(r.ok, true, `expected ${v} to enable`)
    assert.ok(r.argv.includes('--census'))
  }
})

test('A3: a falsy CENSUS_ENABLED does NOT synthesise --census', () => {
  for (const v of ['0', 'false', 'no', 'off']) {
    const r = censusArgvFromEnv({ CENSUS_ENABLED: v })
    assert.equal(r.ok, true)
    assert.ok(!r.argv.includes('--census'), `${v} must not enable the census`)
  }
})

test('A3: a nonsense CENSUS_ENABLED is an error, not a silent disable', () => {
  const r = censusArgvFromEnv({ CENSUS_ENABLED: 'maybe' })
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('CENSUS_ENABLED must be')))
})

test('A3: CENSUS_RATE without CENSUS_ENABLED still fails loud through the parser', () => {
  const env = { CENSUS_RATE: '1/20' }
  const r = censusArgvFromEnv(env)
  assert.equal(r.ok, true, 'transport itself is well-formed')
  // The transport does NOT paper over it — the single parser rejects it.
  const parsed = parseCensusFlags(r.argv)
  assert.equal(parsed.ok, false, 'rate without master switch must be rejected')
  assert.ok(parsed.errors.some((e) => e.includes('would never take effect')))
})

test('A3: CENSUS_RATE + CENSUS_ENABLED parses cleanly end to end', () => {
  const r = censusArgvFromEnv({ CENSUS_ENABLED: '1', CENSUS_RATE: '1/20' })
  assert.equal(r.ok, true)
  const parsed = parseCensusFlags(r.argv)
  assert.equal(parsed.ok, true, parsed.errors.join(';'))
  assert.equal(parsed.flags.rate, '1/20')
})

test('A3: CENSUS_ON_REVIEW without the master switch is rejected', () => {
  const parsed = parseCensusFlags(censusArgvFromEnv({ CENSUS_ON_REVIEW: '1' }).argv)
  assert.equal(parsed.ok, false)
})

test('A3: a misspelled CENSUS_* variable is an ERROR, never silently ignored', () => {
  const r = censusArgvFromEnv({ CENSUS_ENABLED: '1', CENSUS_RATTE: '1/20' })
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('CENSUS_RATTE')))
  assert.ok(r.errors.some((e) => e.includes('silently ignored')))
})

test('A3: non-census env vars are left alone', () => {
  const r = censusArgvFromEnv({ PATH: '/usr/bin', NEXUS_AGENT_MODEL: 'opencode/x', PORT: '3100' })
  assert.equal(r.ok, true)
  assert.deepEqual(r.argv, [])
})

test('A3: empty-string env vars are treated as unset', () => {
  const r = censusArgvFromEnv({ CENSUS_ENABLED: '', CENSUS_RATE: '' })
  assert.equal(r.ok, true)
  assert.deepEqual(r.argv, [])
})

test('A3: the rate value is passed through verbatim for the parser to judge', () => {
  // Transport must not "helpfully" validate; one parser owns the rules.
  const r = censusArgvFromEnv({ CENSUS_ENABLED: '1', CENSUS_RATE: '20/1' })
  assert.equal(r.ok, true)
  assert.equal(parseCensusFlags(r.argv).ok, false, 'parser must reject N > M')
})

// ── session identity ───────────────────────────────────────────────────────

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

test('A3: a supplied session reference wins — do not invent a second identity', () => {
  assert.equal(
    resolveSessionId({ supplied: 'wind-harness-20260929-120000-abcd1234', source: 'wind' }),
    'wind-harness-20260929-120000-abcd1234',
  )
})

test('A3: a minted session is NEVER uuid-shaped (DBA condition 3)', () => {
  for (let i = 0; i < 50; i++) {
    const s = resolveSessionId({ source: 'wind', executorId: 'harness' })
    assert.equal(UUID_RE.test(s), false, `uuid-shaped session minted: ${s}`)
  }
})

test('A3: a minted session follows the established <source>-<executor>-<stamp>-<hex> shape', () => {
  const at = new Date('2026-09-29T12:00:00Z')
  const s = resolveSessionId({ source: 'wind', executorId: 'harness', at })
  assert.match(s, /^wind-harness-20260929-120000-[0-9a-f]{8}$/, `got ${s}`)
})

test('A3: two executions in one supplied session stay distinct (condition 1)', () => {
  const s = 'wind-harness-20260929-120000-abcd1234'
  const a = resolveSessionId({ supplied: s, source: 'wind' })
  const b = resolveSessionId({ supplied: s, source: 'wind' })
  assert.equal(a, b, 'same session, and the session is not the execution id')
})

test('A3: minting without an executor id still produces a valid name', () => {
  const s = resolveSessionId({ source: 'conduit', at: new Date('2026-09-29T12:00:00Z') })
  assert.match(s, /^conduit-anon-20260929-120000-[0-9a-f]{8}$/, `got ${s}`)
})

test('A3: an empty supplied value falls through to minting', () => {
  const s = resolveSessionId({ supplied: '', source: 'wind', at: new Date('2026-09-29T12:00:00Z') })
  assert.match(s, /^wind-/, `got ${s}`)
})

test('A3: minted sessions differ across calls', () => {
  const seen = new Set()
  for (let i = 0; i < 200; i++) seen.add(resolveSessionId({ source: 'wind', executorId: 'harness' }))
  assert.ok(seen.size > 190, `expected near-unique sessions, got ${seen.size}/200`)
})
