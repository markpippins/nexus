import { test } from '@japa/runner'
import db from '@adonisjs/lucid/services/db'
import NebulaHierarchyController from '#controllers/nebula_hierarchy_controller'

/**
 * P4a: the control edge must refuse a guarded requirement delete exactly like
 * nebula-srv and moleculer/nebula — 409 with the requirement ids and their
 * blockers, never a 500, never a partial cascade — and report what a successful
 * delete removed.
 *
 * The DB is stubbed at the Lucid transaction boundary, so these are route-level
 * assertions on the controller actions without needing a live database: what
 * the action sends to the guard, in what order, and how it answers.
 */

const REQ = '804b3f87-fe84-4167-b5a7-15eaa35c2d4c'
const OTHER = '5fcf5826-b7ad-48bd-900a-c8ed54b95970'
const BLOCKED = '11111111-2222-4333-8444-555555555555'
const SPEC = '22222222-3333-4444-8555-666666666666'
const SYS = '99999999-9999-4999-8999-999999999991'
const FEAT = '77777777-6666-4555-8444-333333333333'

type State = {
  statements: string[]
  deleted: string[][]
  parents: string[]
  committed: boolean
  rolledBack: boolean
  /** The Lucid connection the action opened its transaction on. */
  connection?: string
}

type Options = {
  guardInstalled?: boolean
  blocked?: string[]
  throwOnDelete?: any
}

function fakeTransaction(state: State, opts: Options = {}) {
  const guardInstalled = opts.guardInstalled ?? true
  const blocked = new Set(opts.blocked ?? [])

  return {
    async rawQuery(sql: string, values: any[] = []) {
      const s = String(sql).replace(/\s+/g, ' ').trim()
      state.statements.push(s)

      if (s.includes('to_regprocedure')) {
        return { rows: [{ installed: guardInstalled }], rowCount: 1 }
      }
      if (s.startsWith('SELECT id FROM nebula.requirements WHERE')) {
        return { rows: [{ id: REQ }, { id: OTHER }, { id: BLOCKED }], rowCount: 3 }
      }
      if (s.includes('FROM unnest(')) {
        const ids = values[0] as string[]
        return {
          rows: ids
            .filter((id) => blocked.has(id))
            .map((id) => ({ id, blockers: { specifications: [SPEC] } })),
          rowCount: 1,
        }
      }
      if (s.includes('SELECT resolution.delete_requirements(')) {
        if (opts.throwOnDelete) throw opts.throwOnDelete
        state.deleted.push(values[0])
        return { rows: [{ deleted: (values[0] as string[]).length }], rowCount: 1 }
      }
      if (s.includes('DELETE FROM requirements WHERE id = ANY(')) {
        state.deleted.push(values[0])
        return { rows: (values[0] as string[]).map((id) => ({ id })), rowCount: 1 }
      }
      if (/DELETE FROM (systems|subsystems|features) WHERE id/.test(s)) {
        state.parents.push(s.split(' ')[2])
        return { rows: [], rowCount: 1 }
      }
      if (s.startsWith('DELETE FROM work_sessions')) {
        return { rows: [], rowCount: 0 }
      }
      throw new Error(`Unexpected query: ${s}`)
    },
    async commit() {
      state.committed = true
    },
    async rollback() {
      state.rolledBack = true
    },
  }
}

function fakeContext(id: string) {
  const response = {
    statusCode: 200,
    body: undefined as any,
    status(code: number) {
      this.statusCode = code
      return this
    },
    json(body: any) {
      this.body = body
      return this
    },
  }
  return {
    ctx: { request: { params: () => ({ id }) }, response } as any,
    response,
  }
}

const controller = new NebulaHierarchyController()
const originalConnection = (db as any).connection

/**
 * The delete actions must transact on the `nebula` connection (search_path
 * nebula,public) — the default `pg` connection carries search_path
 * knowledge,public, where `requirements`/`systems`/`features` do not resolve.
 * The integration suite (tests/functional/requirement-delete-guard.spec.ts)
 * proves that against Postgres; this stub records the connection the action
 * asked for so the mistake cannot come back unnoticed here.
 */
