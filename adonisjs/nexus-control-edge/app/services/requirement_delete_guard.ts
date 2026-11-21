/**
 * P4a requirement-delete guard — the service side of "what does deleting a
 * requirement mean after V222".
 *
 * V222 makes `resolution.requirement` canonical and a real reference target:
 * specifications, implementation plans, segment sets, request links, work
 * requests, child requirements and live blueprint payloads all name it. The
 * database owns the ruling (`resolution.requirement_delete_blockers` plus a
 * BEFORE DELETE trigger on the canonical table, SQLSTATE P4A01). This module is
 * the one implementation of the service side: ask the guard before deleting a
 * set — so a system/feature cascade removes everything or nothing — then delete
 * leaf-first through `resolution.delete_requirements` inside the caller's
 * transaction, and turn a refusal into a 409 payload.
 *
 * This file is carried verbatim (modulo quote style) by every writing surface:
 *   typescript/nebula-srv/src/requirement-delete.ts
 *   moleculer/nebula/src/requirement-delete.ts
 *   adonisjs/nexus-control-edge/app/services/requirement_delete_guard.ts
 * `bin/tests/test_requirement_delete_guard_parity.py` fails when the SQL, the
 * conflict code or the success shape drifts between them.
 *
 * V222 is operator-gated, so the guard only exists once it is applied; the
 * module detects that (to_regprocedure) and falls back to the writable
 * transition view, so the routes work before and after the apply.
 */

export type RequirementDeleteBlocker = {
  requirementId: string
  blockers: Record<string, string[]>
}

export type RequirementDeleteRow = { rows: any[]; rowCount?: number | null }

/** A pg Pool, a pg PoolClient, or an adapter over a service's own client. */
export type Queryable = { query: (text: string, values?: any[]) => Promise<RequirementDeleteRow> }

/** The conflict discriminator every surface answers with. */
export const REQUIREMENT_DELETE_BLOCKED_CODE = 'requirement_delete_blocked'

/** Is the V222 guard installed? The migration is operator-gated. */
export const GUARD_PROBE_SQL =
  `SELECT to_regprocedure('resolution.requirement_delete_blockers(uuid,uuid[])') IS NOT NULL AS installed`

/** Requirements in the set that something still references, with the details. */
export const BLOCKERS_SQL =
  `SELECT wanted.id::text AS id,
            resolution.requirement_delete_blockers(wanted.id, $2::uuid[]) AS blockers
       FROM unnest($1::uuid[]) AS wanted (id)
      WHERE resolution.requirement_delete_blockers(wanted.id, $2::uuid[]) <> '{}'::jsonb`

/** Ordered (leaf-first) set delete through the canonical guard. */
export const DELETE_SET_SQL = 'SELECT resolution.delete_requirements($1::uuid[]) AS deleted'

/** Pre-V222: the transition view is still the writable requirement surface. */
export const FALLBACK_DELETE_SQL =
  'DELETE FROM requirements WHERE id = ANY($1::uuid[]) RETURNING id'

export class RequirementDeleteBlocked extends Error {
  blockers: RequirementDeleteBlocker[]

  constructor(message: string, blockers: RequirementDeleteBlocker[]) {
    super(message)
    this.name = 'RequirementDeleteBlocked'
    this.blockers = blockers
  }
}

export async function requirementDeleteGuardInstalled(db: Queryable): Promise<boolean> {
  const {
    rows: [row],
  } = await db.query(GUARD_PROBE_SQL)
  return Boolean(row?.installed)
}

/** Requirement ids in one hierarchy scope, read off the transition surface. */
export async function requirementIdsInScope(
  db: Queryable,
  scope: 'system_id' | 'subsystem_id' | 'feature_id',
  id: string
): Promise<string[]> {
  const { rows } = await db.query(`SELECT id FROM nebula.requirements WHERE ${scope} = $1`, [id])
  return rows.map((row: any) => String(row.id))
}

export async function blockedRequirements(
  db: Queryable,
  ids: string[]
): Promise<RequirementDeleteBlocker[]> {
  if (ids.length === 0) return []
  // The ids double as `p_also_deleting`, so a cascade does not report the
  // children it is removing in the same call as blockers.
  const { rows } = await db.query(BLOCKERS_SQL, [ids, ids])
  return rows.map((row: any) => ({ requirementId: row.id, blockers: row.blockers }))
}

export async function deleteRequirements(
  db: Queryable,
  ids: string[],
  guardInstalled: boolean
): Promise<number> {
  if (ids.length === 0) return 0
  if (guardInstalled) {
    // Leaf-first inside the database: parent_id is a self-FK, so an id list
    // cannot be trusted to remove children before parents.
    const {
      rows: [row],
    } = await db.query(DELETE_SET_SQL, [ids])
    return Number(row?.deleted ?? 0)
  }
  const { rows } = await db.query(FALLBACK_DELETE_SQL, [ids])
  return rows.length
}

/** Delete the set, or refuse with the surfaces that still reference it. */
export async function deleteRequirementSet(
  db: Queryable,
  ids: string[],
  scopeLabel: string
): Promise<number> {
  const guardInstalled = await requirementDeleteGuardInstalled(db)
  const blockers = guardInstalled ? await blockedRequirements(db, ids) : []
  if (blockers.length > 0) {
    throw new RequirementDeleteBlocked(
      `${scopeLabel} has requirements that are still referenced and cannot be deleted`,
      blockers
    )
  }
  return deleteRequirements(db, ids, guardInstalled)
}

/**
 * Map a refusal to its 409 payload. The trigger's own refusal (a writer that
 * referenced the requirement between the pre-check and the delete) and a
 * residual FK violation are conflicts, never server faults.
 */
export function requirementDeleteConflict(
  err: any
): { error: string; blockers: RequirementDeleteBlocker[] } | null {
  if (err instanceof RequirementDeleteBlocked) {
    return { error: err.message, blockers: err.blockers }
  }
  if (err?.code === 'P4A01') {
    let parsed: any = null
    try {
      parsed = err.detail ? JSON.parse(err.detail) : null
    } catch {
      parsed = null
    }
    const blockers = parsed?.requirementId
      ? [{ requirementId: String(parsed.requirementId), blockers: parsed.blockers ?? {} }]
      : []
    return { error: err.message, blockers }
  }
  if (err?.code === '23503') {
    return { error: 'A requirement in this delete is still referenced', blockers: [] }
  }
  return null
}

/** What a successful guarded delete reports: what it removed. */
export function requirementDeleteSuccess(deleted: number): {
  ok: true
  deletedRequirements: number
} {
  return { ok: true, deletedRequirements: deleted }
}