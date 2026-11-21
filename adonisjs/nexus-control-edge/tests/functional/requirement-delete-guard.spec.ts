import { test } from '@japa/runner'
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import pg from 'pg'

/**
 * P4a: the control edge's requirement/hierarchy mutations, against a real
 * Postgres database.
 *
 * This suite asserts HTTP route → controller → real `nebula` transaction →
 * database effects. The delete cases exercise the V222 guard and rollback; the
 * move cases exercise lifecycle updates and hierarchy re-parenting in the
 * canonical requirement table, not just the code's intent.
 *
 * What it pins:
 *   * a delete the guard refuses comes back as the service's conflict response
 *     (409), carrying the blocking requirement ids and their surfaces;
 *   * zero rows are removed for that refusal — including every child the
 *     cascade would have touched — so a refusal is never a partial delete;
 *   * a successful delete reports the number of requirements it removed;
 *   * no refusal is surfaced as a 500;
 *   * requirement status moves, feature/subsystem re-parenting and system
 *     demotion complete their intended writes without changing unrelated rows.
 *
 * The database is throwaway. CI provisions `ci_edge` from
 * sql/ci-bootstrap/nexus-ci-bootstrap.sql (see
 * .github/workflows/adonis-control-edge.yml); locally run with
 * PG_DB_NAME=ci_edge (or any disposable name containing `test`). The suite
 * refuses to run against the canonical `nexus` database — it only ever mutates
 * databases whose name says they are disposable.
 *
 * The schema is the real staged migrations, not a hand-built fixture: V220 (the
 * request↔requirement link substrate V222 depends on) and V222 (the port, the
 * compatibility projection and the delete guard) are applied from `sql/` the
 * first time the suite runs against a database that does not have them yet. The
 * test then creates its requirements through `nebula.requirements` — the same
 * transition surface every consumer writes — and deletes them through the HTTP
 * route, so the guard functions exercised are the ones the migration installs.
 */

const DB_NAME = process.env.PG_DB_NAME ?? ''

// Only disposable databases. `nexus` is the canonical database and must never
// be mutated by a test; a name that does not look disposable skips the suite.
const THROWAWAY =
  DB_NAME !== '' && DB_NAME !== 'nexus' && /(^|_)(ci|test)(_|$)/.test(DB_NAME)

const SKIP_REASON =
  `requirement-delete guard integration needs a throwaway database, but PG_DB_NAME is "${DB_NAME}"; ` +
  'set PG_DB_NAME to a disposable name such as ci_edge'

let db: pg.Client | undefined

function newClient(): pg.Client {
  return new pg.Client({
    host: process.env.PG_HOST,
    port: Number(process.env.PG_PORT),
    user: process.env.PG_USER,
    password: process.env.PG_PASSWORD,
    database: DB_NAME,
  })
}

async function applyGuardMigrations() {
  const client = newClient()
  await client.connect()
  try {
    const {
      rows: [row],
    } = await client.query(
      `SELECT to_regprocedure('resolution.requirement_delete_blockers(uuid,uuid[])') IS NOT NULL AS installed`
    )
    if (row.installed) return

    const repoRoot = new URL('../../../../', import.meta.url)
    const read = (name: string) => readFile(fileURLToPath(new URL(`sql/${name}`, repoRoot)), 'utf8')

    // V222's preflight requires the V220 link substrate; apply the real staged
    // files so the guard under test is the one the migration installs.
    await client.query(await read('V220__work_request_requirement_and_artifact_links.sql'))
    await client.query(await read('V222__port_requirements_to_resolution.sql'))

    const {
      rows: [installed],
    } = await client.query(
      `SELECT to_regprocedure('resolution.requirement_delete_blockers(uuid,uuid[])') IS NOT NULL AS installed`
    )
    if (!installed.installed) {
      throw new Error(
        'V222 did not install the requirement delete guard; the throwaway database is not in the expected state'
      )
    }
  } finally {
    await client.end()
  }
}

