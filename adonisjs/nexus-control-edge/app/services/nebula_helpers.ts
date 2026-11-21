import db from '@adonisjs/lucid/services/db'
import type { QueryClientContract, TransactionClientContract } from '@adonisjs/lucid/types/database'
import { randomUUID } from 'node:crypto'

/**
 * nebula-srv re-homing (Wave 3.1) shared helpers.
 *
 * The original Express service ran a pg Pool with `-c search_path=nebula`
 * and `$n` placeholders. The edge runs Lucid (knex) on a named `nebula`
 * connection (searchPath nebula,public) where placeholders are `?`.
 *
 * `q()` converts `$n` → `?` at runtime — each occurrence of `$n` becomes
 * its own `?` with the value pushed once per occurrence (knex has no
 * placeholder reuse, so `$1` appearing twice must be bound twice). This
 * lets the port keep the upstream SQL verbatim.
 */

export type DbLike = QueryClientContract | TransactionClientContract

/** Convert pg-style $n placeholders to knex ? placeholders (expanding repeats). */
export function toKnex(sql: string, params: any[] = []): { sql: string; values: any[] } {
  const values: any[] = []
  const converted = sql.replace(/\$(\d+)/g, (_m, n: string) => {
    const idx = parseInt(n, 10) - 1
    values.push(params[idx])
    return '?'
  })
  // No $n placeholders found — the SQL already uses knex ? placeholders,
  // so forward the caller's bindings unchanged.
  if (converted === sql) {
    return { sql: converted, values: params }
  }
  return { sql: converted, values }
}

/** Run a raw query on a named Lucid connection (default: nebula). */
export async function q(sql: string, params: any[] = [], conn = 'nebula') {
  const { sql: c, values } = toKnex(sql, params)
  return db.connection(conn).rawQuery(c, values)
}

/** Run a raw query on a transaction client. */
export async function qT(trx: DbLike, sql: string, params: any[] = []) {
  const { sql: c, values } = toKnex(sql, params)
  return trx.rawQuery(c, values)
}

/**
 * Adapt a Lucid connection or transaction to the requirement-delete guard's
 * executor shape. The guard module is framework-free on purpose, so the same
 * file (and the same SQL) is carried by nebula-srv, moleculer/nebula and this
 * service; only this adapter is Adonis-specific.
 */
export function asQueryable(client: DbLike) {
  return {
    query: async (sql: string, params: any[] = []) => {
      const result: any = await qT(client, sql, params)
      return { rows: result?.rows ?? [], rowCount: result?.rowCount ?? 0 }
    },
  }
}

// ── Pagination ────────────────────────────────────────────────────────
export function parsePagination(query: Record<string, any>): {
  offset: number
  limit: number
  page: number
  pageSize: number
} {
  const rawLimit = query.limit !== undefined ? parseInt(String(query.limit), 10) : NaN
  const rawOffset = query.offset !== undefined ? parseInt(String(query.offset), 10) : NaN
  if (!isNaN(rawLimit) || !isNaN(rawOffset)) {
    const limit = Math.min(100, Math.max(1, isNaN(rawLimit) ? 100 : rawLimit))
    const offset = Math.max(0, isNaN(rawOffset) ? 0 : rawOffset)
    return { offset, limit, page: Math.floor(offset / limit) + 1, pageSize: limit }
  }
  const page = Math.max(1, parseInt(String(query.page || '1'), 10))
  const pageSize = Math.min(100, Math.max(1, parseInt(String(query.pageSize || '100'), 10)))
  return { offset: (page - 1) * pageSize, limit: pageSize, page, pageSize }
}

// ── Row mappers ───────────────────────────────────────────────────────
export function toEpochMs(row: any, ...cols: string[]): any {
  const out = { ...row }
  for (const col of cols) {
    if (out[col] && typeof out[col] === 'object' && out[col].getTime) {
      out[col] = out[col].getTime()
    }
  }
  return out
}

/** Convert snake_case DB row keys to camelCase and Date values to epoch ms */
export function camelCaseRow(row: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {}
  for (const [key, value] of Object.entries(row)) {
    const camelKey = key.replace(/_([a-z])/g, (_m, c: string) => c.toUpperCase())
    if (value instanceof Date) {
      out[camelKey] = value.getTime()
    } else {
      out[camelKey] = value
    }
  }
  return out
}

export function isUuid(v: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)
}

