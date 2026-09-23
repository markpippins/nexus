import type { HttpContext } from '@adonisjs/core/http'
import db from '@adonisjs/lucid/services/db'
import { q, qT, camelCaseRow, parsePagination, randomUUID } from '../services/nebula_helpers.js'

/**
 * nebula-srv (Wave 3.1) — conduit / execution / role-leases domain.
 * Ported from nexus/typescript/nebula-srv/src/routes.ts sections:
 * CONDUIT, EXECUTION AUTHORITY, ROLE LEASES.
 */

function err(e: any, status = 500) {
  return { status, body: { error: e?.message ?? String(e) } }
}

const VALID_TRANSITIONS: Record<string, string[]> = {
  DRAFT:     ['COMPILED', 'CANCELLED'],
  COMPILED:  ['VALIDATED', 'CANCELLED'],
  VALIDATED: ['ADMITTED', 'CANCELLED'],
  ADMITTED:  ['READY', 'CANCELLED'],
  READY:     ['COMPLETED', 'FAILED', 'CANCELLED'],
  COMPLETED: [],
  FAILED:    [],
  CANCELLED: [],
}

/**
 * Route tags for role-lease lifecycle agent records (revoked / swept /
 * exhausted). wr-conf / leased-builder conformance suites create synthetic
 * test leases (roles like `wr-conf-002` / `wr_conf_*`, models like `test/...`);
 * their lifecycle records must NOT tag real interactive roles — that floods
 * real-role inboxes (R17 pointer checks) with harness noise (to-do 41505e71).
 * Synthetic leases are routed to a `to:wr-conf-observer` tag instead; the
 * domain tags (`type:*`, `role:*`, `domain:*`) and the record itself (audit
 * trail) are unchanged. Mirrors roleLeaseRecordTags in nebula-srv routes.ts.
 */
function roleLeaseRecordTags(baseTags: string[], role: string, model: string | null | undefined): string[] {
  const synthetic =
    /^wr[_-]conf/i.test(role) ||
    (typeof model === 'string' && model.trim().startsWith('test/'))
  if (!synthetic) return baseTags
  const REAL_TO =
    /^to:(architect|engineer|engineer-ii|engineer-iii|planner|reviewer|analyst|devops|topologist|inspector|critic)$/
  const domain = baseTags.filter((t) => !REAL_TO.test(t))
  return [...domain, 'to:wr-conf-observer']
}

export default class NebulaConduitController {
  // ── CONDUIT — plan history & point-in-time queries ──────────────────