function stubDb(state: State, opts: Options = {}) {
  ;(db as any).connection = (name: string) => {
    state.connection = name
    return { transaction: async () => fakeTransaction(state, opts) }
  }
}

test.group('requirement delete guard (P4a) — control edge contract', (group) => {
  group.each.teardown(() => {
    ;(db as any).connection = originalConnection
  })

  test('refuses a referenced requirement with 409 + blockers (never 500)', async ({ assert }) => {
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state, { blocked: [BLOCKED] })
    const { ctx, response } = fakeContext(BLOCKED)

    await controller.deleteRequirement(ctx)

    assert.equal(state.connection, 'nebula')
    assert.equal(response.statusCode, 409)
    assert.equal(response.body.code, 'requirement_delete_blocked')
    assert.deepEqual(response.body.blockers, [
      { requirementId: BLOCKED, blockers: { specifications: [SPEC] } },
    ])
    assert.deepEqual(state.deleted, [])
    assert.isFalse(state.committed)
    assert.isTrue(state.rolledBack)
  })

  test('reports what a successful requirement delete removed', async ({ assert }) => {
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state)
    const { ctx, response } = fakeContext(REQ)

    await controller.deleteRequirement(ctx)

    assert.equal(state.connection, 'nebula')
    assert.equal(response.statusCode, 200)
    assert.deepEqual(response.body, { ok: true, deletedRequirements: 1 })
    assert.deepEqual(state.deleted, [[REQ]])
    assert.isTrue(state.committed)
  })

  test('refuses the whole feature cascade and deletes nothing', async ({ assert }) => {
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state, { blocked: [BLOCKED] })
    const { ctx, response } = fakeContext(FEAT)

    await controller.deleteFeature(ctx)

    assert.equal(state.connection, 'nebula')
    assert.equal(response.statusCode, 409)
    assert.equal(response.body.code, 'requirement_delete_blocked')
    assert.deepEqual(state.deleted, [])
    assert.deepEqual(state.parents, [])
    assert.isFalse(state.committed)
  })

  test('cascades a system\'s requirements before the system row', async ({ assert }) => {
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state)
    const { ctx, response } = fakeContext(SYS)

    await controller.deleteSystem(ctx)

    assert.equal(state.connection, 'nebula')
    assert.equal(response.statusCode, 200)
    assert.deepEqual(response.body, { ok: true, deletedRequirements: 3 })
    assert.deepEqual(state.deleted, [[REQ, OTHER, BLOCKED]])
    assert.deepEqual(state.parents, ['systems'])
    assert.isTrue(state.committed)
  })

  test('maps a racing guard refusal (P4A01) to 409 with the trigger detail', async ({ assert }) => {
    const err: any = new Error('P4A-DELETE-BLOCKED: requirement x still has dependants')
    err.code = 'P4A01'
    err.detail = JSON.stringify({ requirementId: REQ, blockers: { specifications: [SPEC] } })
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state, { throwOnDelete: err })
    const { ctx, response } = fakeContext(REQ)

    await controller.deleteRequirement(ctx)

    assert.equal(response.statusCode, 409)
    assert.equal(response.body.code, 'requirement_delete_blocked')
    assert.deepEqual(response.body.blockers, [
      { requirementId: REQ, blockers: { specifications: [SPEC] } },
    ])
  })

  test('maps a residual FK violation (23503) to 409, not 500', async ({ assert }) => {
    const err: any = new Error('update or delete violates foreign key constraint')
    err.code = '23503'
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state, { throwOnDelete: err })
    const { ctx, response } = fakeContext(REQ)

    await controller.deleteRequirement(ctx)

    assert.equal(response.statusCode, 409)
    assert.equal(response.body.code, 'requirement_delete_blocked')
  })

  test('still deletes through the transition view before V222 is applied', async ({ assert }) => {
    const state: State = { statements: [], deleted: [], parents: [], committed: false, rolledBack: false }
    stubDb(state, { guardInstalled: false })
    const { ctx, response } = fakeContext(REQ)

    await controller.deleteRequirement(ctx)

    assert.equal(response.statusCode, 200)
    assert.equal(response.body.deletedRequirements, 1)
    assert.deepEqual(state.deleted, [[REQ]])
  })
})