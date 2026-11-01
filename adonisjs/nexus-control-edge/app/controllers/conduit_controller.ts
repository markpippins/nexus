import type { HttpContext } from '@adonisjs/core/http'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { q } from '../services/nebula_helpers.js'
import { resolvePipelineDir } from '../lib/repo-root.js'

/**
 * conduit-srv (Wave 3.3) — workflows / tickets / tokens / config /
 * governance / vision / session-log / wr domains.
 * Ported from nexus/typescript/conduit-srv/src/routes/*.ts.
 * Queries run against the `conduit` named connection (search_path=
 * conduit,vision,peb,tackle) so unqualified table names resolve as
 * they did upstream. Routes are mounted at root (backward compat with
 * conduit-mcp consumers), matching the original service.
 */

// Same env-driven constants as the original service.
const CONDUIT_SCHEMA = process.env.CONDUIT_PG_SCHEMA || 'conduit'
const VISION_SCHEMA = 'vision'
const PEB_SCHEMA = 'peb'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

export default class ConduitController {
  // ── WORKFLOWS ─────────────────────────────────────────────────────

  /** GET /workflows */
  async listWorkflows(_ctx: HttpContext) {
    const { rows } = await q(
      `SELECT id, agent_role, start_iso, end_iso, is_running, plans_processed, pid, created_at
       FROM ${CONDUIT_SCHEMA}.sessions ORDER BY start_iso DESC`,
      [],
      'conduit'
    )
    const sessions = rows
    const active = sessions.filter((s: any) => s.is_running === 1 || s.is_running === true)
    const workflows = active.map((s: any) => {
      let planId = ''
      try {
        const plans = JSON.parse(s.plans_processed || '[]')
        if (Array.isArray(plans) && plans.length > 0) planId = plans[0]
      } catch {}
      return {
        workflowId: planId ? `plan-${planId}-${s.agent_role}` : s.id,
        runId: s.id,
        status: 'running',
        startTime: s.start_iso || s.created_at || null,
        closeTime: s.end_iso || null,
        planId,
        role: s.agent_role,
        pid: s.pid ?? null,
      }
    })
    const counts = { running: workflows.length, completed: 0, failed: 0, cancelled: 0, total: workflows.length }
    return { connected: true, counts, workflows }
  }

  // ── TICKETS ───────────────────────────────────────────────────────

  /** POST /tickets/detect */
  async detectTickets(_ctx: HttpContext) {
    const staleResult = await q(
      `UPDATE vision.tickets SET status = 'stale'
       WHERE status = 'claimed'
         AND last_activity IS NOT NULL
         AND last_activity < (NOW() - INTERVAL '6 hours')
       RETURNING id`,
      [],
      'conduit'
    )
    for (const row of staleResult.rows) {
      await q(
        `INSERT INTO kernel.transition_event
           (event_id, event_type, aggregate_type, aggregate_id, actor, authority, payload)
         VALUES ($1, 'transition.requested', 'ticket', $2, 'conduit-srv', 'system', $3::jsonb)`,
        [randomUUID(), row.id, JSON.stringify({ from_status: 'claimed', to_status: 'stale', reason: 'stale_detection' })],
        'conduit'
      )
    }
    const stale = staleResult.rows.length

    const expiredAffected = await q(
      `SELECT id, status FROM vision.tickets
       WHERE status IN ('open', 'claimed', 'stale')
         AND expires_at IS NOT NULL
         AND expires_at < NOW()`,
      [],
      'conduit'
    )
    if (expiredAffected.rows.length > 0) {
      await q(
        `UPDATE vision.tickets SET status = 'expired'
         WHERE id = ANY($1::text[])`,
        [expiredAffected.rows.map((r: any) => r.id)],
        'conduit'
      )
    }
    for (const row of expiredAffected.rows) {
      await q(
        `INSERT INTO kernel.transition_event
           (event_id, event_type, aggregate_type, aggregate_id, actor, authority, payload)
         VALUES ($1, 'transition.rejected', 'ticket', $2, 'conduit-srv', 'system', $3::jsonb)`,
        [randomUUID(), row.id, JSON.stringify({ from_status: row.status, to_status: 'expired', reason: 'expiry_detection' })],
        'conduit'
      )
    }
    const expired = expiredAffected.rows.length

    return {
      detected: true,
      stale,
      expired,
      timestamp: new Date().toISOString(),
    }
  }

