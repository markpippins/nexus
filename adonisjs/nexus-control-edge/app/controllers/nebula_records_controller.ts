import type { HttpContext } from '@adonisjs/core/http'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { q, toEpochMs, camelCaseRow, parsePagination, AUDIT_ROOT } from '../services/nebula_helpers.js'
import { isValidCrossReferenceType, validateCrossRefConstraint, ALL_CROSSREF_TYPES } from '../services/crossref_taxonomy.js'
import { isValidEvidenceLinkType, isValidProvenance, ALL_EVIDENCE_LINK_TYPES, EVIDENCE_PROVENANCE_VALUES } from '../services/evidence_link_types.js'
import * as bsRedis from '../services/block_segmentation_redis.js'

/**
 * nebula-srv (Wave 3.1) — records domain.
 * Ported from nexus/typescript/nebula-srv/src/routes.ts sections:
 * AGENT RECORDS, INBOX POINTERS, PROJECTIONS, CROSS-REFERENCES,
 * EVIDENCE LINKS.
 */

const VALID_TYPES = ['report', 'analysis', 'assessment', 'inspection', 'prompt', 'response', 'engineering_log', 'architecture_note', 'decision']

const ASSEMBLY_URL = process.env.ASSEMBLY_URL || 'http://localhost:3107'
const DECISIONS_FORUM_ID = '703bc0f9-faf4-4c94-a52d-8f0d4024a89b'

let assemblyUserMapCache: Record<string, string> | null = null
async function getAssemblyUserMap(): Promise<Record<string, string>> {
  if (assemblyUserMapCache) return assemblyUserMapCache
  const resp = await fetch(`${ASSEMBLY_URL}/api/users`)
  if (!resp.ok) throw new Error(`assembly /api/users -> HTTP ${resp.status}`)
  const users = (await resp.json()) as any[]
  const map: Record<string, string> = {}
  for (const u of users) {
    if (u && u.name) map[String(u.name).toLowerCase()] = u.id
  }
  assemblyUserMapCache = map
  return map
}

async function mirrorDecisionToForum(record: any): Promise<void> {
  try {
    const users = await getAssemblyUserMap()
    const userId = users[String(record.role || '').toLowerCase()]
    if (!userId) {
      console.warn(`[decisions-mirror] no assembly user for role '${record.role}' — skipping ${record.id}`)
      return
    }
    const resp = await fetch(`${ASSEMBLY_URL}/api/forums/by-id/${DECISIONS_FORUM_ID}/threads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: record.title,
        body: record.content,
        postedById: userId,
        source_url: `nebula://agent-record/${record.id}`,
        role: record.role,
        model: record.model || null,
      }),
    })
    if (!resp.ok) {
      console.warn(`[decisions-mirror] forum post HTTP ${resp.status} for ${record.id}`)
    }
  } catch (e: any) {
    console.warn(`[decisions-mirror] error for ${record.id}: ${e?.message || e}`)
  }
}

function err(e: any, status = 500) {
  return { status, body: { error: e?.message ?? String(e) } }
}

export default class NebulaRecordsController {
  // ── AGENT RECORDS ───────────────────────────────────────────────────

