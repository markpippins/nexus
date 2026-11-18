/**
 * Fixture fidelity check: does the keychain-delta CI fixture still define the
 * outbox table the way the repo-canonical schema does?
 *
 * Why this exists
 * ---------------
 * `tests/fixtures/keychain-delta-ci-pg.sql` is the ONLY outbox schema the
 * hermetic CI job has, and the harness creates its test queue with
 * `CREATE TABLE ... (LIKE resolution.keychain_event_outbox INCLUDING ALL)`.
 * The fixture silently fell behind production (21 columns, bigserial id, no
 * `created_at`), the clone inherited the drift, and the broker's own
 * `/keychain-snapshot/status` 500'd — which the test reported as "broker did
 * not become healthy", i.e. the cheapest possible explanation for an
 * expensive-looking failure. The drift was only found by reading the broker's
 * captured stderr.
 *
 * This check is what should have failed instead: apply both definitions to
 * scratch databases and diff the table they produce. It needs no broker, no
 * Mongo, and two empty databases, so it runs in seconds before anything boots.
 *
 * What is compared
 * ----------------
 * The columns (`information_schema.columns`: name, order, type, nullability,
 * default — the same shape the provisioning tests reconcile against), the
 * indexes (uniqueness, key, predicate, with the name stripped) and the
 * constraints (`pg_get_constraintdef`, name stripped, so PRIMARY KEY, UNIQUE
 * and CHECK semantics are compared rather than their auto-generated names).
 *
 * Usage
 * -----
 *   node tests/verify-outbox-fixture.js            # via `npm run verify:outbox-fixture`
 *   node tests/verify-outbox-fixture.js --bootstrap=/path/to/schema.sql --fixture=/path/to/fixture.sql
 *
 * Env: PG_HOST / PG_PORT / PG_USER / PG_PASSWORD (an account that may CREATE
 * DATABASE; the scratch databases are dropped on the way out). The target
 * database itself is never read or written — `PG_DB_NAME` is deliberately
 * ignored so pointing this at production cannot touch production schema.
 *
 * Exit: 0 match, 1 mismatch or setup error, with the diff printed.
 */
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

// Same as the test files: the broker's .env supplies PG credentials locally.
// CI has no .env, and dotenv does not override variables already in the
// environment, so an explicit PG_PASSWORD always wins.
require('dotenv').config({ path: path.resolve(__dirname, '..', '.env') })

const { outboxShape, shapesMatch, describeShapeDiff } = require('./helpers/isolated-mongo')

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..')
const TABLE = 'resolution.keychain_event_outbox'

function argValue(name, fallback) {
  const hit = process.argv.find((arg) => arg.startsWith(`--${name}=`))
  return hit ? hit.slice(name.length + 3) : fallback
}

const BOOTSTRAP_SQL = path.resolve(argValue('bootstrap', path.join(REPO_ROOT, 'sql/ci-bootstrap/nexus-ci-bootstrap.sql')))
const FIXTURE_SQL = path.resolve(argValue('fixture', path.join(__dirname, 'fixtures/keychain-delta-ci-pg.sql')))

function pgConfig(database) {
  return {
    host: process.env.PG_HOST || 'localhost',
    port: Number(process.env.PG_PORT || 5432),
    user: process.env.PG_USER || 'pguser',
    password: process.env.PG_PASSWORD || 'pgpass',
    database,
  }
}

/** Index identity without its (DB- or dump-generated) name. */
async function indexShapes(client) {
  const res = await client.query(
    `SELECT indexdef FROM pg_indexes
      WHERE schemaname = $1 AND tablename = $2`,
    TABLE.split('.'),
  )
  return res.rows
    .map((row) => row.indexdef.replace(/^CREATE (UNIQUE )?INDEX \S+ ON /, 'CREATE $1INDEX ON '))
    .sort()
}

/** Constraint semantics without their generated names. */
async function constraintShapes(client) {
  const res = await client.query(
    `SELECT pg_get_constraintdef(oid) AS def
       FROM pg_constraint
      WHERE conrelid = $1::regclass
      ORDER BY def`,
    [TABLE],
  )
  return res.rows.map((row) => row.def)
}