  /** GET /tickets/lineage/:planId */
  async ticketLineage({ request, response }: HttpContext) {
    const { planId } = request.params()
    if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
      return response.status(400).json({ error: 'Invalid plan ID' })
    }
    const { rows } = await q(
      `SELECT id, role, status, tokens_used,
              parent_ticket_id, spawn_reason,
              replacement_of, closure_reason,
              created_at, closed_at
       FROM vision.tickets WHERE plan_id = $1
       ORDER BY created_at ASC`,
      [planId]
    )
    return { plan_id: planId, tickets: rows }
  }

  // ── TOKENS ────────────────────────────────────────────────────────

  /** GET /tokens/plan/:planId */
  async tokensByPlan({ request, response }: HttpContext) {
    const { planId } = request.params()
    if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
      return response.status(400).json({ error: 'Invalid plan ID' })
    }
    const { rows } = await q(
      `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
       FROM vision.receipts WHERE plan_id = $1`,
      [planId]
    )
    const row = rows[0]
    return { plan_id: planId, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 }
  }

  /** GET /tokens/role/:role */
  async tokensByRole({ request, response }: HttpContext) {
    const { role } = request.params()
    if (!['builder', 'reviewer', 'planner', 'critic'].includes(role)) {
      return response.status(400).json({ error: `Invalid role: ${role}` })
    }
    const { rows } = await q(
      `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
       FROM vision.receipts WHERE agent_role = $1`,
      [role]
    )
    const row = rows[0]
    return { role, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 }
  }

  /** GET /tokens/ticket/:ticketId */
  async tokensByTicket({ request, response }: HttpContext) {
    const { ticketId } = request.params()
    if (!/^[a-zA-Z0-9_-]+$/.test(ticketId)) {
      return response.status(400).json({ error: 'Invalid ticket ID' })
    }
    const { rows } = await q(
      `SELECT COALESCE(tokens_used, 0) as tokens_used
       FROM vision.tickets WHERE id = $1`,
      [ticketId]
    )
    const row = rows[0]
    return { ticket_id: ticketId, tokens_used: row?.tokens_used ?? 0 }
  }

  // ── CONFIG ────────────────────────────────────────────────────────

  /** GET /config/cron */
  async getCron(_ctx: HttpContext) {
    const PIPELINE_CRON = process.env.PIPELINE_CRON || '*/3'
    const match = PIPELINE_CRON.match(/^\*\/(\d+)$/)
    const intervalMinutes = match ? parseInt(match[1], 10) : 3
    return {
      cron: PIPELINE_CRON,
      intervalMinutes,
      description: `Every ${intervalMinutes} minute${intervalMinutes === 1 ? '' : 's'}`,
      timestamp: new Date().toISOString(),
    }
  }

  /** GET /config/failure-recovery
   *
   * Wave 3.5 consolidation: this path was claimed by BOTH conduit-srv
   * (reading conduit.circuit_breaker) and tackle-srv (reading
   * tackle.circuit_breaker). The only REST consumers are the UIs
   * (wind-ui/tackle-ui route /config/failure-recovery to tackle-srv), and
   * conduit-mcp reads its breaker directly from the DB via its own
   * getBreaker(). So the edge serves the TACKLE table (the UI contract).
   */
  async getFailureRecovery({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT max_retries_per_model, retry_delay_seconds, max_fallbacks,
                push_back_to_pending, retry_after
         FROM tackle.circuit_breaker WHERE id = 1`,
        [],
        'tackle'
      )
      const breaker = rows[0]
      if (!breaker) {
        return {
          max_retries_per_model: 3,
          retry_delay_seconds: 120,
          max_fallbacks: 3,
          push_back_to_pending: true,
          circuit_breaker_retry_after: 1800,
        }
      }
      return {
        max_retries_per_model: breaker.max_retries_per_model ?? 3,
        retry_delay_seconds: breaker.retry_delay_seconds ?? 120,
        max_fallbacks: breaker.max_fallbacks ?? 3,
        push_back_to_pending: breaker.push_back_to_pending === 1 || breaker.push_back_to_pending === null,
        circuit_breaker_retry_after: breaker.retry_after ?? 1800,
      }
    } catch (e: any) {
      return response.status(500).json({ error: e.message })
    }
  }

  /** POST /config/failure-recovery */
  async saveFailureRecovery({ request, response }: HttpContext) {
    try {
      const {
        max_retries_per_model,
        retry_delay_seconds,
        max_fallbacks,
        push_back_to_pending,
        circuit_breaker_retry_after,
      } = request.body() || {}

      await q(
        `UPDATE tackle.circuit_breaker SET
           max_retries_per_model = COALESCE($1, max_retries_per_model),
           retry_delay_seconds = COALESCE($2, retry_delay_seconds),
           max_fallbacks = COALESCE($3, max_fallbacks),
           push_back_to_pending = COALESCE($4, push_back_to_pending),
           retry_after = COALESCE($5, retry_after),
           updated_at = NOW()
         WHERE id = 1`,
        [
          max_retries_per_model ?? null,
          retry_delay_seconds ?? null,
          max_fallbacks ?? null,
          push_back_to_pending !== undefined ? (push_back_to_pending ? 1 : 0) : null,
          circuit_breaker_retry_after ?? null,
        ],
        'tackle'
      )
      return { saved: true }
    } catch (e: any) {
      return response.status(500).json({ error: e.message })
    }
  }

  /** GET /config/failure-recovery/conduit — the CONDUIT breaker table.
   * R2-3 (architect ruling R-A-2026-08-15-001): the shared
   * /config/failure-recovery serves the tackle table (UIs consume it);
   * this distinct path exposes conduit.circuit_breaker for operability.
   * The two tables are never merged. */
  async getConduitFailureRecovery({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT max_retries_per_model, retry_delay_seconds, max_fallbacks,
                push_back_to_pending, retry_after, tripped, paused
         FROM conduit.circuit_breaker WHERE id = 1`,
        [],
        'conduit'
      )
      const breaker = rows[0]
      if (!breaker) {
        return {
          max_retries_per_model: 3,
          retry_delay_seconds: 120,
          max_fallbacks: 3,
          push_back_to_pending: true,
          circuit_breaker_retry_after: 1800,
          tripped: 0,
          paused: 0,
        }
      }
      return {
        max_retries_per_model: breaker.max_retries_per_model ?? 3,
        retry_delay_seconds: breaker.retry_delay_seconds ?? 120,
        max_fallbacks: breaker.max_fallbacks ?? 3,
        push_back_to_pending: breaker.push_back_to_pending === 1 || breaker.push_back_to_pending === null,
        circuit_breaker_retry_after: breaker.retry_after ?? 1800,
        tripped: breaker.tripped ?? 0,
        paused: breaker.paused ?? 0,
      }
    } catch (e: any) {
      return response.status(500).json({ error: e.message })
    }
  }

  /** POST /config/failure-recovery/conduit */
  async saveConduitFailureRecovery({ request, response }: HttpContext) {
    try {
      const {
        max_retries_per_model,
        retry_delay_seconds,
        max_fallbacks,
        push_back_to_pending,
        circuit_breaker_retry_after,
      } = request.body() || {}

      await q(
        `UPDATE conduit.circuit_breaker SET
           max_retries_per_model = COALESCE($1, max_retries_per_model),
           retry_delay_seconds = COALESCE($2, retry_delay_seconds),
           max_fallbacks = COALESCE($3, max_fallbacks),
           push_back_to_pending = COALESCE($4, push_back_to_pending),
           retry_after = COALESCE($5, retry_after),
           updated_at = NOW()
         WHERE id = 1`,
        [
          max_retries_per_model ?? null,
          retry_delay_seconds ?? null,
          max_fallbacks ?? null,
          push_back_to_pending !== undefined ? (push_back_to_pending ? 1 : 0) : null,
          circuit_breaker_retry_after ?? null,
        ],
        'conduit'
      )
      return { saved: true }
    } catch (e: any) {
      return response.status(500).json({ error: e.message })
    }
  }

  // ── GOVERNANCE ────────────────────────────────────────────────────

  /** POST /governance/replay */
  async replayGovernance({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, work_request_id, plan_id, agent_role, payload, created_at)
         SELECT
           r.id,
           'receipt:' || r.type,
           wr.work_request_uuid,
           r.plan_id,
           r.agent_role,
           jsonb_build_object(
             'session_id', r.session_id,
             'artifact_path', r.artifact_path,
             'summary', r.summary,
             'ticket_id', r.ticket_id,
             'tokens_used', r.tokens_used
           ),
           r.created_at
         FROM ${VISION_SCHEMA}.receipts r
         LEFT JOIN ${VISION_SCHEMA}.work_requests wr ON wr.wr_id = r.plan_id
         WHERE NOT EXISTS (
           SELECT 1 FROM ${PEB_SCHEMA}.governance_events g WHERE g.receipt_id = r.id
         )
         ON CONFLICT (receipt_id) DO NOTHING
         RETURNING id`
      )
      return { ok: true, replayed: rows.length }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  /** GET /governance/events */
  async listGovernanceEvents({ request, response }: HttpContext) {
    try {
      const planId = request.qs().planId as string | undefined
      const eventType = request.qs().eventType as string | undefined
      const limit = request.qs().limit ? parseInt(String(request.qs().limit), 10) : 50

      let sql = `SELECT id, receipt_id, event_type, work_request_id, plan_id, agent_role, payload, created_at, replayed_at
                 FROM ${PEB_SCHEMA}.governance_events`
      const conditions: string[] = []
      const params: any[] = []
      let i = 1

      if (planId) {
        conditions.push(`plan_id = $${i++}`)
        params.push(planId)
      }
      if (eventType) {
        conditions.push(`event_type = $${i++}`)
        params.push(eventType)
      }
      if (conditions.length > 0) {
        sql += ` WHERE ${conditions.join(' AND ')}`
      }
      sql += ` ORDER BY created_at DESC LIMIT $${i++}`
      params.push(limit)

      const { rows } = await q(sql, params)
      return { ok: true, events: rows }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  // ── VISION ────────────────────────────────────────────────────────

  /** POST /vision/work-requests */
  async upsertWorkRequest({ request, response }: HttpContext) {
    try {
      const { id, work_request_uuid, dco_json, context, status, title, entity_key } = request.body()
      if (!id) {
        return response.status(400).json({ ok: false, error: 'Missing required field: id' })
      }
      const uuid = work_request_uuid || randomUUID()
      const { rows } = await q(
        `INSERT INTO ${VISION_SCHEMA}.work_requests (wr_id, work_request_uuid, dco_json, context, status, title, entity_key)
         VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
         ON CONFLICT (wr_id) DO UPDATE SET
           dco_json = EXCLUDED.dco_json,
           context = EXCLUDED.context,
           status = EXCLUDED.status,
           title = EXCLUDED.title,
           entity_key = COALESCE(EXCLUDED.entity_key, ${VISION_SCHEMA}.work_requests.entity_key)
         RETURNING work_request_uuid, (xmax = 0) AS inserted`,
        [id, uuid, dco_json || '{}', JSON.stringify(context || {}), status || 'pending', title || '', entity_key || null]
      )
      const inserted = rows[0]?.inserted === true
      return { ok: true, id, work_request_uuid: rows[0]?.work_request_uuid || uuid, action: inserted ? 'created' : 'updated' }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  /** GET /vision/work-requests */
  async listWorkRequests({ request, response }: HttpContext) {
    try {
      const status = request.qs().status as string | undefined
      const limit = request.qs().limit ? parseInt(String(request.qs().limit), 10) : 50

      // NOTE: vision.work_requests has no updated_at column (recorded_on_dt
      // is the created timestamp) — the original conduit-srv queried
      // updated_at and 500'd. Alias recorded_on_dt to preserve the response
      // contract.
      let sql = `SELECT id, wr_id, work_request_uuid, dco_json, context, status, title, recorded_on_dt, recorded_on_dt AS updated_at
                 FROM ${VISION_SCHEMA}.work_requests`
      const params: any[] = []
      if (status) {
        sql += ` WHERE status = $1`
        params.push(status)
      }
      sql += ` ORDER BY recorded_on_dt DESC LIMIT $${params.length + 1}`
      params.push(limit)

      const { rows } = await q(sql, params)
      return { ok: true, work_requests: rows }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  /** GET /vision/work-requests/:id */
  async getWorkRequest({ request, response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT id, wr_id, work_request_uuid, dco_json, context, status, title, recorded_on_dt, recorded_on_dt AS updated_at
         FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = $1`,
        [request.params().id]
      )
      const wr = rows[0]
      if (!wr) {
        return response.status(404).json({ ok: false, error: 'Not found' })
      }
      return { ok: true, work_request: wr }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  /** GET /vision/receipts */
  async listReceipts({ request, response }: HttpContext) {
    try {
      const planId = request.qs().planId as string
      if (!planId) {
        return response.status(400).json({ ok: false, error: 'Missing required query: planId' })
      }
      const { rows } = await q(
        `SELECT id, plan_id, type, agent_role, session_id, ticket_id,
                artifact_path, summary, metadata_json, tokens_used, created_at, sequence
         FROM ${VISION_SCHEMA}.receipts
         WHERE plan_id = $1
         ORDER BY sequence ASC NULLS LAST, created_at ASC`,
        [planId]
      )
      return { ok: true, receipts: rows }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  // ── SESSION LOG (SSE) ─────────────────────────────────────────────

  /** GET /log/:sessionId (SSE) */
  async sessionLog({ request, response }: HttpContext) {
    const { sessionId } = request.params()

    if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
      return response.status(400).json({ error: 'Invalid session ID' })
    }

    const PIPELINE_DIR = process.env.PIPELINE_DIR || resolvePipelineDir(__dirname)

    const sessionsDir = path.join(PIPELINE_DIR, 'sessions')
    const logPath = path.join(sessionsDir, `${sessionId}.log`)

    response.response.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    })

    let lastSize = 0
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let resolved = false

    const sendLines = () => {
      try {
        if (!fs.existsSync(logPath)) return
        const stats = fs.statSync(logPath)
        if (stats.size <= lastSize) return

        const fd = fs.openSync(logPath, 'r')
        const buf = Buffer.alloc(stats.size - lastSize)
        fs.readSync(fd, buf, 0, buf.length, lastSize)
        fs.closeSync(fd)
        lastSize = stats.size

        const newContent = buf.toString('utf-8')
        const lines = newContent.split('\n')
        for (const line of lines) {
          if (line.length === 0) continue
          const isStderr = line.startsWith('[stderr] ') || line.startsWith('[stderr]')
          const logType = isStderr ? 'stderr' : 'stdout'
          const event = JSON.stringify({
            type: 'session_log',
            data: {
              sessionId,
              line,
              timestamp: new Date().toISOString(),
              logType,
            },
          })
          response.response.write(`data: ${event}\n\n`)
        }
      } catch {
        // file may disappear — stop polling
      }
    }

    const logExists = fs.existsSync(logPath)
    response.response.write(
      `data: ${JSON.stringify({
        type: 'session_log_meta',
        data: { sessionId, logFileExists: logExists, logPath },
      })}\n\n`
    )

    if (logExists) {
      sendLines()
    }

    if (logExists) {
      pollTimer = setInterval(() => {
        if (resolved) return
        sendLines()
      }, 500)
    }

    const keepAlive = setInterval(() => {
      if (resolved) return
      response.response.write(`: keepalive\n\n`)
    }, 15000)

    request.request.on('close', () => {
      resolved = true
      if (pollTimer) clearInterval(pollTimer)
      clearInterval(keepAlive)
    })
  }

  // ── WR (projection drift) ─────────────────────────────────────────

  /** GET /wr/:id/projection-drift */
  async projectionDrift({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const uuid = await this.resolveWrUuid(id)
      const drift = await this.checkProjectionDrift(uuid)
      if (!drift) {
        return response.status(404).json({ ok: false, error: `WorkRequest ${id} not found` })
      }
      return { ok: true, workRequestId: uuid, drift }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  /** GET /wr/drift-scan */
  async driftScan({ request, response }: HttpContext) {
    try {
      const limit = request.qs().limit ? parseInt(String(request.qs().limit), 10) : 100
      const status = request.qs().status as string | undefined
      const { scanned, drifted } = await this.scanProjectionDrift({
        limit,
        statusFilter: status ? [status] : undefined,
      })
      return {
        ok: true,
        scanned: scanned.length,
        drifted: drifted.length,
        findings: drifted.map((s: any) => ({
          work_request_uuid: s.work_request_uuid,
          wr_id: s.wr_id,
          status: s.status,
          expected_state: s.drift.expected_state,
          live_state: s.drift.live_state,
          expected_vision_stage: s.drift.expected_vision_stage,
          live_vision_stage: s.drift.live_vision_stage,
          expected_vision_ir_version: s.drift.expected_vision_ir_version,
          live_vision_ir_version: s.drift.live_vision_ir_version,
          expected_last_event_id: s.drift.expected_last_event_id,
          live_last_event_id: s.drift.live_last_event_id,
        })),
      }
    } catch (err: any) {
      return response.status(500).json({ ok: false, error: err.message })
    }
  }

  // ── Drift helpers (ported from conduit-srv/src/db/drift.ts) ────────

  private async resolveWrUuid(wrIdOrUuid: string): Promise<string> {
    const uuidRegex =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    if (uuidRegex.test(wrIdOrUuid)) return wrIdOrUuid
    const { rows } = await q(
      `SELECT work_request_uuid FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = $1`,
      [wrIdOrUuid]
    )
    if (!rows[0]) return wrIdOrUuid
    return rows[0].work_request_uuid
  }

  private async checkProjectionDrift(
    workRequestId: string
  ): Promise<any | undefined> {
    const { rows } = await q(
      `SELECT * FROM ${CONDUIT_SCHEMA}.check_projection_drift($1::uuid)`,
      [workRequestId]
    )
    return rows[0]
  }

  private async scanProjectionDrift(opts: {
    limit?: number
    statusFilter?: string[]
  }): Promise<{ scanned: any[]; drifted: any[] }> {
    const raw = Number.isFinite(opts.limit as number) ? (opts.limit as number) : 100
    const limit = Math.min(Math.max(raw, 1), 500)
    const { rows } = await q(
      opts.statusFilter?.length
        ? `SELECT work_request_uuid, wr_id, status
           FROM ${VISION_SCHEMA}.work_requests
           WHERE status = ANY($1::text[])
           ORDER BY recorded_on_dt DESC
           LIMIT $2`
        : `SELECT work_request_uuid, wr_id, status
           FROM ${VISION_SCHEMA}.work_requests
           WHERE status NOT IN ('completed', 'cancelled', 'failed', 'settled',
                                'rejected', 'noop', 'deferred')
           ORDER BY recorded_on_dt DESC
           LIMIT $1`,
      opts.statusFilter?.length
        ? [opts.statusFilter, limit]
        : [limit]
    )

    const scanned: any[] = []
    const drifted: any[] = []
    for (const row of rows) {
      try {
        const drift = await this.checkProjectionDrift(row.work_request_uuid)
        if (!drift) continue
        const entry = {
          work_request_uuid: row.work_request_uuid,
          wr_id: row.wr_id,
          status: row.status,
          drift,
        }
        scanned.push(entry)
        if (drift.has_drift) drifted.push(entry)
      } catch (err: any) {
        console.error(
          `[drift-scan] failed for ${row.work_request_uuid}: ${err?.message ?? err}`
        )
      }
    }
    return { scanned, drifted }
  }
}
