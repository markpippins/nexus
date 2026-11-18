#!/usr/bin/env node
/**
 * Set aside historical test rows still present in the keychain outbox.
 *
 * Why
 * ---
 * The keychain outbox is production's broadcast queue: the broker claims a row
 * with SKIP LOCKED, delivers it into a keychain checkpoint, and marks it
 * delivered. The harness used to write its seed rows into THIS table (it now
 * has its own via KEYCHAIN_OUTBOX_TABLE), and eighteen such rows are still in
 * production's queue — all of them already `delivered`, some of them delivered
 * by a broker that wrote the resulting checkpoints into the LIVE keychains
 * store.
 *
 * They are not claimable today (the claim predicate takes `pending`, `failed`
 * and stale `delivering` only), but "not currently claimable" is not a state
 * anyone can audit: a delivered test row is indistinguishable from a delivered
 * real event, and any operator retry that sets it back to `pending` puts it
 * straight back into the broadcast path. This tool gives them a durable,
 * legible and reversible disposition instead.
 *
 * What it does
 * ------------
 * For every row matching the historical test signature, it moves the row to
 * `not_applicable` (an existing, allowed state the claim predicate excludes)
 * and writes the evidence into `last_error`: when it was set aside, what state
 * it was in, how many delivery attempts it had, and why. Nothing is deleted and
 * no column is invented — so the schema, the CI fixture and the canonical
 * bootstrap stay exactly as they are, and the fixture-vs-canonical check keeps
 * its authority.
 *
 * Reversibility
 * -------------
 * `--restore` puts every quarantined row back in the state recorded in its own
 * note and clears the note. Round-tripping is lossless, which is why the note
 * records the prior status rather than just saying "quarantined".
 *
 * Idempotent: matching rows already at `not_applicable` (or already annotated)
 * are skipped, so running it twice changes nothing and re-running is safe.
 *
 * Usage
 * -----
 *   node bin/outbox-quarantine.js                 # dry run (default): what would change
 *   node bin/outbox-quarantine.js --apply         # set the rows aside
 *   node bin/outbox-quarantine.js --restore --apply
 *   node bin/outbox-quarantine.js --table=resolution_test.probe_1 --apply   # tests
 *   node bin/outbox-quarantine.js --reason="..."  # override the recorded reason
 *
 * Env: PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_DB_NAME (falls back to the
 * broker's .env, i.e. whatever the deployment actually uses).
 */
const path = require('node:path')

require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') })

const PRODUCTION_TABLE = 'resolution.keychain_event_outbox'

/**
 * The historical test signature — the same four prefixes (and the actor
 * fallback) the harness's leak guard has always counted. It is deliberately
 * static rather than run-scoped: this tool's job is the residue of past runs,
 * long after those runs' own prefixes have been forgotten. Keep it in step with
 * PRODUCTION_TEST_SIGNATURE in tests/helpers/isolated-mongo.js; the unit test
 * asserts the two agree.
 */
const HISTORICAL_TEST_SIGNATURE =
  "(source_namespace ~ '^(keychain-test-|sol-e2e-|broker-smoke|keychains-)' " +
  "OR actor ~ '^broker-smoke')"

const QUARANTINE_STATUS = 'not_applicable'
const QUARANTINE_MARKER = 'quarantined '
const DEFAULT_REASON =
  'historical test-harness outbox row (pre-KEYCHAIN_OUTBOX_TABLE); set aside so it can ' +
  'never be claimed by the broadcast path'

/** `schema.table`, validated — the name is interpolated into SQL. */
function splitTable(table) {
  const match = /^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$/.exec(String(table).trim())
  if (!match) throw new Error(`table must be schema.table, got ${JSON.stringify(table)}`)
  return { schema: match[1], table: match[2] }
}

/** The evidence string that makes a set-aside row auditable from the row itself. */
function buildNote({ at, previousStatus, deliveryAttempts, reason }) {
  const when = at instanceof Date ? at.toISOString() : new Date(at).toISOString()
  return `${QUARANTINE_MARKER}${when} (was: ${previousStatus}, delivery_attempts=${deliveryAttempts}): ${reason}`
}

/** Recover the prior status from a note this tool wrote; null when unparseable. */
function parsePreviousStatus(note) {
  const match = /\(was: ([a-z_]+), delivery_attempts=\d+\)/.exec(String(note || ''))
  return match ? match[1] : null
}

function makePool(overrides = {}) {
  const { Pool } = require('pg')
  return new Pool({
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database: process.env.PG_DB_NAME || 'nexus',
    ...overrides,
  })
}

/**
 * Core operation, shared by the CLI and the tests.
 *
 * Returns `{ table, at, dryRun, restore, matched, changed, rows }` where each
 * row carries the id, the status it had, the status it now has, and the note.
 * Nothing is written when `dryRun` is true.
 */
