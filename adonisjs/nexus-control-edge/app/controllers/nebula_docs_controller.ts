import type { HttpContext } from '@adonisjs/core/http'
import db from '@adonisjs/lucid/services/db'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { q, qT, camelCaseRow, parsePagination, NEXUS_ROOT, AUDIT_ROOT } from '../services/nebula_helpers.js'

/**
 * nebula-srv (Wave 3.1) — docs/plans/audit/preferences/info/import/seed.
 * Ported from nexus/typescript/nebula-srv/src/routes.ts sections:
 * DOCS FILES, PLANS DISPLAY, AUDIT FILES, USER PREFERENCES, SYSTEM INFO
 * TABS, IMPORT / SEED. SQL kept verbatim (knex ? placeholders).
 */

const KNOWN_FILES = ['README.md', 'ARCHITECTURE.md', 'README.markdown', 'SPEC.md', 'REFERENCE.md']

function readDocFiles(workspacePath: string): { filename: string; content: string }[] {
  const resolved = path.resolve(NEXUS_ROOT, workspacePath)
  if (!resolved.startsWith(NEXUS_ROOT)) return []
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) return []

  const files: { filename: string; content: string }[] = []
  for (const fname of KNOWN_FILES) {
    const fpath = path.join(resolved, fname)
    if (fs.existsSync(fpath) && fs.statSync(fpath).isFile()) {
      files.push({ filename: fname, content: fs.readFileSync(fpath, 'utf-8') })
    }
  }
  return files
}

function scanAuditDir(dir: string, baseDir: string): { filePath: string; absPath: string; sizeBytes: number; mtime: string }[] {
  const results: { filePath: string; absPath: string; sizeBytes: number; mtime: string }[] = []
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return results
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const absPath = path.join(dir, entry.name)
    const relPath = path.relative(baseDir, absPath)
    if (entry.isDirectory()) {
      results.push(...scanAuditDir(absPath, baseDir))
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      const st = fs.statSync(absPath)
      results.push({ filePath: relPath, absPath, sizeBytes: st.size, mtime: st.mtime.toISOString() })
    }
  }
  return results
}

