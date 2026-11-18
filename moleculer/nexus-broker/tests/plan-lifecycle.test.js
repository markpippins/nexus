/**
 * Plan-lifecycle submission gate — unit tests.
 *
 * The ledger is the authority, not this repo's mirror of it. `validateReceipt`
 * in typescript/conduit-mcp/src/receipts.ts permits PLAN_CREATE only from the
 * states its ALLOWED map lists (default-deny: `ALLOWED[current] || []`), and
 * refuses everything else with HTTP 400. These tests therefore:
 *
 *   1. parse the REAL ALLOWED map out of receipts.ts and require the broker's
 *      PLAN_CREATE_ALLOWED_FROM to equal the set of states that permit
 *      PLAN_CREATE — divergence in either direction fails here, not in a run;
 *   2. pin the live defect: a plan whose latest receipt is REVIEW_PASS must be
 *      refused up front with an actionable message;
 *   3. pin default-deny for states the map does not know.
 *
 * No network, no PG.
 */
const { test, describe } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const {
  PLAN_CREATE_ALLOWED_FROM,
  PLAN_CREATE_RECEIPT_TYPE,
  LATEST_PLAN_RECEIPT_SQL,
  normalizePlanReceiptType,
  isPlanCreatePermitted,
  decidePlanSubmission,
} = require('../lib/plan-lifecycle.ts')

const RECEIPTS_TS = path.join(
  __dirname, '..', '..', '..', 'typescript', 'conduit-mcp', 'src', 'receipts.ts',
)

/** Parse `const ALLOWED: Record<string, string[]> = { … }` out of receipts.ts. */
function ledgerAllowedMap() {
  const src = fs.readFileSync(RECEIPTS_TS, 'utf8')
  const start = src.indexOf('const ALLOWED')
  assert.notEqual(start, -1, 'ALLOWED map not found in receipts.ts')
  const open = src.indexOf('{', start)
  const close = src.indexOf('\n};', open)
  assert.ok(open !== -1 && close !== -1, 'ALLOWED map braces not found')
  const body = src.slice(open + 1, close)

  const map = {}
  for (const line of body.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('//')) continue
    const m = /^"?([A-Z_]*)"?\s*:\s*\[(.*?)\]/.exec(trimmed)
    if (!m) continue
    map[m[1]] = [...m[2].matchAll(/"([A-Z_]+)"/g)].map((x) => x[1])
  }
  return map
}

describe('mirror of the ledger transition map', () => {
  test('the parsed ledger map is non-trivial (guards the parser itself)', () => {
    const map = ledgerAllowedMap()
    assert.ok(Object.keys(map).length >= 15, `parsed only ${Object.keys(map).length} states`)
    assert.ok(map[''] && map['PLAN_CREATE'], 'fresh and PLAN_CREATE states must parse')
  })

  test('PLAN_CREATE_ALLOWED_FROM equals exactly the states the ledger lets PLAN_CREATE follow', () => {
    const map = ledgerAllowedMap()
    const permitted = Object.keys(map).filter((state) => map[state].includes(PLAN_CREATE_RECEIPT_TYPE))
    assert.deepEqual(
      [...PLAN_CREATE_ALLOWED_FROM].sort(),
      permitted.sort(),
      'broker plan-lifecycle table has drifted from receipts.ts ALLOWED',
    )
  })

  test('every ledger state decides identically to the ledger, including refusals', () => {
    const map = ledgerAllowedMap()
    for (const [state, targets] of Object.entries(map)) {
      const expected = targets.includes(PLAN_CREATE_RECEIPT_TYPE)
      assert.equal(
        isPlanCreatePermitted(state),
        expected,
        `state ${state || '(fresh)'}: ledger ${expected ? 'permits' : 'refuses'} PLAN_CREATE, broker disagrees`,
      )
    }
  })

  test('REVIEW_PASS is terminal in the ledger itself (the defect this gate closes)', () => {
    const map = ledgerAllowedMap()
    assert.deepEqual(map.REVIEW_PASS, [], 'REVIEW_PASS must allow no further receipts')
    assert.equal(isPlanCreatePermitted('REVIEW_PASS'), false)
  })
})