// ── Status Normalization (Plan 0132) ──────────────────────────────────
export const STATUS_CANONICAL = new Set([
  'Backlog', 'ToDo', 'InProgress', 'Active',
  'Blocked', 'Done', 'Cancelled', 'Accepted',
])
const STATUS_NORMALIZATION: Record<string, string> = {
  backlog: 'Backlog',
  todo: 'ToDo',
  inprogress: 'InProgress',
  active: 'Active',
  blocked: 'Blocked',
  done: 'Done',
  cancelled: 'Cancelled',
  accepted: 'Accepted',
  'to-do': 'ToDo',
  'to do': 'ToDo',
  'in progress': 'InProgress',
  'in-progress': 'InProgress',
  in_progress: 'InProgress',
  cancel: 'Cancelled',
  canceled: 'Cancelled',
  accept: 'Accepted',
  complete: 'Done',
  completed: 'Done',
  resolved: 'Done',
  wip: 'InProgress',
  new: 'Backlog',
}
export function normalizeStatus(input: string | null | undefined): string | null {
  if (input === undefined || input === null) return null
  const key = String(input).trim().toLowerCase()
  if (!key) return null
  return STATUS_NORMALIZATION[key] ?? null
}

export const REQ_TYPES = ['Epic', 'Story', 'Task', 'Bug'] as const

// ── Color palette (matches client) ────────────────────────────────────
const COLOR_PALETTE = [
  '#EF4444', '#F97316', '#F59E0B', '#10B981', '#06B6D4',
  '#3B82F6', '#6366F1', '#8B5CF6', '#EC4899', '#F43F5E',
  '#84CC16', '#14B8A6',
]

export async function getUnusedColor(systemId: string): Promise<string> {
  const { rows } = await q('SELECT color FROM subsystems WHERE system_id = ?', [systemId])
  const used = new Set(rows.map((r: any) => r.color))
  for (const c of COLOR_PALETTE) {
    if (!used.has(c)) return c
  }
  return COLOR_PALETTE[0]
}

// ── Plan cross-reference helpers (must run inside a transaction) ─────
export function hasPlanRef(planRef: any): boolean {
  return planRef !== undefined && planRef !== null && String(planRef).trim() !== ''
}

export async function createSpawnsPlanCrossRef(
  trx: DbLike,
  candidateId: string,
  planRef: string | null | undefined,
  extraMetadata?: Record<string, any>,
): Promise<any | null> {
  if (!hasPlanRef(planRef)) return null
  const planRefStr = String(planRef).trim()
  const { rows: [xref] } = await qT(
    trx,
    `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
     SELECT 'harvest_candidate', ?, 'plan', ?, 'ag:spawns_plan', ?
     WHERE NOT EXISTS (
       SELECT 1 FROM nebula.cross_references_history
       WHERE source_type = 'harvest_candidate'
         AND source_id = ?
         AND target_type = 'plan'
         AND target_id = ?
         AND rel_type = 'ag:spawns_plan'
         AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
     )
     ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
       WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
     DO NOTHING
     RETURNING *`,
    [candidateId, planRefStr, JSON.stringify(extraMetadata || {}), candidateId, planRefStr]
  )
  return xref || null
}

export async function upsertHarvestContextTab(
  trx: DbLike,
  systemId: string,
  candidate: { harvest_id: string; title: string; status: string | null; id: string; intent_description: string },
) {
  const { rows: [harvest] } = await qT(trx, 'SELECT source_filename FROM nebula.harvests WHERE id = ?', [candidate.harvest_id])

  const tabContent = [
    `## Harvest: ${candidate.title}`,
    '',
    `**Source:** ${harvest?.source_filename || candidate.harvest_id}`,
    `**Status:** ${candidate.status || 'unlinked'}`,
    `**Candidate ID:** ${candidate.id}`,
    '',
    '### Intent',
    '',
    candidate.intent_description,
  ].join('\n')

  await qT(
    trx,
    `UPDATE nebula.system_info_tabs_history
     SET recorded_until_dt = NOW()
     WHERE system_id = ? AND tab_id = 'harvest_context'
       AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
    [systemId]
  )
  await qT(
    trx,
    `INSERT INTO nebula.system_info_tabs_history
     (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
     VALUES (?, 'harvest_context', ?, NOW(), '9999-12-31 23:59:59+00')`,
    [systemId, tabContent]
  )
}

// ── Filesystem paths (same roots as the original service) ────────────
export const NEXUS_ROOT = '/home/codex/dev/nexus'
export const AUDIT_ROOT = '/home/codex/dev/nexus/audit'

// ── Misc ──────────────────────────────────────────────────────────────
export { randomUUID }