async function scalar(sql: string, params: any[] = []): Promise<number> {
  if (!db) throw new Error('test database client is not connected')
  const {
    rows: [row],
  } = await db.query(sql, params)
  return Number(row?.n ?? 0)
}

async function seedSystem(label: string): Promise<string> {
  const id = randomUUID()
  await db!.query(
    `INSERT INTO nebula.systems
       (id, name, description, created_at, recorded_on_dt, recorded_until_dt, valid_from, valid_until)
     VALUES ($1, $2, '', now(), now(), '9999-12-31 23:59:59+00', now(), '9999-12-31 23:59:59+00')`,
    [id, `p4a guard ${label} ${id.slice(0, 8)}`]
  )
  return id
}

async function seedSubsystem(systemId: string, label: string): Promise<string> {
  const id = randomUUID()
  await db!.query(
    `INSERT INTO nebula.subsystems
       (id, system_id, name, description, color, created_at,
        recorded_on_dt, recorded_until_dt, valid_from, valid_until)
     VALUES ($1, $2, $3, '', '#336699', now(), now(), '9999-12-31 23:59:59+00', now(), '9999-12-31 23:59:59+00')`,
    [id, systemId, `p4a ${label} ${id.slice(0, 8)}`]
  )
  return id
}

async function seedFeature(subsystemId: string, label: string): Promise<string> {
  const id = randomUUID()
  await db!.query(
    `INSERT INTO nebula.features
       (id, subsystem_id, name, description, created_at,
        recorded_on_dt, recorded_until_dt, valid_from, valid_until)
     VALUES ($1, $2, $3, '', now(), now(), '9999-12-31 23:59:59+00', now(), '9999-12-31 23:59:59+00')`,
    [id, subsystemId, `p4a ${label} ${id.slice(0, 8)}`]
  )
  return id
}

async function seedRequirement(opts: {
  systemId: string
  subsystemId?: string
  featureId?: string
  parentId?: string
  title: string
  status?: string
}): Promise<string> {
  // Through the transition surface: the projection's write-through puts the row
  // on resolution.requirement, which is what the guard reads.
  const {
    rows: [row],
  } = await db!.query(
    `INSERT INTO nebula.requirements (system_id, subsystem_id, feature_id, parent_id, title, status)
     VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`,
    [
      opts.systemId,
      opts.subsystemId ?? null,
      opts.featureId ?? null,
      opts.parentId ?? null,
      opts.title,
      opts.status ?? 'ToDo',
    ]
  )
  return String(row.id)
}

async function seedSpecification(requirementId: string): Promise<string> {
  const {
    rows: [row],
  } = await db!.query(
    `INSERT INTO resolution.specification (agenda_id, requirement_id, revision_number, revision_type)
     VALUES (gen_random_uuid(), $1, 1, 'created') RETURNING id`,
    [requirementId]
  )
  return String(row.id)
}

/** Best-effort cleanup of the rows a test created; the database is disposable. */
async function cleanup(ids: {
  systems?: string[]
  subsystems?: string[]
  features?: string[]
  requirements?: string[]
}) {
  if (!db) return
  const requirements = ids.requirements ?? []
  if (requirements.length > 0) {
    await db.query('DELETE FROM resolution.specification WHERE requirement_id = ANY($1::uuid[])', [requirements])
    await db.query('SELECT resolution.delete_requirements($1::uuid[])', [requirements])
  }
  if ((ids.features ?? []).length > 0) {
    await db.query('DELETE FROM nebula.features WHERE id = ANY($1::uuid[])', [ids.features])
  }
  if ((ids.subsystems ?? []).length > 0) {
    await db.query('DELETE FROM nebula.subsystems WHERE id = ANY($1::uuid[])', [ids.subsystems])
  }
  if ((ids.systems ?? []).length > 0) {
    await db.query('DELETE FROM nebula.work_sessions WHERE parent_id = ANY($1::text[])', [ids.systems])
    await db.query('DELETE FROM nebula.systems WHERE id = ANY($1::uuid[])', [ids.systems])
  }
}