describe('normalizePlanReceiptType / isPlanCreatePermitted', () => {
  test('a plan with no receipts is fresh and accepts work', () => {
    for (const raw of [null, undefined, '', '   ']) {
      assert.equal(normalizePlanReceiptType(raw), null)
      assert.equal(isPlanCreatePermitted(raw), true)
    }
  })

  test('receipt types are case/whitespace normalized before deciding', () => {
    assert.equal(normalizePlanReceiptType(' review_pass '), 'REVIEW_PASS')
    assert.equal(isPlanCreatePermitted(' plan_create '), true)
    assert.equal(isPlanCreatePermitted(' review_pass '), false)
  })

  test('states the ledger does not know are refused (default-deny, mirroring ALLOWED[state] || [])', () => {
    for (const raw of ['REVIEWED', 'WHATEVER', 'plan_created', '0']) {
      assert.equal(isPlanCreatePermitted(raw), false, `${raw} must not be treated as permitted`)
    }
  })
})

describe('decidePlanSubmission', () => {
  const plan = 'f3608088-a2a5-4fbf-90de-57fad6880531'

  test('a fresh plan is allowed', () => {
    const d = decidePlanSubmission(plan, null)
    assert.equal(d.allowed, true)
    assert.equal(d.reason, 'PLAN_ACCEPTS_WORK')
    assert.equal(d.message, '')
  })

  test('a REVIEW_PASS (terminal) plan is refused with a 409 and an actionable message', () => {
    const d = decidePlanSubmission(plan, 'REVIEW_PASS')
    assert.equal(d.allowed, false)
    assert.equal(d.http_status, 409)
    assert.equal(d.reason, 'PLAN_STATE_REFUSED')
    assert.equal(d.latest_receipt_type, 'REVIEW_PASS')
    assert.match(d.message, new RegExp(plan))
    assert.match(d.message, /REVIEW_PASS/)
    assert.match(d.message, /terminal/)
    assert.match(d.message, /fresh plan|new wind task/)
    assert.match(d.message, /PLAN_CREATE is accepted only from/, 'message must name the states that do accept it')
  })

  test('a blocked plan (BLOCK must be requeued before PLAN_CREATE) is refused too', () => {
    const d = decidePlanSubmission(plan, 'BLOCK')
    assert.equal(d.allowed, false)
    assert.match(d.message, /REQUEUED/)
  })

  test('an in-flight plan (IMPLEMENTATION) is refused rather than double-started', () => {
    const d = decidePlanSubmission(plan, 'IMPLEMENTATION')
    assert.equal(d.allowed, false)
    assert.equal(d.message.length > 0, true)
  })

  test('a requeued/cancelled plan is allowed, because the ledger permits PLAN_CREATE from it', () => {
    for (const state of ['REQUEUED', 'CANCELLED', 'ABANDONED', 'HOLD', 'PLANNING', 'CRITIQUE_REJECT', 'PLAN_CREATE']) {
      assert.equal(decidePlanSubmission(plan, state).allowed, true, `${state} must be allowed`)
    }
  })
})

describe('ledger read', () => {
  test('reads the same view the ledger validates against, newest first', () => {
    assert.match(LATEST_PLAN_RECEIPT_SQL, /FROM nebula\.receipts_unified/)
    assert.match(LATEST_PLAN_RECEIPT_SQL, /WHERE plan_id = \$1/)
    assert.match(LATEST_PLAN_RECEIPT_SQL, /ORDER BY created_at DESC, id DESC/)
    assert.match(LATEST_PLAN_RECEIPT_SQL, /LIMIT 1/)
  })
})
