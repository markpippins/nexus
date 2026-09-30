/**
 * Cross-language pin for the doctrine snapshot digest — Decision 28 (85c6d979).
 *
 * The fixture in `bin/fixtures/doctrine-snapshot-vectors.json` is generated from the
 * CANONICAL python implementation (`python/peb-kernel/src/peb_kernel/doctrine.py`).
 * If this suite and `python/peb-kernel/tests/test_doctrine.py` both read that one file,
 * the two implementations cannot drift silently: a change to either shows up as a
 * failing vector in the other language.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const {
  buildDoctrineSnapshot, canonicalJson, contentHash, normalizeCards,
  SNAPSHOT_SCHEMA_VERSION, SHA256_RE,
} = require('../lib/doctrine-snapshot.ts')

const FIXTURE = path.resolve(__dirname, '../../../bin/fixtures/doctrine-snapshot-vectors.json')
const fixture = JSON.parse(readFileSync(FIXTURE, 'utf8'))

test('Decision 28: the fixture is present and pins the schema version', () => {
  assert.ok(fixture.vectors.length >= 3, 'ruling requires at least 3 distinct vectors')
  assert.equal(fixture.snapshot_schema_version, SNAPSHOT_SCHEMA_VERSION)
})

for (const v of fixture.vectors) {
  test(`fixture vector: ${v.name}`, () => {
    const snap = buildDoctrineSnapshot({
      systemPrompt: v.input.system_prompt,
      bootstrap: v.input.bootstrap,
      activeProcedureCards: v.input.active_procedure_cards,
    })
    assert.equal(snap.snapshot_id, v.expected.snapshot_id,
      `snapshot_id drift on "${v.name}" — TS and python disagree; one implementation is wrong`)
    assert.equal(snap.system_prompt_hash, v.expected.system_prompt_hash)
    assert.equal(snap.bootstrap_hash, v.expected.bootstrap_hash)
    assert.deepEqual(snap.active_procedure_cards, v.expected.active_procedure_cards)
    assert.equal(snap.schema_version, SNAPSHOT_SCHEMA_VERSION)
  })
}

// ── the specific traps the ruling names ─────────────────────────────────────

test('Decision 28: card REORDERING yields an identical address', () => {
  const byName = Object.fromEntries(fixture.vectors.map((v) => [v.name, v]))
  assert.ok(byName['card-reorder'] && byName['three-cards'], 'fixture must contain both')
  assert.equal(
    byName['card-reorder'].expected.snapshot_id,
    byName['three-cards'].expected.snapshot_id,
    'fixture invariant: reordering must not change the address',
  )
})

test('Decision 28: DUPLICATE cards are deduped', () => {
  const byName = Object.fromEntries(fixture.vectors.map((v) => [v.name, v]))
  assert.equal(byName['duplicate-cards'].expected.snapshot_id, byName['three-cards'].expected.snapshot_id)
})

test('Decision 28: cards are hashed by CONTENT, not slug — an edit changes the address', () => {
  const before = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'x', summary: 'one' }] })
  const after  = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'x', summary: 'TWO' }] })
  assert.notEqual(before.snapshot_id, after.snapshot_id, 'a content edit must move the address')
  const sameSlugNewContent = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'y', summary: 'one' }] })
  assert.notEqual(before.snapshot_id, sameSlugNewContent.snapshot_id, 'slug is not the identity')
})

test('Decision 28: serialization is ALPHABETICAL, not the field-declaration order', () => {
  // The ruling's prose lists schema_version first; the canonical recipe uses
  // sort_keys=True. A port that followed the prose would fail this.
  const s = canonicalJson({ system_prompt_hash: 'sha256:' + 'a'.repeat(64), bootstrap_hash: 'sha256:' + 'b'.repeat(64), schema_version: 1, active_procedure_cards: [] })
  assert.equal(s.indexOf('active_procedure_cards') < s.indexOf('bootstrap_hash'), true, 'keys must be sorted')
  assert.equal(s.indexOf('bootstrap_hash') < s.indexOf('schema_version'), true)
  assert.equal(s.indexOf('schema_version') < s.indexOf('system_prompt_hash'), true)
  assert.equal(/\s/.test(s), false, 'compact separators: no whitespace')
})

test('Decision 28: non-ASCII stays literal (ensure_ascii=False)', () => {
  assert.equal(canonicalJson('建築'), '"建築"')
  const snap = buildDoctrineSnapshot({ systemPrompt: '建築', bootstrap: 'règles ✓', activeProcedureCards: [] })
  assert.ok(SHA256_RE.test(snap.system_prompt_hash), 'still a valid sha256: reference')
})

test('Decision 28: every produced address is a bare sha256: reference, never hex alone', () => {
  for (const v of fixture.vectors) {
    const snap = buildDoctrineSnapshot({
      systemPrompt: v.input.system_prompt, bootstrap: v.input.bootstrap,
      activeProcedureCards: v.input.active_procedure_cards,
    })
    for (const h of [snap.snapshot_id, snap.system_prompt_hash, snap.bootstrap_hash, snap.procedure_card_set_hash]) {
      assert.ok(SHA256_RE.test(h), `not a valid sha256: reference -> ${h}`)
    }
  }
})

test('Decision 28: an already-hashed reference passes through unchanged (idempotence)', () => {
  const ref = 'sha256:' + 'c'.repeat(64)
  assert.equal(contentHash(ref), ref, 're-hashing a reference would change the address')
})

test('Decision 28: procedure_card_set_hash is populated and card-set specific', () => {
  const a = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'a' }] })
  const b = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'b' }] })
  assert.ok(SHA256_RE.test(a.procedure_card_set_hash), 'card set hash must be populated')
  assert.notEqual(a.procedure_card_set_hash, b.procedure_card_set_hash, 'different cards, different set hash')
  const same = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [{ slug: 'a' }] })
  assert.equal(a.procedure_card_set_hash, same.procedure_card_set_hash, 'stable for the same cards')
})

test('Decision 28: bootstrap is part of the address, so a frame change yields a NEW frame', () => {
  const one = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'vetted-v1', activeProcedureCards: [] })
  const two = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'vetted-v2', activeProcedureCards: [] })
  assert.notEqual(one.snapshot_id, two.snapshot_id, 'governing text is part of the frame')
  // And the same text always yields the same frame — that is the stability B1/B3 relies on.
  const again = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'vetted-v1', activeProcedureCards: [] })
  assert.equal(one.snapshot_id, again.snapshot_id, 'same governing text must yield the same frame')
})

test('Decision 28: session/execution identity is EXCLUDED from the address', () => {
  const a = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [] })
  const b = buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: 'b', activeProcedureCards: [] })
  assert.equal(a.snapshot_id, b.snapshot_id, 'identical doctrine shares one address')
})

test('Decision 28: a card that is neither string nor object is rejected, not silently hashed', () => {
  for (const bad of [42, true, null, ['nested']]) {
    assert.throws(() => normalizeCards([bad]), /sequence of card contents|unsupported/i,
      `expected rejection for ${JSON.stringify(bad)}`)
  }
})

test('Decision 28: non-string prompt/bootstrap is rejected (python raises TypeError)', () => {
  assert.throws(() => buildDoctrineSnapshot({ systemPrompt: 42, bootstrap: 'b', activeProcedureCards: [] }), /must be strings/)
  assert.throws(() => buildDoctrineSnapshot({ systemPrompt: 'p', bootstrap: null, activeProcedureCards: [] }), /must be strings/)
})