function blockerIds(body: any): string[] {
  return (body?.blockers ?? []).map((entry: any) => String(entry.requirementId))
}

test.group('nebula hierarchy mutations + requirement delete guard (P4a) — real Postgres', (group) => {
  group.each.skip(!THROWAWAY, SKIP_REASON)

  group.setup(async () => {
    if (!THROWAWAY) return
    await applyGuardMigrations()
    db = newClient()
    await db.connect()
  })

  group.teardown(async () => {
    if (db) {
      await db.end()
      db = undefined
    }
  })

  test('POST /api/requirements/:id/move commits the canonical lifecycle status', async ({ client, assert }) => {
    const systemId = await seedSystem('requirement-move')
    const requirementId = await seedRequirement({ systemId, title: 'requirement move target' })

    try {
      const response = await client.post(`/api/requirements/${requirementId}/move`).json({
        targetStatus: 'InProgress',
        expectedCurrentStatus: 'ToDo',
      })

      response.assertStatus(200)
      assert.equal(response.body().id, requirementId)
      assert.equal(response.body().status, 'InProgress')
      const { rows: [requirement] } = await db!.query(
        'SELECT status FROM resolution.requirement WHERE id = $1',
        [requirementId]
      )
      assert.equal(requirement.status, 'InProgress')
    } finally {
      await cleanup({ systems: [systemId], requirements: [requirementId] })
    }
  })

  test('POST /api/features/move re-parents feature and its requirements atomically', async ({ client, assert }) => {
    const sourceSystemId = await seedSystem('feature-move-source')
    const targetSystemId = await seedSystem('feature-move-target')
    const sourceSubsystemId = await seedSubsystem(sourceSystemId, 'feature-move-source')
    const targetSubsystemId = await seedSubsystem(targetSystemId, 'feature-move-target')
    const featureId = await seedFeature(sourceSubsystemId, 'feature-move')
    const movedRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId: sourceSubsystemId,
      featureId,
      title: 'feature move requirement',
    })
    const unrelatedRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId: sourceSubsystemId,
      title: 'feature move unrelated requirement',
    })

    try {
      const response = await client.post('/api/features/move').json({
        featureId,
        targetSystemId,
        targetSubsystemId,
      })

      response.assertStatus(200)
      assert.deepEqual(response.body(), { ok: true })
      const { rows: [feature] } = await db!.query(
        'SELECT subsystem_id FROM nebula.features WHERE id = $1',
        [featureId]
      )
      assert.equal(feature.subsystem_id, targetSubsystemId)

      const { rows: [moved] } = await db!.query(
        'SELECT system_id, subsystem_id, feature_id FROM resolution.requirement WHERE id = $1',
        [movedRequirementId]
      )
      assert.equal(moved.system_id, targetSystemId)
      assert.equal(moved.subsystem_id, targetSubsystemId)
      assert.equal(moved.feature_id, featureId)

      const { rows: [unrelated] } = await db!.query(
        'SELECT system_id, subsystem_id, feature_id FROM resolution.requirement WHERE id = $1',
        [unrelatedRequirementId]
      )
      assert.equal(unrelated.system_id, sourceSystemId)
      assert.equal(unrelated.subsystem_id, sourceSubsystemId)
      assert.isNull(unrelated.feature_id)
    } finally {
      await cleanup({
        systems: [sourceSystemId, targetSystemId],
        subsystems: [sourceSubsystemId, targetSubsystemId],
        features: [featureId],
        requirements: [movedRequirementId, unrelatedRequirementId],
      })
    }
  })

  test('POST /api/subsystems/move reparents subsystem and every scoped requirement', async ({
    client,
    assert,
  }) => {
    const sourceSystemId = await seedSystem('subsystem-move-source')
    const targetSystemId = await seedSystem('subsystem-move-target')
    const subsystemId = await seedSubsystem(sourceSystemId, 'subsystem-move')
    const featureId = await seedFeature(subsystemId, 'subsystem-move')
    const subsystemRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId,
      title: 'subsystem-level move requirement',
    })
    const featureRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId,
      featureId,
      title: 'feature-level subsystem move requirement',
    })
    const unrelatedRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      title: 'subsystem move unrelated requirement',
    })

    try {
      const response = await client.post('/api/subsystems/move').json({ subsystemId, targetSystemId })

      response.assertStatus(200)
      assert.deepEqual(response.body(), { ok: true })
      const { rows: [subsystem] } = await db!.query(
        'SELECT system_id FROM nebula.subsystems WHERE id = $1',
        [subsystemId]
      )
      assert.equal(subsystem.system_id, targetSystemId)

      const { rows: movedRequirements } = await db!.query(
        'SELECT id, system_id, subsystem_id, feature_id FROM resolution.requirement WHERE id = ANY($1::uuid[])',
        [[subsystemRequirementId, featureRequirementId]]
      )
      assert.equal(movedRequirements.length, 2)
      for (const requirement of movedRequirements) {
        assert.equal(requirement.system_id, targetSystemId)
        assert.equal(requirement.subsystem_id, subsystemId)
      }
      assert.equal(
        movedRequirements.find((requirement: any) => requirement.id === subsystemRequirementId)?.feature_id,
        null
      )
      assert.equal(
        movedRequirements.find((requirement: any) => requirement.id === featureRequirementId)?.feature_id,
        featureId
      )

      const { rows: [unrelated] } = await db!.query(
        'SELECT system_id, subsystem_id, feature_id FROM resolution.requirement WHERE id = $1',
        [unrelatedRequirementId]
      )
      assert.equal(unrelated.system_id, sourceSystemId)
      assert.isNull(unrelated.subsystem_id)
      assert.isNull(unrelated.feature_id)
    } finally {
      await cleanup({
        systems: [sourceSystemId, targetSystemId],
        subsystems: [subsystemId],
        features: [featureId],
        requirements: [subsystemRequirementId, featureRequirementId, unrelatedRequirementId],
      })
    }
  })

  test('POST /api/systems/demote/:id creates destination hierarchy and preserves requirement ids', async ({
    client,
    assert,
  }) => {
    const sourceSystemId = await seedSystem('system-demote-source')
    const targetSystemId = await seedSystem('system-demote-target')
    const sourceSubsystemId = await seedSubsystem(sourceSystemId, 'system-demote')
    const sourceFeatureId = await seedFeature(sourceSubsystemId, 'system-demote')
    const systemRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      title: 'system-level demotion requirement',
      status: 'InProgress',
    })
    const subsystemRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId: sourceSubsystemId,
      title: 'subsystem-level demotion requirement',
      status: 'Blocked',
    })
    const featureRequirementId = await seedRequirement({
      systemId: sourceSystemId,
      subsystemId: sourceSubsystemId,
      featureId: sourceFeatureId,
      title: 'feature-level demotion requirement',
      status: 'Done',
    })
    let newSubsystemId: string | undefined
    let newFeatureId: string | undefined

    try {
      const response = await client.post(`/api/systems/demote/${sourceSystemId}`).json({ targetSystemId })

      response.assertStatus(200)
      assert.equal(response.body().ok, true)
      newSubsystemId = response.body().newSubsystemId
      assert.isString(newSubsystemId)
      assert.notEqual(newSubsystemId, sourceSubsystemId)
      const { rows: [newSubsystem] } = await db!.query(
        'SELECT system_id, name FROM nebula.subsystems WHERE id = $1',
        [newSubsystemId]
      )
      assert.equal(newSubsystem.system_id, targetSystemId)
      assert.equal(newSubsystem.name, `p4a guard system-demote-source ${sourceSystemId.slice(0, 8)}`)

      const { rows: [newFeature] } = await db!.query(
        'SELECT id, subsystem_id, name FROM nebula.features WHERE subsystem_id = $1',
        [newSubsystemId]
      )
      assert.exists(newFeature)
      newFeatureId = newFeature.id
      assert.equal(newFeature.subsystem_id, newSubsystemId)
      assert.equal(newFeature.name, `p4a system-demote ${sourceSubsystemId.slice(0, 8)}`)

      const { rows: [movedSystemRequirement] } = await db!.query(
        'SELECT id, system_id, subsystem_id, feature_id, status FROM resolution.requirement WHERE id = $1',
        [systemRequirementId]
      )
      assert.equal(movedSystemRequirement.id, systemRequirementId)
      assert.equal(movedSystemRequirement.status, 'InProgress')
      assert.equal(movedSystemRequirement.system_id, targetSystemId)
      assert.equal(movedSystemRequirement.subsystem_id, newSubsystemId)
      assert.isNull(movedSystemRequirement.feature_id)

      const { rows: [movedSubsystemRequirement] } = await db!.query(
        'SELECT id, system_id, subsystem_id, feature_id, status FROM resolution.requirement WHERE id = $1',
        [subsystemRequirementId]
      )
      assert.equal(movedSubsystemRequirement.id, subsystemRequirementId)
      assert.equal(movedSubsystemRequirement.status, 'Blocked')
      assert.equal(movedSubsystemRequirement.system_id, targetSystemId)
      assert.equal(movedSubsystemRequirement.subsystem_id, newSubsystemId)
      assert.equal(movedSubsystemRequirement.feature_id, newFeatureId)

      const { rows: [movedFeatureRequirement] } = await db!.query(
        'SELECT id, system_id, subsystem_id, feature_id, status FROM resolution.requirement WHERE id = $1',
        [featureRequirementId]
      )
      assert.equal(movedFeatureRequirement.id, featureRequirementId)
      assert.equal(movedFeatureRequirement.status, 'Done')
      assert.equal(movedFeatureRequirement.system_id, targetSystemId)
      assert.equal(movedFeatureRequirement.subsystem_id, newSubsystemId)
      assert.equal(movedFeatureRequirement.feature_id, newFeatureId)
      // Check by the saved source subsystem ID: checking through the deleted
      // parent would miss orphaned feature rows if the schema has no cascade.
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM nebula.features WHERE subsystem_id = $1', [sourceSubsystemId]),
        0
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE id = $1', [sourceSubsystemId]), 0)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE system_id = $1', [sourceSystemId]), 0)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.systems WHERE id = $1', [sourceSystemId]), 0)
    } finally {
      await cleanup({
        systems: [sourceSystemId, targetSystemId],
        subsystems: [sourceSubsystemId, ...(newSubsystemId ? [newSubsystemId] : [])],
        features: [sourceFeatureId, ...(newFeatureId ? [newFeatureId] : [])],
        requirements: [systemRequirementId, subsystemRequirementId, featureRequirementId],
      })
    }
  })

  test('DELETE /api/requirements/:id refuses a referenced requirement with 409 + blockers', async ({ 
    client,
    assert,
  }) => {
    const systemId = await seedSystem('req-refused')
    const requirementId = await seedRequirement({ systemId, title: 'refused requirement' })
    const specificationId = await seedSpecification(requirementId)

    try {
      const response = await client.delete(`/api/requirements/${requirementId}`)

      response.assertStatus(409)
      const body = response.body()
      assert.equal(body.code, 'requirement_delete_blocked')
      assert.deepEqual(blockerIds(body), [requirementId])
      assert.deepEqual(body.blockers[0].blockers.specifications, [specificationId])

      // Nothing removed: the requirement and the specification still exist.
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = $1', [requirementId]),
        1
      )
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.specification WHERE id = $1', [specificationId]),
        1
      )
    } finally {
      await cleanup({ systems: [systemId], requirements: [requirementId] })
    }
  })

  test('DELETE /api/features/:id refuses the whole cascade and removes no rows', async ({ client, assert }) => {
    const systemId = await seedSystem('feature-refused')
    const subsystemId = await seedSubsystem(systemId, 'feature-refused')
    const featureId = await seedFeature(subsystemId, 'feature-refused')

    const parentId = await seedRequirement({ systemId, subsystemId, featureId, title: 'cascade parent' })
    const childId = await seedRequirement({
      systemId,
      subsystemId,
      featureId,
      parentId,
      title: 'cascade child',
    })
    const blockedId = await seedRequirement({ systemId, subsystemId, featureId, title: 'cascade blocked' })
    const specificationId = await seedSpecification(blockedId)
    const created = [parentId, childId, blockedId]

    try {
      const response = await client.delete(`/api/features/${featureId}`)

      response.assertStatus(409)
      const body = response.body()
      assert.equal(body.code, 'requirement_delete_blocked')
      // The blocked requirement is named; its siblings are not "blockers" —
      // they are part of the set the cascade would have removed.
      assert.deepEqual(blockerIds(body), [blockedId])
      assert.deepEqual(body.blockers[0].blockers.specifications, [specificationId])

      // No partial cascade: every requirement the feature delete would have
      // touched is still there, and so is the feature itself.
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = ANY($1::uuid[])', [created]),
        3
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.features WHERE id = $1', [featureId]), 1)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE id = $1', [subsystemId]), 1)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.systems WHERE id = $1', [systemId]), 1)
    } finally {
      await cleanup({ systems: [systemId], subsystems: [subsystemId], features: [featureId], requirements: created })
    }
  })

  test('DELETE /api/subsystems/:id refuses the whole cascade and removes no rows', async ({ client, assert }) => {
    const systemId = await seedSystem('subsystem-refused')
    const subsystemId = await seedSubsystem(systemId, 'subsystem-refused')
    const featureId = await seedFeature(subsystemId, 'subsystem-refused')
    const requirementId = await seedRequirement({ systemId, subsystemId, featureId, title: 'subsystem blocked' })
    await seedSpecification(requirementId)

    try {
      const response = await client.delete(`/api/subsystems/${subsystemId}`)

      response.assertStatus(409)
      assert.equal(response.body().code, 'requirement_delete_blocked')
      assert.deepEqual(blockerIds(response.body()), [requirementId])
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = $1', [requirementId]),
        1
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE id = $1', [subsystemId]), 1)
    } finally {
      await cleanup({ systems: [systemId], subsystems: [subsystemId], features: [featureId], requirements: [requirementId] })
    }
  })

  test('DELETE /api/systems/:id refuses the widest cascade without removing any descendants', async ({
    client,
    assert,
  }) => {
    const systemId = await seedSystem('system-refused')
    const subsystemId = await seedSubsystem(systemId, 'system-refused')
    const featureId = await seedFeature(subsystemId, 'system-refused')
    const parentId = await seedRequirement({ systemId, title: 'system refusal parent' })
    const childId = await seedRequirement({
      systemId,
      subsystemId,
      featureId,
      parentId,
      title: 'system refusal child',
    })
    const blockedId = await seedRequirement({
      systemId,
      subsystemId,
      featureId,
      title: 'system refusal blocked',
    })
    const specificationId = await seedSpecification(blockedId)
    const created = [parentId, childId, blockedId]
    const {
      rows: [session],
    } = await db!.query(
      `INSERT INTO nebula.work_sessions (parent_id, parent_type, parent_name, context)
       VALUES ($1, 'system', 'system refusal session', 'must survive refusal') RETURNING id`,
      [systemId]
    )

    try {
      const response = await client.delete(`/api/systems/${systemId}`)

      response.assertStatus(409)
      const body = response.body()
      assert.equal(body.code, 'requirement_delete_blocked')
      assert.deepEqual(blockerIds(body), [blockedId])
      assert.deepEqual(body.blockers[0].blockers.specifications, [specificationId])

      // The system cascade crosses every hierarchy level and work sessions.
      // The blocker must abort the transaction before any descendant disappears.
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = ANY($1::uuid[])', [created]),
        3
      )
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.specification WHERE id = $1', [specificationId]),
        1
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.features WHERE id = $1', [featureId]), 1)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE id = $1', [subsystemId]), 1)
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.systems WHERE id = $1', [systemId]), 1)
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM nebula.work_sessions WHERE id = $1', [session.id]),
        1
      )
    } finally {
      await db!.query('DELETE FROM nebula.work_sessions WHERE id = $1', [session.id])
      await cleanup({
        systems: [systemId],
        subsystems: [subsystemId],
        features: [featureId],
        requirements: created,
      })
    }
  })

  test('DELETE /api/requirements/:id reports what it removed', async ({ client, assert }) => {
    const systemId = await seedSystem('req-deleted')
    const requirementId = await seedRequirement({ systemId, title: 'deletable requirement' })

    try {
      const response = await client.delete(`/api/requirements/${requirementId}`)

      response.assertStatus(200)
      assert.deepEqual(response.body(), { ok: true, deletedRequirements: 1 })
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = $1', [requirementId]),
        0
      )
    } finally {
      await cleanup({ systems: [systemId] })
    }
  })

  test('DELETE /api/subsystems/:id cascades its requirements and reports the count', async ({ client, assert }) => {
    const systemId = await seedSystem('subsystem-deleted')
    const subsystemId = await seedSubsystem(systemId, 'subsystem-deleted')
    const featureId = await seedFeature(subsystemId, 'subsystem-deleted')
    const parentId = await seedRequirement({ systemId, subsystemId, featureId, title: 'subsystem parent' })
    const childId = await seedRequirement({
      systemId,
      subsystemId,
      featureId,
      parentId,
      title: 'subsystem child',
    })

    try {
      const response = await client.delete(`/api/subsystems/${subsystemId}`)

      response.assertStatus(200)
      assert.deepEqual(response.body(), { ok: true, deletedRequirements: 2 })
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = ANY($1::uuid[])', [
          [parentId, childId],
        ]),
        0
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.subsystems WHERE id = $1', [subsystemId]), 0)
    } finally {
      await cleanup({ systems: [systemId], features: [featureId] })
    }
  })

  test('DELETE /api/systems/:id cascades every requirement it owns and reports the count', async ({
    client,
    assert,
  }) => {
    const systemId = await seedSystem('system-deleted')
    const subsystemId = await seedSubsystem(systemId, 'system-deleted')
    const featureId = await seedFeature(subsystemId, 'system-deleted')
    const parentId = await seedRequirement({ systemId, subsystemId, featureId, title: 'system parent' })
    const childId = await seedRequirement({
      systemId,
      subsystemId,
      featureId,
      parentId,
      title: 'system child',
    })
    const looseId = await seedRequirement({ systemId, title: 'system loose' })

    try {
      const response = await client.delete(`/api/systems/${systemId}`)

      response.assertStatus(200)
      assert.deepEqual(response.body(), { ok: true, deletedRequirements: 3 })
      assert.equal(
        await scalar('SELECT count(*)::int AS n FROM resolution.requirement WHERE id = ANY($1::uuid[])', [
          [parentId, childId, looseId],
        ]),
        0
      )
      assert.equal(await scalar('SELECT count(*)::int AS n FROM nebula.systems WHERE id = $1', [systemId]), 0)
    } finally {
      await cleanup({ systems: [systemId], subsystems: [subsystemId], features: [featureId] })
    }
    })
})