async function apply(file, database, { Client }) {
  if (!fs.existsSync(file)) throw new Error(`SQL file not found: ${file}`)
  const client = new Client(pgConfig(database))
  await client.connect()
  try {
    await client.query(fs.readFileSync(file, 'utf8'))
    const present = await client.query('SELECT to_regclass($1) AS reg', [TABLE])
    if (!present.rows[0] || !present.rows[0].reg) {
      throw new Error(`${file} does not define ${TABLE}`)
    }
    return { columns: await outboxShape(client, TABLE), indexes: await indexShapes(client), constraints: await constraintShapes(client) }
  } finally {
    await client.end()
  }
}

async function main() {
  const { Client } = require('pg')
  const suffix = crypto.randomBytes(4).toString('hex')
  const bootstrapDb = `fixture_bootstrap_${suffix}`
  const fixtureDb = `fixture_candidate_${suffix}`
  const admin = new Client(pgConfig('postgres'))
  await admin.connect()

  const drop = async (name) => {
    await admin.query(`DROP DATABASE IF EXISTS ${name} WITH (FORCE)`).catch(() => undefined)
  }
  try {
    for (const name of [bootstrapDb, fixtureDb]) {
      await drop(name)
      await admin.query(`CREATE DATABASE ${name}`)
    }
    console.log(`[verify-outbox-fixture] canonical schema: ${BOOTSTRAP_SQL}`)
    console.log(`[verify-outbox-fixture] fixture:         ${FIXTURE_SQL}`)
    const canonical = await apply(BOOTSTRAP_SQL, bootstrapDb, { Client })
    const fixture = await apply(FIXTURE_SQL, fixtureDb, { Client })

    const problems = []
    if (!shapesMatch(canonical.columns, fixture.columns)) {
      const notes = describeShapeDiff(canonical.columns, fixture.columns)
      problems.push(
        `${TABLE} columns differ (canonical -> fixture):\n` +
        notes.map((note) => `  - ${note}`).join('\n'),
      )
    }
    const indexDiff = canonical.indexes.filter((def) => !fixture.indexes.includes(def))
    const extraIndexes = fixture.indexes.filter((def) => !canonical.indexes.includes(def))
    if (indexDiff.length || extraIndexes.length) {
      problems.push(
        `${TABLE} indexes differ:\n` +
        [...indexDiff.map((def) => `  - missing in fixture: ${def}`),
          ...extraIndexes.map((def) => `  - only in fixture: ${def}`)].join('\n'),
      )
    }
    const constraintDiff = canonical.constraints.filter((def) => !fixture.constraints.includes(def))
    const extraConstraints = fixture.constraints.filter((def) => !canonical.constraints.includes(def))
    if (constraintDiff.length || extraConstraints.length) {
      problems.push(
        `${TABLE} constraints differ:\n` +
        [...constraintDiff.map((def) => `  - missing in fixture: ${def}`),
          ...extraConstraints.map((def) => `  - only in fixture: ${def}`)].join('\n'),
      )
    }

    if (problems.length) {
      console.error('\n[verify-outbox-fixture] FIXTURE DRIFT')
      for (const problem of problems) console.error(problem)
      console.error(
        '\nThe hermetic CI job clones this fixture into the test queue with LIKE, so every' +
        '\nomission here becomes a missing column the broker queries against. Regenerate the' +
        `\ntable definition from ${path.relative(REPO_ROOT, BOOTSTRAP_SQL)} (or the live table)` +
        '\nand update tests/fixtures/keychain-delta-ci-pg.sql.',
      )
      process.exitCode = 1
      return
    }
    console.log(
      `[verify-outbox-fixture] OK: ${canonical.columns.length} columns, ` +
      `${canonical.indexes.length} indexes, ${canonical.constraints.length} constraints match`,
    )
  } finally {
    await drop(fixtureDb)
    await drop(bootstrapDb)
    await admin.end().catch(() => undefined)
  }
}

main().catch((err) => {
  console.error(`[verify-outbox-fixture] failed: ${err.message}`)
  process.exitCode = 1
})