  /** GET /api/conduit/plans */
  async listConduitPlans({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const includeDeleted = qs.includeDeleted === 'true'
      const asOf = qs.asOf as string | undefined
      const statusFilter = qs.status as string | undefined
      const limit = Math.min(parseInt(qs.limit as string) || 100, 500)
      const offset = parseInt(qs.offset as string) || 0

      let sql: string
      const params: any[] = []
      let i = 1

      if (asOf) {
        sql = `SELECT p.*,
          (
            SELECT r.type FROM vision.receipts r
            WHERE r.plan_id = p.id
              AND r.created_at <= $${i}
            ORDER BY r.created_at DESC LIMIT 1
          ) AS derived_status_at_time
          FROM nebula.plans p
          WHERE 1=1`
        i++
        params.push(asOf)

        if (!includeDeleted) {
          sql += ` AND p.deleted = 0`
        }
        if (statusFilter) {
          sql += ` AND (SELECT r.type FROM vision.receipts r
            WHERE r.plan_id = p.id
              AND r.created_at <= $1
            ORDER BY r.created_at DESC LIMIT 1) = $${i}`
          i++
          params.push(statusFilter)
        }
        sql += ` ORDER BY p.created_at DESC LIMIT $${i} OFFSET $${i+1}`
        params.push(limit, offset)
      } else {
        sql = `SELECT * FROM nebula.plan_status ps WHERE 1=1`
        if (!includeDeleted) {
          sql += ` AND ps.deleted = 0`
        }
        if (statusFilter) {
          sql += ` AND ps.derived_status = $${i}`
          i++
          params.push(statusFilter)
        }
        sql += ` ORDER BY ps.created_at DESC LIMIT $${i} OFFSET $${i+1}`
        params.push(limit, offset)
      }

      const { rows } = await q(sql, params)
      response.json({ plans: rows, count: rows.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/conduit/plans/as-of */
  async conduitPlansAsOf({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const timestamp = qs.timestamp as string
      if (!timestamp) {
        response.status(400).json({ error: 'timestamp query parameter is required (ISO 8601)' })
        return
      }
      const includeDeleted = qs.includeDeleted === 'true'

      const { rows } = await q(
        `SELECT p.*,
          (
            SELECT r.type FROM vision.receipts r
            WHERE r.plan_id = p.id AND r.created_at <= ?
            ORDER BY r.created_at DESC LIMIT 1
          ) AS derived_status_at_time,
          (
            SELECT r.created_at FROM vision.receipts r
            WHERE r.plan_id = p.id AND r.created_at <= ?
            ORDER BY r.created_at DESC LIMIT 1
          ) AS last_receipt_at_time
          FROM nebula.plans p
          WHERE (p.created_at <= ? OR p.updated_at <= ?)
          ${includeDeleted ? '' : 'AND p.deleted = 0'}
          ORDER BY p.created_at DESC`,
        [timestamp, timestamp, timestamp, timestamp]
      )
      response.json({ timestamp, plans: rows, count: rows.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/conduit/plans/:id/history */
  async conduitPlanHistory({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [plan] } = await q('SELECT * FROM nebula.plans WHERE id = ?', [id])
      if (!plan) {
        response.status(404).json({ error: `Plan ${id} not found` })
        return
      }

      const { rows: receipts } = await q('SELECT * FROM vision.receipts WHERE plan_id = ? ORDER BY created_at ASC', [id])
      const { rows: tickets } = await q('SELECT * FROM vision.tickets WHERE plan_id = ? ORDER BY created_at ASC', [id])
      const { rows: [tokenUsage] } = await q(
        'SELECT COALESCE(SUM(tokens_used), 0) AS total_tokens, COUNT(*) AS receipt_count FROM vision.receipts WHERE plan_id = ?',
        [id]
      )
      const { rows: sessions } = await q(
        'SELECT s.id, s.agent_role, s.start_iso, s.end_iso, s.model, s.exit_code, s.workflow_id FROM conduit.sessions s WHERE s.id IN (SELECT DISTINCT r.session_id FROM vision.receipts r WHERE r.plan_id = ? AND r.session_id IS NOT NULL) ORDER BY s.start_iso ASC',
        [id]
      )

      response.json({
        plan,
        receipts,
        tickets,
        sessions,
        tokenUsage: { totalTokens: tokenUsage?.total_tokens ?? 0, receiptCount: tokenUsage?.receipt_count ?? 0 },
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/conduit/plans/:id/receipts */
  async conduitPlanReceipts({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows } = await q('SELECT * FROM vision.receipts WHERE plan_id = ? ORDER BY created_at ASC', [id])
      response.json({ planId: id, receipts: rows, count: rows.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/conduit/deleted-plans */
  async conduitDeletedPlans({ request, response }: HttpContext) {
    try {
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q('SELECT * FROM nebula.plans WHERE deleted = 1 ORDER BY updated_at DESC LIMIT ? OFFSET ?', [pageSize, offset]),
        q('SELECT COUNT(*)::int AS total FROM nebula.plans WHERE deleted = 1'),
      ])
      response.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── EXECUTION AUTHORITY (ADR-006) ───────────────────────────────────

  /** POST /api/execution/requests */
  async createExecutionRequest({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const {
        businessKey, title, intentType, objective, inputs,
        deterministic, maxRetries, timeoutPolicy, resourceHints,
        opTrace, status, sourcePlanId, sourceWrId,
      } = body

      if (!businessKey) {
        await trx.rollback()
        response.status(400).json({ error: 'businessKey is required' })
        return
      }

      const { rows: [row] } = await qT(
        trx,
        `INSERT INTO execution.requests (
          business_key, title, intent_type, objective, inputs,
          deterministic, max_retries, timeout_policy, resource_hints,
          op_trace, status, source_plan_id, source_wr_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING *`,
        [
          businessKey, title || '', intentType || 'task', objective || '',
          inputs || {}, deterministic ?? true, maxRetries || null,
          timeoutPolicy || null, resourceHints || [], opTrace || {},
          status || 'DRAFT', sourcePlanId || null, sourceWrId || null,
        ]
      )
      await trx.commit()
      response.status(201).json(row)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      if (e.code === '23505') {
        response.status(409).json({ error: `Request with business_key '${request.body().businessKey}' already exists` })
      } else {
        const { status, body } = err(e)
        response.status(status).json(body)
      }
    }
  }

  /** GET /api/execution/requests */
  async listExecutionRequests({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { status } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const filterParams: any[] = []
      let i = 1
      if (status) { clauses.push(`status = $${i++}`); filterParams.push(status) }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM execution.requests ${where} ORDER BY created_at DESC LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM execution.requests ${where}`, filterParams),
      ])

      response.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/execution/requests/:id */
  async getExecutionRequest({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows } = await q('SELECT * FROM execution.requests WHERE id = ?', [id])
      if (rows.length === 0) {
        response.status(404).json({ error: 'Request not found' })
        return
      }
      const { rows: leases } = await q('SELECT * FROM execution.leases WHERE request_id = ? ORDER BY acquired_at DESC', [id])
      const { rows: attempts } = await q('SELECT * FROM execution.attempts WHERE request_id = ? ORDER BY created_at DESC', [id])
      const { rows: receipts } = await q('SELECT * FROM execution.receipts WHERE request_id = ? ORDER BY issued_at DESC', [id])
      response.json({ ...rows[0], leases, attempts, receipts })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/execution/requests/:id/transition */
  async transitionExecutionRequest({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const body = request.body()
      const { targetStatus, reason } = body

      const { rows: [current] } = await qT(trx, 'SELECT * FROM execution.requests WHERE id = ?', [id])
      if (!current) {
        await trx.rollback()
        response.status(404).json({ error: 'Request not found' })
        return
      }

      const allowed = VALID_TRANSITIONS[current.status] || []
      if (!allowed.includes(targetStatus)) {
        await trx.rollback()
        response.status(400).json({ error: `Invalid transition: ${current.status} → ${targetStatus}`, allowed })
        return
      }

      const { rows: [updated] } = await qT(
        trx,
        'UPDATE execution.requests SET status = ?, updated_at = NOW() WHERE id = ? RETURNING *',
        [targetStatus, id]
      )

      await trx.commit()
      response.json({ previous: current.status, request: updated, reason: reason || null })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/execution/leases/acquire */
  async acquireExecutionLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { requestId, executorId, ttlSeconds } = body

      if (!requestId || !executorId) {
        await trx.rollback()
        response.status(400).json({ error: 'requestId and executorId are required' })
        return
      }

      const { rows: [reqRow] } = await qT(trx, 'SELECT * FROM execution.requests WHERE id = ?', [requestId])
      if (!request) {
        await trx.rollback()
        response.status(404).json({ error: 'Request not found' })
        return
      }
      if (!['ADMITTED', 'READY'].includes(reqRow.status)) {
        await trx.rollback()
        response.status(400).json({ error: `Request must be ADMITTED or READY to lease (current: ${reqRow.status})` })
        return
      }

      const { rows: existing } = await qT(trx, "SELECT id FROM execution.leases WHERE request_id = ? AND status = 'ACTIVE'", [requestId])
      if (existing.length > 0) {
        await trx.rollback()
        response.status(409).json({ error: 'Active lease already exists for this request', existingLeaseId: existing[0].id })
        return
      }

      const ttl = ttlSeconds || 300
      const { rows: [lease] } = await qT(
        trx,
        `INSERT INTO execution.leases (request_id, executor_id, ttl_seconds, expires_at)
         VALUES (?, ?, ?, NOW() + (? || ' seconds')::interval) RETURNING *`,
        [requestId, executorId, ttl, String(ttl)]
      )

      await trx.commit()
      response.status(201).json(lease)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/execution/leases/:id/renew */
  async renewExecutionLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const { ttlSeconds } = request.body()
      const ttl = ttlSeconds || 300

      const { rows: [lease] } = await qT(trx, 'SELECT * FROM execution.leases WHERE id = ?', [id])
      if (!lease) {
        await trx.rollback()
        response.status(404).json({ error: 'Lease not found' })
        return
      }
      if (lease.status !== 'ACTIVE') {
        await trx.rollback()
        response.status(400).json({ error: `Cannot renew lease in status '${lease.status}' (must be ACTIVE)` })
        return
      }
      if (new Date(lease.expires_at) < new Date()) {
        await qT(trx, "UPDATE execution.leases SET status = 'EXPIRED' WHERE id = ?", [id])
        await trx.commit()
        response.status(400).json({ error: 'Lease has already expired' })
        return
      }

      const { rows: [updated] } = await qT(
        trx,
        `UPDATE execution.leases
         SET ttl_seconds = ?, expires_at = NOW() + (? || ' seconds')::interval
         WHERE id = ? AND status = 'ACTIVE' RETURNING *`,
        [ttl, String(ttl), id]
      )

      await trx.commit()
      response.json(updated)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/execution/leases/:id/release */
  async releaseExecutionLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()

      const { rows: [lease] } = await qT(trx, 'SELECT * FROM execution.leases WHERE id = ?', [id])
      if (!lease) {
        await trx.rollback()
        response.status(404).json({ error: 'Lease not found' })
        return
      }
      if (lease.status !== 'ACTIVE') {
        await trx.rollback()
        response.status(400).json({ error: `Cannot release lease in status '${lease.status}' (must be ACTIVE)` })
        return
      }

      const { rows: [updated] } = await qT(
        trx,
        "UPDATE execution.leases SET status = 'RELEASED', released_at = NOW() WHERE id = ? RETURNING *",
        [id]
      )

      await trx.commit()
      response.json(updated)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── ROLE LEASES (RoleLeases / plan 1286) ────────────────────────────

  /** POST /api/role-leases/issue */
  async issueRoleLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { role, channel, model, ttlSeconds, budgetUnits, windowEnd } = body

      if (!role) {
        await trx.rollback()
        response.status(400).json({ error: 'role is required' })
        return
      }

      const { rows: existing } = await qT(trx, "SELECT id FROM tackle.role_leases WHERE role = ? AND status = 'ACTIVE'", [role])
      if (existing.length > 0) {
        await trx.rollback()
        response.status(409).json({ error: 'Active role lease already exists', existingLeaseId: existing[0].id })
        return
      }

      const ttl = ttlSeconds ?? 3600
      const windowEndTs = windowEnd
        ? new Date(windowEnd)
        : new Date(Date.now() + ttl * 1000)
      if (windowEndTs.getTime() <= Date.now()) {
        await trx.rollback()
        response.status(400).json({ error: 'windowEnd/ttlSeconds must be in the future' })
        return
      }

      const { rows: [lease] } = await qT(
        trx,
        `INSERT INTO tackle.role_leases
           (role, channel, model, window_end, budget_units, expires_at)
         VALUES (?, ?, ?, ?, ?, ?) RETURNING *`,
        [role, channel || 'interactive', model || null, windowEndTs, budgetUnits ?? null, windowEndTs]
      )
      await trx.commit()
      response.status(201).json(lease)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/role-leases/:id/renew */
  async renewRoleLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const { ttlSeconds, budgetUnits } = request.body()

      const { rows: [lease] } = await qT(trx, 'SELECT * FROM tackle.role_leases WHERE id = ?', [id])
      if (!lease) {
        await trx.rollback()
        response.status(404).json({ error: 'Role lease not found' })
        return
      }
      if (lease.status !== 'ACTIVE') {
        await trx.rollback()
        response.status(400).json({ error: `Cannot renew role lease in status '${lease.status}' (must be ACTIVE)` })
        return
      }
      if (new Date(lease.expires_at) < new Date()) {
        await qT(trx, "UPDATE tackle.role_leases SET status = 'EXPIRED' WHERE id = ?", [id])
        await trx.commit()
        response.status(400).json({ error: 'Role lease has already expired' })
        return
      }

      const ttl = ttlSeconds ?? 3600
      const { rows: [updated] } = await qT(
        trx,
        `UPDATE tackle.role_leases
         SET window_end = GREATEST(window_end, NOW() + (? || ' seconds')::interval),
             expires_at = NOW() + (? || ' seconds')::interval,
             budget_units = COALESCE(?, budget_units),
             updated_at = NOW()
         WHERE id = ? AND status = 'ACTIVE' RETURNING *`,
        [ttl, ttl, budgetUnits ?? null, id]
      )
      await trx.commit()
      response.json(updated)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/role-leases/:id/revoke */
  async revokeRoleLease({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const { rows: [lease] } = await qT(trx, 'SELECT * FROM tackle.role_leases WHERE id = ?', [id])
      if (!lease) {
        await trx.rollback()
        response.status(404).json({ error: 'Role lease not found' })
        return
      }
      if (lease.status !== 'ACTIVE') {
        await trx.rollback()
        response.status(400).json({ error: `Cannot revoke role lease in status '${lease.status}' (must be ACTIVE)` })
        return
      }
      const { rows: [updated] } = await qT(
        trx,
        "UPDATE tackle.role_leases SET status = 'RELEASED', released_at = NOW(), updated_at = NOW() WHERE id = ? RETURNING *",
        [id]
      )
      await trx.commit()
      response.json(updated)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/role-leases */
  async listRoleLeases({ request, response }: HttpContext) {
    try {
      const qs = request.qs() as Record<string, string | undefined>
      const { role, status, limit } = qs
      const conds: string[] = []
      const vals: any[] = []
      if (role) { vals.push(role); conds.push(`role = $${vals.length}`) }
      if (status) { vals.push(status); conds.push(`status = $${vals.length}`) }
      const where = conds.length ? `WHERE ${conds.join(' AND ')}` : ''
      const { rows } = await q(
        `SELECT * FROM tackle.role_leases ${where} ORDER BY created_at DESC LIMIT $${vals.length + 1}`,
        [...vals, Number(limit) || 50]
      )
      response.json({ items: rows })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/cascade/subscriber-status */
  async cascadeSubscriberStatus({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT application_name, state, backend_start, pid
         FROM pg_stat_activity
         WHERE application_name = 'cascade-interactive-turn'
           AND datname = current_database()
         LIMIT 1`
      )
      const row = rows[0] || null
      response.json({
        up: !!row,
        state: row?.state ?? null,
        backendSince: row?.backend_start ? new Date(row.backend_start).toISOString() : null,
        backendPid: row?.pid ?? null,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/role-leases/stale */
  async staleRoleLeases({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT * FROM tackle.role_leases
         WHERE status = 'ACTIVE'
           AND (expires_at < NOW()
             OR (budget_units IS NOT NULL AND consumed_units >= budget_units))
         ORDER BY expires_at ASC`
      )
      response.json({ items: rows })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/role-leases/consume */
  async consumeRoleLease({ request, response }: HttpContext) {
    try {
      const { role } = request.body()
      if (!role) {
        response.status(400).json({ error: 'role is required' })
        return
      }
      const { rows } = await q(
        `UPDATE tackle.role_leases
         SET consumed_units = consumed_units + 1, updated_at = NOW()
         WHERE role = ? AND status = 'ACTIVE'
         RETURNING id, consumed_units, budget_units, window_end, channel, model`,
        [role]
      )
      if (rows.length === 0) {
        response.status(404).json({ error: `No ACTIVE lease for role '${role}'` })
        return
      }
      const lease = rows[0]
      const exhausted = lease.budget_units !== null && lease.consumed_units >= lease.budget_units

      if (exhausted) {
        const revoked = await q(
          `UPDATE tackle.role_leases SET status = 'RELEASED', updated_at = NOW()
           WHERE id = ? AND status = 'ACTIVE' RETURNING id`,
          [lease.id]
        )
        if (revoked.rows.length > 0) {
          const exhaustId = randomUUID()
          const now = new Date().toISOString()
          q(
            `INSERT INTO nebula.agent_records_history (id, record_type, role, title, content, tags, created_at, recorded_on_dt, model)
             VALUES (?::uuid, 'report', ?, ?, ?, ?, ?, ?, ?)`,
            [
              exhaustId,
              'architect',
              `Role-lease exhausted: ${role} (${lease.consumed_units}/${lease.budget_units})`,
              `## Role lease exhausted\n\n- **Role:** ${role}\n- **Channel:** ${lease.channel || 'unknown'}\n- **Model:** ${lease.model || 'unknown'}\n- **Consumed:** ${lease.consumed_units}/${lease.budget_units}\n- **Window end:** ${lease.window_end}\n- **Lease ID:** ${lease.id}\n\nThe lease has been auto-revoked. Issue a new lease to resume work.`,
              roleLeaseRecordTags(['type:lease-exhausted', 'to:architect', 'to:engineer', `role:${role}`], role, lease.model),
              now,
              now,
              lease.model || null
            ]
          ).catch(() => { /* best-effort */ })
        }
      }

      response.json({
        ok: true,
        consumed: lease.consumed_units,
        budget: lease.budget_units,
        exhausted,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/execution/attempts */
  async createExecutionAttempt({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { leaseId, status: attemptStatus, result, error: attemptError, exitCode } = body

      if (!leaseId) {
        await trx.rollback()
        response.status(400).json({ error: 'leaseId is required' })
        return
      }

      const { rows: [lease] } = await qT(trx, 'SELECT * FROM execution.leases WHERE id = ?', [leaseId])
      if (!lease) {
        await trx.rollback()
        response.status(404).json({ error: 'Lease not found' })
        return
      }
      if (lease.status !== 'ACTIVE') {
        await trx.rollback()
        response.status(400).json({ error: `Lease is not ACTIVE (current: ${lease.status})` })
        return
      }
      if (new Date(lease.expires_at) < new Date()) {
        await qT(trx, "UPDATE execution.leases SET status = 'EXPIRED' WHERE id = ?", [leaseId])
        await trx.commit()
        response.status(400).json({ error: 'Lease has expired' })
        return
      }

      const finalStatus = attemptStatus || 'SUCCEEDED'
      const now = new Date().toISOString()

      const { rows: [attempt] } = await qT(
        trx,
        `INSERT INTO execution.attempts (
          lease_id, request_id, executor_id, status,
          started_at, completed_at, result, error, exit_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [
          leaseId, lease.request_id, lease.executor_id, finalStatus,
          now, now, result || {}, attemptError || null, exitCode || null,
        ]
      )

      await trx.commit()
      response.status(201).json(attempt)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/execution/receipts */
  async issueExecutionReceipt({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { attemptId, type, agentRole, summary, metadata } = body

      if (!attemptId) {
        await trx.rollback()
        response.status(400).json({ error: 'attemptId is required' })
        return
      }

      const { rows: [attempt] } = await qT(trx, 'SELECT * FROM execution.attempts WHERE id = ?', [attemptId])
      if (!attempt) {
        await trx.rollback()
        response.status(404).json({ error: 'Attempt not found' })
        return
      }

      const receiptType = type || (attempt.status === 'SUCCEEDED' ? 'EXECUTION_COMPLETE' : 'EXECUTION_FAILED')

      const { rows: [receipt] } = await qT(
        trx,
        `INSERT INTO execution.receipts (
          attempt_id, request_id, type, agent_role, summary, metadata
        ) VALUES (?, ?, ?, ?, ?, ?) RETURNING *`,
        [attemptId, attempt.request_id, receiptType, agentRole || attempt.executor_id, summary || '', metadata || {}]
      )

      await trx.commit()
      response.status(201).json(receipt)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/execution/receipts */
  async listExecutionReceipts({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { requestId, type } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const filterParams: any[] = []
      let i = 1
      if (requestId) { clauses.push(`request_id = $${i++}`); filterParams.push(requestId) }
      if (type) { clauses.push(`type = $${i++}`); filterParams.push(type) }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM execution.receipts ${where} ORDER BY issued_at DESC LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM execution.receipts ${where}`, filterParams),
      ])

      response.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/execution/state */
  async executionState({ response }: HttpContext) {
    try {
      const { rows: reqs } = await q('SELECT status, count(*) as count FROM execution.requests GROUP BY status ORDER BY status')
      const { rows: leases } = await q('SELECT status, count(*) as count FROM execution.leases GROUP BY status ORDER BY status')
      const { rows: attempts } = await q('SELECT status, count(*) as count FROM execution.attempts GROUP BY status ORDER BY status')
      const { rows: [receiptTotal] } = await q('SELECT count(*) as total FROM execution.receipts')
      const { rows: receiptTypes } = await q('SELECT type, count(*) as count FROM execution.receipts GROUP BY type ORDER BY count DESC')
      response.json({
        requests: reqs,
        leases,
        attempts,
        receipts: { total: Number(receiptTotal.total), byType: receiptTypes },
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }
}