async function syncAuditFilesToDb() {
  const scanned = scanAuditDir(AUDIT_ROOT, AUDIT_ROOT)
  const scannedPaths = new Set(scanned.map((f) => f.filePath))
  const trx = await db.transaction()
  try {
    if (scannedPaths.size > 0) {
      await qT(trx, 'DELETE FROM audit_files WHERE file_path != ALL(?::text[])', [Array.from(scannedPaths)])
    } else {
      await qT(trx, 'DELETE FROM audit_files')
    }

    const results: { id: string; filePath: string; content: string; sizeBytes: number; recordedOn: string }[] = []
    for (const file of scanned) {
      try {
        const content = await fs.promises.readFile(file.absPath, 'utf-8')
        await qT(
          trx,
          `UPDATE nebula.audit_files_history
           SET recorded_until_dt = NOW()
           WHERE file_path = ?
             AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
          [file.filePath]
        )
        const { rows: [row] } = await qT(
          trx,
          `INSERT INTO nebula.audit_files_history (file_path, content, size_bytes, recorded_on_dt, recorded_until_dt)
           VALUES (?, ?, ?, NOW(), '9999-12-31 23:59:59+00')
           RETURNING id, file_path, content, size_bytes, recorded_on_dt`,
          [file.filePath, content, file.sizeBytes]
        )
        results.push({
          id: row.id,
          filePath: row.file_path,
          content: row.content,
          sizeBytes: row.size_bytes,
          recordedOn: row.recorded_on_dt,
        })
      } catch (fileErr: any) {
        console.warn(`[audit/sync] Skipping ${file.filePath}: ${fileErr.message}`)
      }
    }
    await trx.commit()
    return results
  } catch (err) {
    await trx.rollback().catch(() => {})
    throw err
  }
}

function err(e: any, status = 500) {
  return { status, body: { error: e?.message ?? String(e) } }
}

export default class NebulaDocsController {
  // ── DOCS FILES ──────────────────────────────────────────────────────

  /** GET /api/docs */
  async docs({ request, response }: HttpContext) {
    try {
      const workspacePath = request.qs().workspacePath as string
      if (!workspacePath) {
        response.status(400).json({ error: 'workspacePath query parameter is required' })
        return
      }
      const resolved = path.resolve(NEXUS_ROOT, workspacePath)
      if (!resolved.startsWith(NEXUS_ROOT)) {
        response.status(403).json({ error: 'Path traversal denied' })
        return
      }
      if (!fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) {
        response.status(404).json({ error: 'Workspace directory not found on disk' })
        return
      }
      const files = readDocFiles(workspacePath)
      response.json({ workspacePath, files, found: files.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/subsystems/:id/docs */
  async subsystemDocs({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: workspaces } = await q(
        'SELECT id, workspace_path FROM nebula.system_workspaces WHERE subsystem_id = ?',
        [id]
      )
      const docs: { workspacePath: string; files: { filename: string; content: string }[] }[] = []
      for (const ws of workspaces) {
        const files = readDocFiles(ws.workspace_path)
        if (files.length > 0) {
          docs.push({ workspacePath: ws.workspace_path, files })
        }
      }
      response.json({ subsystemId: id, docs, found: docs.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/docs */
  async systemDocs({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: workspaces } = await q(
        'SELECT id, workspace_path, subsystem_id FROM nebula.system_workspaces WHERE system_id = ?',
        [id]
      )
      const docs: { workspacePath: string; subsystemId: string | null; files: { filename: string; content: string }[] }[] = []
      for (const ws of workspaces) {
        const files = readDocFiles(ws.workspace_path)
        if (files.length > 0) {
          docs.push({ workspacePath: ws.workspace_path, subsystemId: ws.subsystem_id, files })
        }
      }
      response.json({ systemId: id, docs, found: docs.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── PLANS DISPLAY (Plan 0134) ───────────────────────────────────────

  /** GET /api/plans */
  async listPlans({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { status } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (status && status !== 'all') {
        clauses.push('p.status = ?')
        vals.push(status)
      }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT p.plan_number AS id, p.title, p.goal, p.content,
                  p.files_affected, p.acceptance_criteria, p.dependencies,
                  p.status, p.metadata, p.created_at, p.updated_at,
                  char_length(p.content)::int AS "sizeBytes",
                  p.updated_at AS "modifiedAt"
           FROM nebula.implementation_plans p
           ${where}
           ORDER BY p.updated_at DESC
           LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM nebula.implementation_plans p ${where}`, vals),
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

  /** GET /api/plans/:id */
  async getPlan({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [plan] } = await q(
        `SELECT p.plan_number AS id, p.title, p.goal, p.content,
                p.files_affected, p.acceptance_criteria, p.dependencies,
                p.status, p.metadata, p.created_at, p.updated_at,
                char_length(p.content)::int AS "sizeBytes",
                p.updated_at AS "modifiedAt"
         FROM nebula.implementation_plans p
         WHERE p.plan_number = ?`,
        [id]
      )
      if (!plan) {
        response.status(404).json({ error: `Plan ${id} not found` })
        return
      }
      response.json(plan)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/plans */
  async createPlan({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { title, goal = '', filesAffected = [], acceptanceCriteria = [], dependencies = [], promptRef = '' } = body
      if (!title) {
        response.status(400).json({ error: 'title is required' })
        return
      }
      const slug = title
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, '')
        .trim()
        .replace(/\s+/g, '-')
        .slice(0, 50) || 'plan'

      const metadata: Record<string, any> = {}
      if (promptRef) metadata.prompt_ref = promptRef

      const now = new Date().toISOString()

      const maxRetries = 5
      for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
          const { rows: [maxRow] } = await q(
            `SELECT MAX(NULLIF(regexp_replace(plan_number, '^0+', ''), '')::int) AS max_id
             FROM nebula.implementation_plans`
          )
          const nextId = String((maxRow?.max_id || 0) + 1).padStart(4, '0')
          const fileName = `${slug}-v${nextId}.md`

          const { rows: [plan] } = await q(
            `INSERT INTO nebula.implementation_plans
             (plan_number, title, goal, content, files_affected, acceptance_criteria, dependencies, status, metadata, created_at, updated_at)
             VALUES (?, ?, ?, '', ?::text[], ?::jsonb, ?::text[], 'pending', ?::jsonb, ?, ?)
             RETURNING *`,
            [nextId, title, goal, filesAffected, JSON.stringify(acceptanceCriteria), dependencies, JSON.stringify(metadata), now, now]
          )

          response.status(201).json({
            created: true,
            planNumber: plan.plan_number,
            fileName,
            title: plan.title,
            goal: plan.goal,
            status: plan.status,
            timestamp: now,
          })
          return
        } catch (insertErr: any) {
          if (insertErr.code === '23505' && attempt < maxRetries - 1) {
            continue
          }
          throw insertErr
        }
      }
      throw new Error('Failed to create plan after max retries')
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/implementation-plans/statuses */
  async planStatuses({ response }: HttpContext) {
    try {
      const { rows } = await q(
        `SELECT DISTINCT status FROM nebula.implementation_plans ORDER BY status`
      )
      response.json({ statuses: rows.map((r: any) => r.status) })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/implementation-plans */
  async systemPlans({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT DISTINCT p.plan_number AS id, p.title, p.goal, p.content,
                  p.files_affected, p.acceptance_criteria, p.dependencies,
                  p.status, p.metadata, p.created_at, p.updated_at,
                  char_length(p.content)::int AS "sizeBytes",
                  p.updated_at AS "modifiedAt"
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.system_id = ?
           ORDER BY p.updated_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.system_id = ?`,
          [id]
        ),
      ])
      response.json({
        systemId: id,
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

  /** GET /api/subsystems/:id/implementation-plans */
  async subsystemPlans({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT DISTINCT p.plan_number AS id, p.title, p.goal, p.content,
                  p.files_affected, p.acceptance_criteria, p.dependencies,
                  p.status, p.metadata, p.created_at, p.updated_at,
                  char_length(p.content)::int AS "sizeBytes",
                  p.updated_at AS "modifiedAt"
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.subsystem_id = ?
           ORDER BY p.updated_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.subsystem_id = ?`,
          [id]
        ),
      ])
      response.json({
        subsystemId: id,
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

  /** GET /api/features/:id/implementation-plans */
  async featurePlans({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT DISTINCT p.plan_number AS id, p.title, p.goal, p.content,
                  p.files_affected, p.acceptance_criteria, p.dependencies,
                  p.status, p.metadata, p.created_at, p.updated_at,
                  char_length(p.content)::int AS "sizeBytes",
                  p.updated_at AS "modifiedAt"
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.feature_id = ?
           ORDER BY p.updated_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.feature_id = ?`,
          [id]
        ),
      ])
      response.json({
        featureId: id,
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

  // ── AUDIT FILES ─────────────────────────────────────────────────────

  /** GET /api/audit */
  async listAudit({ request, response }: HttpContext) {
    try {
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          'SELECT id, file_path, size_bytes, recorded_on_dt FROM audit_files ORDER BY file_path LIMIT ? OFFSET ?',
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM audit_files'),
      ])
      response.json({
        items: dataResult.rows.map((r: any) => ({
          id: r.id,
          filePath: r.file_path,
          content: '',
          sizeBytes: r.size_bytes,
          updatedAt: new Date(r.recorded_on_dt).getTime(),
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/audit/graph — entity/edge graph over agent_records + cross_references */
  async auditGraph({ request, response }: HttpContext) {
    try {
      const limit = Math.min(parseInt(request.qs().limit as string) || 200, 500)
      const [records, crossRefs] = await Promise.all([
        q(
          `SELECT id, record_type AS entity_type, role, title AS name,
                  substring(content, 1, 300) AS description_abbr,
                  tags, created_at
           FROM nebula.agent_records
           ORDER BY created_at DESC
           LIMIT ?`,
          [limit]
        ),
        q(
          `SELECT id, source_type AS relation_type,
                  source_type AS source_section, source_id,
                  target_type AS target_section, target_id,
                  rel_type, metadata
           FROM nebula.cross_references
           LIMIT ?`,
          [limit]
        ),
      ])
      response.json({
        entities: records.rows,
        edges: crossRefs.rows,
        entityCount: records.rows.length,
        edgeCount: crossRefs.rows.length,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/audit/:id */
  async getAuditFile({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q(
        'SELECT id, file_path, content, size_bytes, recorded_on_dt FROM audit_files WHERE id = ?',
        [id]
      )
      if (!row) {
        response.status(404).json({ error: 'Audit file not found' })
        return
      }
      response.json({
        id: row.id,
        filePath: row.file_path,
        content: row.content,
        sizeBytes: row.size_bytes,
        updatedAt: new Date(row.recorded_on_dt).getTime(),
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/audit/sync */
  async syncAudit({ response }: HttpContext) {
    try {
      const files = await syncAuditFilesToDb()
      response.json({
        files: files.map((f) => ({
          id: f.id,
          filePath: f.filePath,
          content: '',
          sizeBytes: f.sizeBytes,
          recordedOn: new Date(f.recordedOn).getTime(),
        })),
        count: files.length,
      })
    } catch (e: any) {
      const message = e?.message ?? String(e ?? 'unknown error')
      console.error('[audit/sync] failed:', message)
      response.status(500).json({ error: message })
    }
  }

  /** POST /api/audit/:id/regenerate */
  async regenerateAuditFile({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [existing] } = await q('SELECT file_path FROM audit_files WHERE id = ?', [id])
      if (!existing) {
        response.status(404).json({ error: 'Audit file not found' })
        return
      }
      const absPath = path.resolve(AUDIT_ROOT, existing.file_path)
      if (!absPath.startsWith(AUDIT_ROOT)) {
        response.status(403).json({ error: 'Path traversal denied' })
        return
      }
      if (!fs.existsSync(absPath) || !fs.statSync(absPath).isFile()) {
        response.status(404).json({ error: 'Source file not found on disk' })
        return
      }
      const content = fs.readFileSync(absPath, 'utf-8')
      const st = fs.statSync(absPath)
      const { rows: [updated] } = await q(
        'UPDATE audit_files SET content = ?, size_bytes = ? WHERE id = ? RETURNING id, file_path, content, size_bytes, recorded_on_dt',
        [content, st.size, id]
      )
      response.json({
        id: updated.id,
        filePath: updated.file_path,
        content: updated.content,
        sizeBytes: updated.size_bytes,
        updatedAt: new Date(updated.recorded_on_dt).getTime(),
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── USER PREFERENCES ────────────────────────────────────────────────

  /** GET /api/preferences */
  async getPreferences({ response }: HttpContext) {
    try {
      const { rows } = await q('SELECT key, value FROM user_preferences WHERE user_id = ?', ['default'])
      const prefs: Record<string, any> = {}
      rows.forEach((r: any) => { prefs[r.key] = r.value })
      response.json(prefs)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PUT /api/preferences/:key */
  async setPreference({ request, response }: HttpContext) {
    try {
      const { key } = request.params()
      const { value } = request.body()
      if (value === undefined) {
        response.status(400).json({ error: 'value is required' })
        return
      }
      await q(
        `UPDATE nebula.user_preferences_history
         SET recorded_until_dt = NOW()
         WHERE user_id = ? AND key = ?
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        ['default', key]
      )
      await q(
        `INSERT INTO nebula.user_preferences_history (user_id, key, value, recorded_on_dt, recorded_until_dt)
         VALUES (?, ?, ?, NOW(), '9999-12-31 23:59:59+00')`,
        ['default', key, JSON.stringify(value)]
      )
      response.json({ ok: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/preferences/:key */
  async deletePreference({ request, response }: HttpContext) {
    try {
      const { key } = request.params()
      const { rowCount } = await q('DELETE FROM user_preferences WHERE user_id = ? AND key = ?', ['default', key])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Preference not found' })
        return
      }
      response.json({ ok: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── SYSTEM INFO TABS ────────────────────────────────────────────────

  /** GET /api/systems/:id/info */
  async listInfoTabs({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          'SELECT tab_id, content FROM system_info_tabs WHERE system_id = ? ORDER BY tab_id LIMIT ? OFFSET ?',
          [id, pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM system_info_tabs WHERE system_id = ?', [id]),
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

  /** PUT /api/systems/:id/info/:tabId */
  async saveInfoTab({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id, tabId } = request.params()
      const { content } = request.body()
      await qT(
        trx,
        `UPDATE nebula.system_info_tabs_history
         SET recorded_until_dt = NOW()
         WHERE system_id = ? AND tab_id = ?
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        [id, tabId]
      )
      await qT(
        trx,
        `INSERT INTO nebula.system_info_tabs_history (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
         VALUES (?, ?, ?, NOW(), '9999-12-31 23:59:59+00')`,
        [id, tabId, content || '']
      )
      if (tabId === 'harvest_context' && (!content || !String(content).trim())) {
        await qT(trx, 'UPDATE nebula.harvest_candidates SET system_id = NULL WHERE system_id = ?', [id])
      }
      await trx.commit()
      response.json({ ok: true })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/systems/:id/info/:tabId */
  async deleteInfoTab({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id, tabId } = request.params()
      const { rowCount } = await qT(
        trx,
        `UPDATE nebula.system_info_tabs_history
         SET recorded_until_dt = NOW()
         WHERE system_id = ? AND tab_id = ?
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        [id, tabId]
      )
      if (rowCount === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'Info tab not found' })
        return
      }
      if (tabId === 'harvest_context') {
        await qT(trx, 'UPDATE nebula.harvest_candidates SET system_id = NULL WHERE system_id = ?', [id])
      }
      await trx.commit()
      response.json({ ok: true })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── IMPORT / SEED ───────────────────────────────────────────────────

  /** POST /api/import */
  async importData({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { systems, requirements, workSessions, preferences, infoTabs } = body
      let count = 0
      if (systems && Array.isArray(systems)) {
        for (const sys of systems) {
          await qT(
            trx,
            `INSERT INTO systems (id, name, description, readme) SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM nebula.systems_history WHERE id = ? AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
            [sys.id, sys.name, sys.description || '', sys.readme || null, sys.id]
          )
          if (sys.folders) {
            for (const f of sys.folders) {
              await qT(
                trx,
                `INSERT INTO system_folders (id, system_id, name, category, note) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM nebula.system_folders_history WHERE id = ? AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
                [f.id, sys.id, f.name, f.category, f.note || '', f.id]
              )
            }
          }
          if (sys.subsystems) {
            for (const sub of sys.subsystems) {
              await qT(
                trx,
                `INSERT INTO subsystems (id, system_id, name, description, readme, color) SELECT ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM nebula.subsystems_history WHERE id = ? AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
                [sub.id, sys.id, sub.name, sub.description || '', sub.readme || null, sub.color || '#3B82F6', sub.id]
              )
              if (sub.features) {
                for (const feat of sub.features) {
                  await qT(
                    trx,
                    `INSERT INTO features (id, subsystem_id, name, description, readme) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM nebula.features_history WHERE id = ? AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
                    [feat.id, sub.id, feat.name, feat.description || '', feat.readme || null, feat.id]
                  )
                }
              }
            }
          }
          count++
        }
      }
      if (requirements && Array.isArray(requirements)) {
        for (const r of requirements) {
          await qT(
            trx,
            `INSERT INTO requirements (id, system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date)
             SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
             WHERE NOT EXISTS (SELECT 1 FROM requirements WHERE id = ?)`,
            [r.id, r.systemId, r.subsystemId, r.featureId || null, r.title, r.description || '', r.status || 'Backlog', r.priority || 'Medium', r.startDate || null, r.completionDate || null, r.id]
          )
        }
      }
      if (workSessions && Array.isArray(workSessions)) {
        for (const ws of workSessions) {
          await qT(
            trx,
            `INSERT INTO work_sessions (id, parent_id, parent_type, parent_name, context, platform, model, outcome, status)
             SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
             WHERE NOT EXISTS (SELECT 1 FROM nebula.work_sessions_history WHERE id = ? AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
            [ws.id, ws.parentId, ws.parentType, ws.parentName || '', ws.context || '', ws.platform || '', ws.model || '', ws.outcome || null, ws.status || 'Pending', ws.id]
          )
        }
      }
      if (preferences && typeof preferences === 'object') {
        for (const [key, value] of Object.entries(preferences)) {
          await qT(
            trx,
            `UPDATE nebula.user_preferences_history
             SET recorded_until_dt = NOW()
             WHERE user_id = ? AND key = ?
               AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
            ['default', key]
          )
          await qT(
            trx,
            `INSERT INTO nebula.user_preferences_history (user_id, key, value, recorded_on_dt, recorded_until_dt)
             VALUES (?, ?, ?, NOW(), '9999-12-31 23:59:59+00')`,
            ['default', key, JSON.stringify(value)]
          )
        }
      }
      if (infoTabs && typeof infoTabs === 'object') {
        for (const [systemId, tabs] of Object.entries(infoTabs)) {
          if (typeof tabs === 'object' && tabs !== null) {
            for (const [tabId, content] of Object.entries(tabs as Record<string, string>)) {
              await qT(
                trx,
                `UPDATE nebula.system_info_tabs_history
                 SET recorded_until_dt = NOW()
                 WHERE system_id = ? AND tab_id = ?
                   AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
                [systemId, tabId]
              )
              await qT(
                trx,
                `INSERT INTO nebula.system_info_tabs_history (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
                 VALUES (?, ?, ?, NOW(), '9999-12-31 23:59:59+00')`,
                [systemId, tabId, content]
              )
            }
          }
        }
      }
      await trx.commit()
      response.json({ ok: true, systemsImported: count })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/seed */
  async seed({ response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const existing = await qT(trx, "SELECT id FROM systems WHERE name = 'E-Commerce Platform'")
      if (existing.rows.length > 0) {
        await trx.commit()
        response.json({ ok: true, message: 'Already seeded', systemId: existing.rows[0].id })
        return
      }
      const { rows: [sys] } = await qT(
        trx,
        "INSERT INTO systems (name, description, readme) VALUES ('E-Commerce Platform', 'Main customer facing retail platform', '# E-Commerce Platform Architecture\\nThis system handles all customer-facing interactions.\\n\\n## Tech Stack\\n- Angular 21\\n- Node.js API\\n- PostgreSQL') RETURNING *"
      )
      const { rows: [_f1] } = await qT(
        trx,
        "INSERT INTO system_folders (system_id, name, category, note) VALUES (?, 'webapp', 'UI', 'Main storefront angular app') RETURNING *",
        [sys.id]
      )
      const { rows: [_f2] } = await qT(
        trx,
        "INSERT INTO system_folders (system_id, name, category, note) VALUES (?, 'api-gateway', 'Service', 'BFF for mobile and web') RETURNING *",
        [sys.id]
      )
      const { rows: [sub] } = await qT(
        trx,
        "INSERT INTO subsystems (system_id, name, description, readme, color) VALUES (?, 'Checkout', 'Payment and Order processing', '## Checkout Flow\\n1. Cart validation\\n2. User auth check\\n3. Shipping address\\n4. Payment processing', '#10B981') RETURNING *",
        [sys.id]
      )
      const { rows: [feat] } = await qT(
        trx,
        "INSERT INTO features (subsystem_id, name, description, readme) VALUES (?, 'Payment Gateway', 'Stripe and PayPal integration', 'Integration requirements for Stripe v3 API.') RETURNING *",
        [sub.id]
      )
      await trx.commit()
      response.status(201).json({
        ok: true,
        systemId: sys.id,
        subsystemId: sub.id,
        featureId: feat.id,
      })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }
}