  /** GET /api/agent-records */
  async listAgentRecords({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { type, role, systemId, subsystemId, featureId, planRef, tag, search, createdAfter, createdBefore, level, visibilityScope } = qs
      const page = Math.max(1, parseInt(String(qs.page || '1'), 10))
      const limitParam = parseInt(String(qs.limit ?? ''), 10)
      const offsetParam = parseInt(String(qs.offset ?? ''), 10)
      const pageSize = Number.isFinite(limitParam)
        ? Math.min(500, Math.max(1, limitParam))
        : Math.min(100, Math.max(1, parseInt(String(qs.pageSize || '100'), 10)))
      const offset = Number.isFinite(offsetParam)
        ? Math.max(0, offsetParam)
        : (page - 1) * pageSize

      const clauses: string[] = []
      const vals: any[] = []
      let i = 1

      if (type) { clauses.push(`record_type = $${i++}`); vals.push(type) }
      if (role) { clauses.push(`role = $${i++}`); vals.push(role) }
      if (systemId) { clauses.push(`system_id = $${i++}`); vals.push(systemId) }
      if (subsystemId) { clauses.push(`subsystem_id = $${i++}`); vals.push(subsystemId) }
      if (featureId) { clauses.push(`feature_id = $${i++}`); vals.push(featureId) }
      if (planRef) { clauses.push(`plan_ref = $${i++}`); vals.push(planRef) }
      if (tag) {
        const raw = Array.isArray(tag) ? tag as string[] : [tag as string]
        const tagArr: string[] = []
        for (const item of raw) {
          for (const part of item.split(',')) {
            const trimmed = part.trim()
            if (trimmed) tagArr.push(trimmed)
          }
        }
        if (tagArr.length === 1) {
          clauses.push(`$${i} = ANY(tags)`)
          vals.push(tagArr[0])
          i++
        } else if (tagArr.length > 1) {
          clauses.push(`tags @> $${i}::text[]`)
          vals.push(tagArr)
          i++
        }
      }
      if (search) {
        clauses.push(`(title ILIKE $${i} OR content ILIKE $${i})`)
        vals.push(`%${search}%`)
        i++
      }
      if (createdAfter) {
        clauses.push(`created_at >= $${i++}`)
        vals.push(createdAfter)
      }
      if (createdBefore) {
        clauses.push(`created_at <= $${i++}`)
        vals.push(createdBefore)
      }
      if (level) { clauses.push(`level = $${i++}`); vals.push(parseInt(level as string)) }
      if (visibilityScope) { clauses.push(`visibility_scope = $${i++}`); vals.push(visibilityScope) }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT id, record_type, role, model, title, source_path, tags, system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt, level, visibility_scope
           FROM nebula.agent_records ${where}
           ORDER BY created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM nebula.agent_records ${where}`, vals),
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

  /** GET /api/agent-records/:id */
  async getAgentRecord({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM nebula.agent_records WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Agent record not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/agent-records/search */
  async searchAgentRecords({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { tags, recordType, role, level, visibilityScope, match = 'all', limit: qLimit, offset: qOffset } = body
      const maxLimit = Math.min(parseInt(qLimit as string) || 100, 500)
      const offset = parseInt(qOffset as string) || 0

      const clauses: string[] = []
      const vals: any[] = []
      let i = 1

      if (recordType) { clauses.push(`record_type = $${i++}`); vals.push(recordType) }
      if (role) { clauses.push(`role = $${i++}`); vals.push(role) }
      if (level !== undefined && level !== null) {
        const levelNum = parseInt(level)
        if (levelNum >= 1 && levelNum <= 4) {
          clauses.push(`level = $${i++}`); vals.push(levelNum)
        }
      }
      if (visibilityScope) { clauses.push(`visibility_scope = $${i++}`); vals.push(visibilityScope) }

      if (tags && Array.isArray(tags) && tags.length > 0) {
        const cleanTags = tags.filter((t: any) => typeof t === 'string' && t.trim()).map((t: string) => t.trim())
        if (cleanTags.length > 0) {
          if (match === 'any') {
            clauses.push(`tags && $${i}::text[]`)
            vals.push(cleanTags)
            i++
          } else {
            if (cleanTags.length === 1) {
              clauses.push(`$${i} = ANY(tags)`)
              vals.push(cleanTags[0])
              i++
            } else {
              clauses.push(`tags @> $${i}::text[]`)
              vals.push(cleanTags)
              i++
            }
          }
        }
      }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const { rows } = await q(
        `SELECT id, record_type, role, model, title, source_path, tags, system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt, level, visibility_scope
         FROM nebula.agent_records ${where}
         ORDER BY created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
        [...vals, maxLimit, offset]
      )

      const countWhere = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''
      const { rows: [{ count }] } = await q(
        `SELECT COUNT(*)::int AS count FROM nebula.agent_records ${countWhere}`,
        vals
      )

      response.json({
        // Normalized to match nebula-srv GET/POST agent-records shape:
        // camelCase rows under `items`, `total` count (count kept as a
        // deprecated mirror for one transition window). The old raw
        // snake_case `records` key is removed.
        items: rows.map(camelCaseRow),
        total: parseInt(count),
        count: parseInt(count),
        limit: maxLimit,
        offset,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/agent-records */
  async createAgentRecord({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { recordType, role, title, content, sourcePath, metadata, tags, systemId, subsystemId, featureId, planRef, level, visibilityScope, model } = body

      if (!recordType || !VALID_TYPES.includes(recordType)) {
        response.status(400).json({ error: `recordType must be one of: ${VALID_TYPES.join(', ')}` })
        return
      }
      if (level !== undefined && (level < 1 || level > 4)) {
        response.status(400).json({ error: 'level must be between 1 and 4' })
        return
      }

      const { rows: [row] } = await q(
        `INSERT INTO nebula.agent_records (record_type, role, title, content, source_path, metadata, tags, system_id, subsystem_id, feature_id, plan_ref, level, visibility_scope, model)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [
          recordType, role || '', title || '', content || '',
          sourcePath || null, metadata || {}, tags || [],
          systemId || null, subsystemId || null, featureId || null, planRef || null,
          level ?? 1, visibilityScope || 'all', model || null,
        ]
      )
      response.status(201).json(row)

      if (recordType === 'decision') {
        mirrorDecisionToForum(row).catch((e: any) => console.warn('[decisions-mirror]', e?.message || e))
      }
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/agent-records/:id */
  async updateAgentRecord({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { title, content, metadata, tags, systemId, subsystemId, featureId, planRef, level, visibilityScope, model } = body
      if (level !== undefined && (level < 1 || level > 4)) {
        response.status(400).json({ error: 'level must be between 1 and 4' })
        return
      }
      const sets: string[] = []
      const vals: any[] = []
      if (title !== undefined) { sets.push('title = ?'); vals.push(title) }
      if (content !== undefined) { sets.push('content = ?'); vals.push(content) }
      if (metadata !== undefined) { sets.push('metadata = ?'); vals.push(metadata) }
      if (tags !== undefined) { sets.push('tags = ?'); vals.push(tags) }
      if (systemId !== undefined) { sets.push('system_id = ?'); vals.push(systemId) }
      if (subsystemId !== undefined) { sets.push('subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId !== undefined) { sets.push('feature_id = ?'); vals.push(featureId) }
      if (planRef !== undefined) { sets.push('plan_ref = ?'); vals.push(planRef) }
      if (level !== undefined) { sets.push('level = ?'); vals.push(level) }
      if (visibilityScope !== undefined) { sets.push('visibility_scope = ?'); vals.push(visibilityScope) }
      if (model !== undefined) { sets.push('model = ?'); vals.push(model) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [row] } = await q(
        `UPDATE nebula.agent_records SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!row) {
        response.status(404).json({ error: 'Agent record not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/agent-records/:id */
  async deleteAgentRecord({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q('UPDATE nebula.agent_records SET valid_until = now() WHERE id = ? AND valid_until > now()', [id])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Agent record not found' })
        return
      }
      response.json({ expired: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── INBOX POINTERS ──────────────────────────────────────────────────

  /** GET /api/inbox-pointer/:role */
  async getInboxPointer({ request, response }: HttpContext) {
    try {
      const role = request.params().role as string
      bsRedis.initRedis()
      const pointer = await bsRedis.getInboxPointer(role)
      response.json({ role, pointer })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PUT /api/inbox-pointer/:role */
  async setInboxPointer({ request, response }: HttpContext) {
    try {
      const role = request.params().role as string
      const { timestamp } = request.body()
      if (!timestamp) {
        response.status(400).json({ error: 'timestamp is required' })
        return
      }
      bsRedis.initRedis()
      await bsRedis.setInboxPointer(role, timestamp)
      response.json({ ok: true, role, pointer: timestamp })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/inbox-pointers */
  async listInboxPointers(_ctx: HttpContext, response: any) {
    try {
      bsRedis.initRedis()
      const pointers = await bsRedis.getAllInboxPointers()
      response.json({ pointers })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── PROJECTIONS ─────────────────────────────────────────────────────

  /** GET /api/projections */
  async listProjections({ request, response }: HttpContext) {
    try {
      const { offset, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          'SELECT id, name, type, description, target_path, model, schedule, created_at, recorded_on_dt FROM nebula.projections ORDER BY name LIMIT ? OFFSET ?',
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.projections'),
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

  /** POST /api/projections */
  async createProjection({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { name, type, description, sourceQuery, template, targetPath, model, schedule, metadata } = body
      if (!name || !type) {
        response.status(400).json({ error: 'name and type are required' })
        return
      }
      if (!['deterministic', 'inference'].includes(type)) {
        response.status(400).json({ error: 'type must be deterministic or inference' })
        return
      }
      const { rows: [row] } = await q(
        `INSERT INTO nebula.projections (name, type, description, source_query, template, target_path, model, schedule, metadata)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [name, type, description || '', sourceQuery || '', template || '', targetPath || '', model || '', schedule || '', metadata || {}]
      )
      response.status(201).json(row)
    } catch (e: any) {
      if (e.code === '23505') {
        response.status(409).json({ error: `Projection '${request.body().name}' already exists` })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/projections/:id/render */
  async renderProjection({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [proj] } = await q('SELECT * FROM nebula.projections WHERE id = ?', [id])
      if (!proj) {
        response.status(404).json({ error: 'Projection not found' })
        return
      }

      if (proj.type === 'deterministic') {
        const { rows: data } = await q(proj.source_query)
        const rendered: { path: string; content: string }[] = []

        for (const row of data) {
          let content = proj.template
          for (const [key, value] of Object.entries(row)) {
            const val = value === null ? '' : String(value)
            const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const safeVal = val.replace(/\$/g, '$$$$')
            content = content.replace(new RegExp(`\\{\\{${escapedKey}\\}\\}`, 'g'), safeVal)
          }
          let targetPath = proj.target_path
          for (const [key, value] of Object.entries(row)) {
            const val = value === null ? '' : String(value)
            const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
            const safeVal = val.replace(/\$/g, '$$$$')
            targetPath = targetPath.replace(new RegExp(`\\{\\{${escapedKey}\\}\\}`, 'g'), safeVal)
          }

          const absPath = path.resolve(AUDIT_ROOT, targetPath)
          if (!absPath.startsWith(AUDIT_ROOT)) {
            response.status(403).json({ error: `Target path traversal denied: ${targetPath}` })
            return
          }
          const dir = path.dirname(absPath)
          fs.mkdirSync(dir, { recursive: true })
          fs.writeFileSync(absPath, content, 'utf-8')
          rendered.push({ path: targetPath, content: content.slice(0, 200) + '...' })
        }

        response.json({ ok: true, type: 'deterministic', rendered: rendered.length, files: rendered })
      } else {
        response.json({ ok: true, type: 'inference', note: 'Inference projection not yet implemented' })
      }
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/projections/:id */
  async deleteProjection({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q('UPDATE nebula.projections SET valid_until = now() WHERE id = ? AND valid_until > now()', [id])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Projection not found' })
        return
      }
      response.json({ expired: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── CROSS-REFERENCES ────────────────────────────────────────────────

  /** POST /api/cross-references */
  async createCrossReference({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { sourceType, sourceId, targetType, targetId, relType, metadata } = body
      if (!sourceType || !sourceId || !targetType || !targetId || !relType) {
        response.status(400).json({ error: 'sourceType, sourceId, targetType, targetId, and relType are required' })
        return
      }
      if (!isValidCrossReferenceType(relType)) {
        response.status(400).json({ error: `Invalid rel_type "${relType}". Allowed values: ${ALL_CROSSREF_TYPES.join(', ')}` })
        return
      }
      const constraint = validateCrossRefConstraint(relType, sourceType, targetType)
      if (!constraint.valid) {
        response.status(400).json({ error: constraint.error })
        return
      }
      const { rows: [row] } = await q(
        `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
           WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
         DO NOTHING
         RETURNING *`,
        [sourceType, sourceId, targetType, targetId, relType, JSON.stringify(metadata || {})]
      )
      if (!row) {
        response.status(409).json({ error: 'Cross-reference already exists' })
        return
      }
      response.status(201).json(toEpochMs(row, 'created_at'))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/cross-references */
  async listCrossReferences({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { sourceType, sourceId, targetType, targetId, relType } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (sourceType) { clauses.push('source_type = ?'); vals.push(sourceType) }
      if (sourceId) { clauses.push('source_id = ?'); vals.push(sourceId) }
      if (targetType) { clauses.push('target_type = ?'); vals.push(targetType) }
      if (targetId) { clauses.push('target_id = ?'); vals.push(targetId) }
      if (relType) { clauses.push('rel_type = ?'); vals.push(relType) }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM nebula.cross_references ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM nebula.cross_references ${where}`, vals),
      ])

      response.json({
        items: dataResult.rows.map((r: any) => toEpochMs(r, 'created_at')),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/cross-references/:id */
  async getCrossReference({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM nebula.cross_references WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Cross-reference not found' })
        return
      }
      response.json(toEpochMs(row, 'created_at'))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/cross-references/:id */
  async deleteCrossReference({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q(
        'UPDATE nebula.cross_references SET valid_until = now() WHERE id = ? AND valid_until > now()',
        [id]
      )
      if (rowCount === 0) {
        response.status(404).json({ error: 'Cross-reference not found' })
        return
      }
      response.json({ expired: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── EVIDENCE LINKS ──────────────────────────────────────────────────

  /** POST /api/evidence-links */
  async createEvidenceLink({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { knowledgeEntityId, nebulaHarvestId, nebulaCandidateId, linkType, confidence, provenance, rationale, sourceSpan, metadata } = body

      if (!knowledgeEntityId || !linkType) {
        response.status(400).json({ error: 'knowledgeEntityId and linkType are required' })
        return
      }
      if (!nebulaHarvestId && !nebulaCandidateId) {
        response.status(400).json({ error: 'At least one of nebulaHarvestId or nebulaCandidateId is required' })
        return
      }
      if (!isValidEvidenceLinkType(linkType)) {
        response.status(400).json({ error: `Invalid linkType "${linkType}". Allowed values: ${ALL_EVIDENCE_LINK_TYPES.join(', ')}` })
        return
      }
      if (provenance) {
        if (!isValidProvenance(provenance)) {
          response.status(400).json({ error: `Invalid provenance "${provenance}". Allowed values: ${EVIDENCE_PROVENANCE_VALUES.join(', ')}` })
          return
        }
      }

      const { rows: [row] } = await q(
        `INSERT INTO steward.evidence_links
           (knowledge_entity_id, nebula_harvest_id, nebula_candidate_id,
            link_type, confidence, provenance, rationale, source_span, metadata)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         RETURNING *`,
        [
          knowledgeEntityId,
          nebulaHarvestId || null,
          nebulaCandidateId || null,
          linkType,
          confidence != null ? confidence : null,
          provenance || 'auto_ingestor',
          rationale || null,
          sourceSpan ? JSON.stringify(sourceSpan) : null,
          JSON.stringify(metadata || {}),
        ]
      )
      response.status(201).json(toEpochMs(row, 'created_at'))
    } catch (e: any) {
      if (e.code === '23505') {
        response.status(409).json({ error: 'Duplicate evidence link — this entity+source+type combination already exists' })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/evidence-links */
  async listEvidenceLinks({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { knowledgeEntityId, nebulaHarvestId, nebulaCandidateId, linkType, provenance, minConfidence, maxConfidence } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (knowledgeEntityId) { clauses.push('knowledge_entity_id = ?'); vals.push(knowledgeEntityId) }
      if (nebulaHarvestId) { clauses.push('nebula_harvest_id = ?'); vals.push(nebulaHarvestId) }
      if (nebulaCandidateId) { clauses.push('nebula_candidate_id = ?'); vals.push(nebulaCandidateId) }
      if (linkType) { clauses.push('link_type = ?'); vals.push(linkType) }
      if (provenance) { clauses.push('provenance = ?'); vals.push(provenance) }
      if (minConfidence) { clauses.push('confidence >= ?'); vals.push(parseFloat(minConfidence as string)) }
      if (maxConfidence) { clauses.push('confidence <= ?'); vals.push(parseFloat(maxConfidence as string)) }

      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM steward.evidence_links ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM steward.evidence_links ${where}`, vals),
      ])

      response.json({
        items: dataResult.rows.map((r: any) => toEpochMs(r, 'created_at')),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/evidence-links/:id */
  async getEvidenceLink({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM steward.evidence_links WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Evidence link not found' })
        return
      }
      response.json(toEpochMs(row, 'created_at'))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/evidence-links/:id */
  async deleteEvidenceLink({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q('DELETE FROM steward.evidence_links WHERE id = ?', [id])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Evidence link not found' })
        return
      }
      response.status(204)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/evidence-links?knowledgeEntityId=... */
  async bulkDeleteEvidenceLinks({ request, response }: HttpContext) {
    try {
      const { knowledgeEntityId } = request.qs()
      if (!knowledgeEntityId) {
        response.status(400).json({ error: 'knowledgeEntityId query parameter is required for bulk delete' })
        return
      }
      const { rowCount } = await q('DELETE FROM steward.evidence_links WHERE knowledge_entity_id = ?', [knowledgeEntityId])
      response.json({ deleted: rowCount })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }
}