async function quarantineRows(pool, options = {}) {
  const {
    table = PRODUCTION_TABLE,
    dryRun = true,
    restore = false,
    reason = DEFAULT_REASON,
    at = new Date(),
  } = options
  const { schema, table: name } = splitTable(table)
  const target = `${schema}.${name}`

  const client = await pool.connect()
  try {
    await client.query('BEGIN')
    const selected = restore
      ? await client.query(
          `SELECT id, source_namespace, source_event_id, actor, checkpoint_status,
                  delivery_attempts, last_error, recorded_at
             FROM ${target}
            WHERE ${HISTORICAL_TEST_SIGNATURE}
              AND checkpoint_status = $1
              AND last_error LIKE $2
            ORDER BY recorded_at, id
            FOR UPDATE`,
          [QUARANTINE_STATUS, `${QUARANTINE_MARKER}%`],
        )
      : await client.query(
          `SELECT id, source_namespace, source_event_id, actor, checkpoint_status,
                  delivery_attempts, last_error, recorded_at
             FROM ${target}
            WHERE ${HISTORICAL_TEST_SIGNATURE}
              AND checkpoint_status <> $1
            ORDER BY recorded_at, id
            FOR UPDATE`,
          [QUARANTINE_STATUS],
        )

    const rows = []
    for (const row of selected.rows) {
      if (restore) {
        const previous = parsePreviousStatus(row.last_error)
        if (!previous) {
          rows.push({ ...row, action: 'skipped', why: 'note does not record a prior status' })
          continue
        }
        rows.push({ ...row, from: row.checkpoint_status, to: previous, action: dryRun ? 'would-restore' : 'restored' })
        if (!dryRun) {
          await client.query(`UPDATE ${target} SET checkpoint_status = $1, last_error = NULL WHERE id = $2`, [
            previous,
            row.id,
          ])
        }
      } else {
        const note = buildNote({
          at,
          previousStatus: row.checkpoint_status,
          deliveryAttempts: row.delivery_attempts,
          reason,
        })
        rows.push({ ...row, from: row.checkpoint_status, to: QUARANTINE_STATUS, note, action: dryRun ? 'would-quarantine' : 'quarantined' })
        if (!dryRun) {
          await client.query(`UPDATE ${target} SET checkpoint_status = $1, last_error = $2 WHERE id = $3`, [
            QUARANTINE_STATUS,
            note,
            row.id,
          ])
        }
      }
    }
    if (dryRun) await client.query('ROLLBACK')
    else await client.query('COMMIT')

    return {
      table: target,
      at: at instanceof Date ? at.toISOString() : new Date(at).toISOString(),
      dryRun,
      restore,
      matched: rows.length,
      changed: rows.filter((row) => row.action !== 'skipped').length,
      rows,
    }
  } catch (err) {
    await client.query('ROLLBACK').catch(() => undefined)
    throw err
  } finally {
    client.release()
  }
}

function formatReport(report) {
  const lines = [
    `table:   ${report.table}`,
    `mode:    ${report.restore ? 'restore' : 'quarantine'}${report.dryRun ? ' (dry run — nothing written)' : ''}`,
    `matching rows: ${report.matched}`,
  ]
  for (const row of report.rows) {
    lines.push(
      `  ${row.id}  ${String(row.from).padEnd(15)} -> ${String(row.to).padEnd(14)} ` +
        `${row.source_namespace} | ${row.actor || '-'} | recorded ${new Date(row.recorded_at).toISOString()}` +
        (row.why ? `  [skipped: ${row.why}]` : ''),
    )
  }
  lines.push(report.dryRun ? 'nothing written; re-run with --apply' : `rows changed: ${report.changed}`)
  return lines.join('\n')
}

async function main(argv = process.argv.slice(2)) {
  const flag = (name) => argv.includes(`--${name}`)
  const value = (name, fallback) => {
    const hit = argv.find((arg) => arg.startsWith(`--${name}=`))
    return hit ? hit.slice(name.length + 3) : fallback
  }
  const dryRun = !flag('apply')
  const restore = flag('restore')
  const table = value('table', PRODUCTION_TABLE)
  const reason = value('reason', DEFAULT_REASON)

  const pool = makePool()
  try {
    const report = await quarantineRows(pool, { table, dryRun, restore, reason })
    if (flag('json')) console.log(JSON.stringify(report, null, 2))
    else console.log(formatReport(report))
    if (report.matched === 0) {
      console.log(restore ? 'nothing to restore' : 'nothing to quarantine (already set aside)')
    }
    return 0
  } finally {
    await pool.end().catch(() => undefined)
  }
}

if (require.main === module) {
  main()
    .then((code) => process.exit(code))
    .catch((err) => {
      console.error(`outbox-quarantine failed: ${err.message}`)
      process.exit(1)
    })
}

module.exports = {
  PRODUCTION_TABLE,
  HISTORICAL_TEST_SIGNATURE,
  QUARANTINE_STATUS,
  QUARANTINE_MARKER,
  DEFAULT_REASON,
  buildNote,
  parsePreviousStatus,
  splitTable,
  makePool,
  quarantineRows,
  formatReport,
}
