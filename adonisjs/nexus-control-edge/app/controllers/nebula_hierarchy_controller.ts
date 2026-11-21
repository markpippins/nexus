import type { HttpContext } from '@adonisjs/core/http'
import db from '@adonisjs/lucid/services/db'
import { q, qT, asQueryable, toEpochMs, parsePagination, normalizeStatus, STATUS_CANONICAL, REQ_TYPES, getUnusedColor, randomUUID } from '../services/nebula_helpers.js'
import {
  REQUIREMENT_DELETE_BLOCKED_CODE,
  deleteRequirementSet,
  requirementDeleteConflict,
  requirementDeleteSuccess,
  requirementIdsInScope,
} from '../services/requirement_delete_guard.js'
import { CrossReferenceType } from '../services/crossref_taxonomy.js'

/**
 * nebula-srv (Wave 3.1) — hierarchy domain.
 * Ported from nexus/typescript/nebula-srv/src/routes.ts (SYSTEMS,
 * SUBSYSTEMS, FEATURES, REQUIREMENTS, SYSTEM FOLDERS, WORK SESSIONS,
 * COMPLEX OPERATIONS, WORKSPACES sections). SQL kept verbatim; $n
 * placeholders converted to knex ? at runtime.
 */

function err(e: any, status = 500) {
  return { status, body: { error: e?.message ?? String(e) } }
}

export default class NebulaHierarchyController {
  // ── SYSTEMS ─────────────────────────────────────────────────────────

  /** GET /api/systems — full nested hierarchy with pagination */
  async listSystems({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { offset, page, pageSize } = parsePagination(qs)
      const { rows } = await q(
        `SELECT * FROM systems ORDER BY created_at ASC LIMIT ? OFFSET ?`,
        [pageSize, offset]
      )
      const { rows: countRows } = await q('SELECT COUNT(*)::int AS total FROM systems')
      const systems = rows.map((s: any) => ({
        ...toEpochMs(s, 'created_at'),
        subsystems: [],
        features: [],
      }))
      const result: any[] = []
      for (const sys of systems) {
        const { rows: subs } = await q('SELECT * FROM subsystems WHERE system_id = ? ORDER BY name', [sys.id])
        const subsystems = subs.map((sub: any) => ({
          ...toEpochMs(sub, 'created_at'),
          systemId: sub.system_id,
          features: [],
        }))
        for (const sub of subsystems) {
          const { rows: feats } = await q('SELECT * FROM features WHERE subsystem_id = ? ORDER BY name', [sub.id])
          sub.features = feats.map((f: any) => ({ ...toEpochMs(f, 'created_at'), subsystemId: f.subsystem_id }))
        }
        sys.subsystems = subsystems
        result.push(sys)
      }
      response.json({ items: result, total: parseInt(countRows[0].total, 10), page, pageSize })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/systems */
  async createSystem({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { name, description = '', readme = null, path = null } = body
      if (!name) {
        response.status(400).json({ error: 'name is required' })
        return
      }
      const { rows: [sys] } = await q(
        'INSERT INTO systems (name, description, readme, path) VALUES (?, ?, ?, ?) RETURNING *',
        [name, description, readme, path]
      )
      response.status(201).json({ ...toEpochMs(sys, 'created_at'), subsystems: [], features: [] })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/systems/:id */
  async updateSystem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { name, description, readme, path } = body
      const sets: string[] = []
      const vals: any[] = []
      if (name !== undefined) { sets.push('name = ?'); vals.push(name) }
      if (description !== undefined) { sets.push('description = ?'); vals.push(description) }
      if (readme !== undefined) { sets.push('readme = ?'); vals.push(readme) }
      if (path !== undefined) { sets.push('path = ?'); vals.push(path) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [sys] } = await q(
        `UPDATE systems SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!sys) {
        response.status(404).json({ error: 'System not found' })
        return
      }
      response.json({ ...toEpochMs(sys, 'created_at'), name: sys.name, description: sys.description, readme: sys.readme, path: sys.path })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /**
   * DELETE /api/systems/:id — cascade delete.
   *
   * P4a: the requirement cascade is explicit (the hierarchy relations are views
   * with no FK to cascade from) and runs in one transaction, leaf-first through
   * the canonical delete guard. A requirement that a specification, plan,
   * request or blueprint still names refuses the whole cascade with 409 instead
   * of leaving it half done. Same contract as nebula-srv and moleculer/nebula.
   */
  async deleteSystem({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id } = request.params()
      const run = asQueryable(trx)
      const requirementIds = await requirementIdsInScope(run, 'system_id', id)
      const deleted = await deleteRequirementSet(run, requirementIds, `System ${id}`)
      await qT(trx, 'DELETE FROM work_sessions WHERE parent_id = ?', [id])
      const { rowCount } = await qT(trx, 'DELETE FROM systems WHERE id = ?', [id])
      if (rowCount === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'System not found' })
        return
      }
      await trx.commit()
      response.json(requirementDeleteSuccess(deleted))
    } catch (e: any) {
      await trx.rollback()
      const conflict = requirementDeleteConflict(e)
      if (conflict) {
        response.status(409).json({ code: REQUIREMENT_DELETE_BLOCKED_CODE, ...conflict })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id — single system with nested hierarchy */
  async getSystem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [sys] } = await q('SELECT * FROM systems WHERE id = ?', [id])
      if (!sys) {
        response.status(404).json({ error: 'System not found' })
        return
      }
      const { rows: subs } = await q('SELECT * FROM subsystems WHERE system_id = ? ORDER BY name', [id])
      const subsystems = subs.map((sub: any) => ({
        ...toEpochMs(sub, 'created_at'),
        systemId: sub.system_id,
        features: [],
      }))
      for (const sub of subsystems) {
        const { rows: feats } = await q('SELECT * FROM features WHERE subsystem_id = ? ORDER BY name', [sub.id])
        sub.features = feats.map((f: any) => ({ ...toEpochMs(f, 'created_at'), subsystemId: f.subsystem_id }))
      }
      response.json({ ...toEpochMs(sys, 'created_at'), subsystems })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── SUBSYSTEMS ──────────────────────────────────────────────────────

  /** POST /api/subsystems */
  async createSubsystem({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { systemId, name, description = '', readme = null, path = null } = body
      if (!systemId || !name) {
        response.status(400).json({ error: 'systemId and name are required' })
        return
      }
      const color = await getUnusedColor(systemId)
      const { rows: [sub] } = await q(
        'INSERT INTO subsystems (system_id, name, description, readme, color, path) VALUES (?, ?, ?, ?, ?, ?) RETURNING *',
        [systemId, name, description, readme, color, path]
      )
      response.status(201).json({
        ...toEpochMs(sub, 'created_at'),
        systemId: sub.system_id,
        features: [],
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/subsystems/:id */
  async updateSubsystem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { name, description, readme, color, path } = body
      const sets: string[] = []
      const vals: any[] = []
      if (name !== undefined) { sets.push('name = ?'); vals.push(name) }
      if (description !== undefined) { sets.push('description = ?'); vals.push(description) }
      if (readme !== undefined) { sets.push('readme = ?'); vals.push(readme) }
      if (color !== undefined) { sets.push('color = ?'); vals.push(color) }
      if (path !== undefined) { sets.push('path = ?'); vals.push(path) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [sub] } = await q(
        `UPDATE subsystems SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!sub) {
        response.status(404).json({ error: 'Subsystem not found' })
        return
      }
      response.json({ ...toEpochMs(sub, 'created_at'), systemId: sub.system_id, name: sub.name, description: sub.description, readme: sub.readme, color: sub.color, path: sub.path })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/subsystems/:id — cascade deletes features and requirements */
  async deleteSubsystem({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id } = request.params()
      const run = asQueryable(trx)
      const requirementIds = await requirementIdsInScope(run, 'subsystem_id', id)
      const deleted = await deleteRequirementSet(run, requirementIds, `Subsystem ${id}`)
      const { rowCount } = await qT(trx, 'DELETE FROM subsystems WHERE id = ?', [id])
      if (rowCount === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'Subsystem not found' })
        return
      }
      await trx.commit()
      response.json(requirementDeleteSuccess(deleted))
    } catch (e: any) {
      await trx.rollback()
      const conflict = requirementDeleteConflict(e)
      if (conflict) {
        response.status(409).json({ code: REQUIREMENT_DELETE_BLOCKED_CODE, ...conflict })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/subsystems/:id — single subsystem with features */
  async getSubsystem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [sub] } = await q('SELECT * FROM subsystems WHERE id = ?', [id])
      if (!sub) {
        response.status(404).json({ error: 'Subsystem not found' })
        return
      }
      const { rows: feats } = await q('SELECT * FROM features WHERE subsystem_id = ? ORDER BY name', [sub.id])
      response.json({
        ...toEpochMs(sub, 'created_at'),
        systemId: sub.system_id,
        features: feats.map((f: any) => ({ ...toEpochMs(f, 'created_at'), subsystemId: f.subsystem_id })),
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── FEATURES ────────────────────────────────────────────────────────

  /** POST /api/features */
  async createFeature({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { subsystemId, name, description = '', readme = null, path = null } = body
      if (!subsystemId || !name) {
        response.status(400).json({ error: 'subsystemId and name are required' })
        return
      }
      const { rows: [feat] } = await q(
        'INSERT INTO features (subsystem_id, name, description, readme, path) VALUES (?, ?, ?, ?, ?) RETURNING *',
        [subsystemId, name, description, readme, path]
      )
      response.status(201).json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/features/:id */
  async updateFeature({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { name, description, readme, path } = body
      const sets: string[] = []
      const vals: any[] = []
      if (name !== undefined) { sets.push('name = ?'); vals.push(name) }
      if (description !== undefined) { sets.push('description = ?'); vals.push(description) }
      if (readme !== undefined) { sets.push('readme = ?'); vals.push(readme) }
      if (path !== undefined) { sets.push('path = ?'); vals.push(path) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [feat] } = await q(
        `UPDATE features SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!feat) {
        response.status(404).json({ error: 'Feature not found' })
        return
      }
      response.json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id, name: feat.name, description: feat.description, readme: feat.readme, path: feat.path })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/features/:id — cascade deletes requirements with feature_id */
  async deleteFeature({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id } = request.params()
      const run = asQueryable(trx)
      const requirementIds = await requirementIdsInScope(run, 'feature_id', id)
      const deleted = await deleteRequirementSet(run, requirementIds, `Feature ${id}`)
      const { rowCount } = await qT(trx, 'DELETE FROM features WHERE id = ?', [id])
      if (rowCount === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'Feature not found' })
        return
      }
      await trx.commit()
      response.json(requirementDeleteSuccess(deleted))
    } catch (e: any) {
      await trx.rollback()
      const conflict = requirementDeleteConflict(e)
      if (conflict) {
        response.status(409).json({ code: REQUIREMENT_DELETE_BLOCKED_CODE, ...conflict })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/features/:id — single feature */
  async getFeature({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [feat] } = await q('SELECT * FROM features WHERE id = ?', [id])
      if (!feat) {
        response.status(404).json({ error: 'Feature not found' })
        return
      }
      response.json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── REQUIREMENTS ────────────────────────────────────────────────────

  private async reqJson(r: any) {
    return {
      ...toEpochMs(r, 'created_at'),
      systemId: r.system_id,
      subsystemId: r.subsystem_id,
      featureId: r.feature_id,
      startDate: r.start_date,
      completionDate: r.completion_date,
      parentId: r.parent_id,
      reqType: r.req_type,
      acceptanceCriteria: r.acceptance_criteria,
      candidateId: r.candidate_id,
      conduitPlanId: r.conduit_plan_id,
    }
  }

  /** GET /api/requirements — filterable with pagination */
  async listRequirements({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { systemId, subsystemId, featureId } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (systemId) { clauses.push('system_id = ?'); vals.push(systemId) }
      if (subsystemId) { clauses.push('subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId) { clauses.push('feature_id = ?'); vals.push(featureId) }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM requirements ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(`SELECT COUNT(*)::int AS total FROM requirements ${where}`, vals),
      ])

      const items: any[] = []
      for (const r of dataResult.rows) {
        items.push(await this.reqJson(r))
      }

      // Question counts
      if (items.length > 0) {
        const ids = items.map((it: any) => it.id)
        const { rows: qcRows } = await q(
          `SELECT requirement_id,
                  COUNT(*)::int AS total,
                  COUNT(*) FILTER (WHERE status = 'OPEN')::int AS open_count,
                  COUNT(*) FILTER (WHERE status = 'OPEN' AND blocking = true)::int AS blocking_count
           FROM nebula.open_questions
           WHERE requirement_id = ANY(?::uuid[])
           GROUP BY requirement_id`,
          [ids]
        )
        const qcMap = new Map<string, any>(qcRows.map((r: any) => [r.requirement_id, r]))
        for (const item of items as any[]) {
          const qc: any = qcMap.get(item.id)
          item.questionCounts = qc
            ? { total: qc.total, openCount: qc.open_count, blockingCount: qc.blocking_count }
            : { total: 0, openCount: 0, blockingCount: 0 }
        }
      }

      response.json({
        items,
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/requirements/:id — single requirement by ID */
  async getRequirement({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [reqt] } = await q('SELECT * FROM requirements WHERE id = ?', [id])
      if (!reqt) {
        response.status(404).json({ error: 'Requirement not found' })
        return
      }
      const { rows: qcRows } = await q(
        `SELECT COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'OPEN')::int AS open_count,
                COUNT(*) FILTER (WHERE status = 'OPEN' AND blocking = true)::int AS blocking_count
         FROM nebula.open_questions
         WHERE requirement_id = ?`,
        [id]
      )
      response.json({
        ...(await this.reqJson(reqt)),
        questionCounts: qcRows.length > 0
          ? { total: qcRows[0].total, openCount: qcRows[0].open_count, blockingCount: qcRows[0].blocking_count }
          : { total: 0, openCount: 0, blockingCount: 0 },
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/requirements/:id/children */
  async requirementChildren({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT * FROM requirements WHERE parent_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM requirements WHERE parent_id = ?', [id]),
      ])

      const items = []
      for (const r of dataResult.rows) items.push(await this.reqJson(r))
      response.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/requirements/:id/dependencies */
  async requirementDependencies({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { offset, page, pageSize } = parsePagination(request.qs())

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT cr.id, cr.source_type, cr.source_id, cr.target_type, cr.target_id,
                  cr.rel_type, cr.metadata, cr.created_at,
                  CASE WHEN cr.source_id = ? THEN cr.target_id ELSE cr.source_id END AS other_id,
                  CASE WHEN cr.source_id = ? THEN 'outgoing' ELSE 'incoming' END AS direction
           FROM nebula.cross_references cr
           WHERE ((cr.source_type = 'requirement' AND cr.source_id = ?)
              OR (cr.target_type = 'requirement' AND cr.target_id = ?))
             AND cr.rel_type IN ('req:blocks', 'req:depends_on')
           ORDER BY cr.created_at ASC
           LIMIT ? OFFSET ?`,
          [id, id, id, id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.cross_references cr
           WHERE ((cr.source_type = 'requirement' AND cr.source_id = ?)
              OR (cr.target_type = 'requirement' AND cr.target_id = ?))
             AND cr.rel_type IN ('req:blocks', 'req:depends_on')`,
          [id, id]
        ),
      ])

      response.json({
        items: dataResult.rows.map((r: any) => ({
          id: r.id,
          relType: r.rel_type,
          sourceType: r.source_type,
          sourceId: r.source_id,
          targetType: r.target_type,
          targetId: r.target_id,
          direction: r.direction,
          otherId: r.other_id,
          metadata: r.metadata,
          createdAt: r.created_at ? new Date(r.created_at).getTime() : null,
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

  /** POST /api/requirements/:id/dependencies */
  async createRequirementDependency({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { targetId, relType = 'req:blocks' } = body
      if (!targetId) {
        response.status(400).json({ error: 'targetId is required' })
        return
      }
      if (id === targetId) {
        response.status(400).json({ error: 'A requirement cannot depend on itself' })
        return
      }
      const validRelTypes = [CrossReferenceType.REQ_BLOCKS, CrossReferenceType.REQ_DEPENDS_ON]
      if (!validRelTypes.includes(relType)) {
        response.status(400).json({ error: `relType must be one of: ${validRelTypes.join(', ')}` })
        return
      }
      const { rows } = await q('SELECT id FROM requirements WHERE id = ANY(?::uuid[])', [[id, targetId]])
      if (rows.length !== 2) {
        response.status(404).json({ error: 'One or both requirements not found' })
        return
      }
      const { rows: [xref] } = await q(
        `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
         SELECT 'requirement', ?, 'requirement', ?, ?, '{}'
         WHERE NOT EXISTS (
           SELECT 1 FROM nebula.cross_references_history
           WHERE source_type = 'requirement'
             AND source_id = ?
             AND target_type = 'requirement'
             AND target_id = ?
             AND rel_type = ?
             AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
         )
         ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
           WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
         DO NOTHING
         RETURNING *`,
        [id, targetId, relType, id, targetId, relType]
      )
      response.status(201).json(xref || { ok: true, message: 'Dependency already exists' })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/requirements/:id/dependencies/:depId */
  async deleteRequirementDependency({ request, response }: HttpContext) {
    try {
      const { id, depId } = request.params()
      const { rowCount } = await q(
        `UPDATE nebula.cross_references
         SET valid_until = now()
         WHERE id = ?
           AND source_type = 'requirement'
           AND target_type = 'requirement'
           AND rel_type IN ('req:blocks', 'req:depends_on')
           AND (source_id = ? OR target_id = ?)
           AND valid_until > now()`,
        [depId, id, id]
      )
      if (rowCount === 0) {
        response.status(404).json({ error: 'Dependency not found' })
        return
      }
      response.json({ expired: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/requirements */
  async createRequirement({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { systemId, subsystemId = null, featureId = null, title, description = '', status = 'Backlog', priority = 'Medium', startDate = null, completionDate = null, parentId = null, reqType = null, acceptanceCriteria = null, candidateId = null } = body
      if (!systemId || !title) {
        response.status(400).json({ error: 'systemId and title are required' })
        return
      }
      const normalizedStatus = normalizeStatus(status)
      if (!normalizedStatus) {
        response.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` })
        return
      }
      if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) {
        response.status(400).json({ error: `reqType, if provided, must be one of: ${REQ_TYPES.join(', ')}` })
        return
      }
      const { rows: [reqt] } = await q(
        `INSERT INTO requirements (system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date, parent_id, req_type, acceptance_criteria, candidate_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [systemId, subsystemId, featureId, title, description, normalizedStatus, priority, startDate, completionDate, parentId, reqType, acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null, candidateId]
      )
      response.status(201).json(await this.reqJson(reqt))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/requirements/batch — batch status update */
  async batchUpdateRequirements({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { ids, status } = body
      if (!ids || !Array.isArray(ids) || !status) {
        response.status(400).json({ error: 'ids (array) and status are required' })
        return
      }
      const normalizedStatus = normalizeStatus(status)
      if (!normalizedStatus) {
        response.status(400).json({ error: `status must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` })
        return
      }
      const { rowCount } = await q(
        'UPDATE requirements SET status = ? WHERE id = ANY(?::uuid[])',
        [normalizedStatus, ids]
      )
      response.json({ ok: true, updated: rowCount })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/requirements/:id */
  async updateRequirement({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { title, description, status, priority, startDate, completionDate, systemId, subsystemId, featureId, parentId, reqType, acceptanceCriteria, candidateId, conduitPlanId } = body
      const sets: string[] = []
      const vals: any[] = []
      if (title !== undefined) { sets.push('title = ?'); vals.push(title) }
      if (description !== undefined) { sets.push('description = ?'); vals.push(description) }
      if (status !== undefined) {
        const normalizedStatus = normalizeStatus(status)
        if (!normalizedStatus) {
          response.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` })
          return
        }
        sets.push('status = ?'); vals.push(normalizedStatus)
      }
      if (priority !== undefined) { sets.push('priority = ?'); vals.push(priority) }
      if (startDate !== undefined) { sets.push('start_date = ?'); vals.push(startDate) }
      if (completionDate !== undefined) { sets.push('completion_date = ?'); vals.push(completionDate) }
      if (systemId !== undefined) { sets.push('system_id = ?'); vals.push(systemId) }
      if (subsystemId !== undefined) { sets.push('subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId !== undefined) { sets.push('feature_id = ?'); vals.push(featureId) }
      if (parentId !== undefined) { sets.push('parent_id = ?'); vals.push(parentId) }
      if (reqType !== undefined) {
        if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) {
          response.status(400).json({ error: `reqType must be one of: ${REQ_TYPES.join(', ')}` })
          return
        }
        sets.push('req_type = ?'); vals.push(reqType)
      }
      if (acceptanceCriteria !== undefined) { sets.push('acceptance_criteria = ?'); vals.push(acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null) }
      if (candidateId !== undefined) { sets.push('candidate_id = ?'); vals.push(candidateId) }
      if (conduitPlanId !== undefined) { sets.push('conduit_plan_id = ?'); vals.push(conduitPlanId) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [reqt] } = await q(
        `UPDATE requirements SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!reqt) {
        response.status(404).json({ error: 'Requirement not found' })
        return
      }
      // Backlog→ToDo auto-compile trigger (Plan 1062) — fire-and-forget.
      // D2 (CP-2): compile is pre-row — no conduit plan implied; plan creation
      // is a separate release-time step (CP-9 release gate).
      if (status !== undefined && reqt.status === 'ToDo') {
        fetch(`http://localhost:3101/api/requirements/${id}/compile`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ createPlan: false }),
        }).catch(() => { /* best-effort */ })
      }
      response.json(await this.reqJson(reqt))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /**
   * DELETE /api/requirements/:id — refused with 409 while any governance
   * surface still names the requirement; the DB enforces the same rule for
   * callers that bypass this action, so the refusal payload is identical to
   * nebula-srv's.
   */
  async deleteRequirement({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id } = request.params()
      const run = asQueryable(trx)
      const deleted = await deleteRequirementSet(run, [id], `Requirement ${id}`)
      if (deleted === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'Requirement not found' })
        return
      }
      await trx.commit()
      response.json(requirementDeleteSuccess(deleted))
    } catch (e: any) {
      await trx.rollback()
      const conflict = requirementDeleteConflict(e)
      if (conflict) {
        response.status(409).json({ code: REQUIREMENT_DELETE_BLOCKED_CODE, ...conflict })
        return
      }
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/requirements/:id/move — kanban-friendly single-id status move */
  async moveRequirement({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id } = request.params()
      const body = request.body()
      const { targetStatus, expectedCurrentStatus } = body
      const allowedList = Array.from(STATUS_CANONICAL).join(', ')
      const expectedSupplied = expectedCurrentStatus !== undefined && expectedCurrentStatus !== null
      const normalizedTarget = normalizeStatus(targetStatus)
      const normalizedExpected = expectedSupplied ? normalizeStatus(expectedCurrentStatus) : undefined
      if (!normalizedTarget) {
        await trx.rollback()
        response.status(400).json({ error: `targetStatus is required and must be one of: ${allowedList}` })
        return
      }
      if (expectedSupplied && normalizedExpected === null) {
        await trx.rollback()
        response.status(400).json({ error: `expectedCurrentStatus, if provided, must be one of: ${allowedList}` })
        return
      }
      // Serialize concurrent moves, then read through the transition surface
      // (V222 re-points `requirements`; the history table is frozen after the
      // port, and an INSTEAD OF view cannot be locked with FOR UPDATE).
      await qT(trx, `SELECT pg_advisory_xact_lock(hashtext(?)::bigint)`, [id])
      const { rows: [currentRow] } = await qT(
        trx,
        `SELECT id, status FROM requirements WHERE id = ?`,
        [id]
      )
      if (!currentRow) {
        await trx.rollback()
        response.status(404).json({ error: 'Requirement not found' })
        return
      }
      if (normalizedExpected !== undefined && currentRow.status !== normalizedExpected) {
        await trx.rollback()
        response.status(409).json({
          error: 'Current status does not match expectedCurrentStatus',
          currentStatus: currentRow.status,
          expectedCurrentStatus: normalizedExpected,
        })
        return
      }
      const { rows: [reqt] } = await qT(
        trx,
        'UPDATE requirements SET status = ? WHERE id = ? RETURNING *',
        [normalizedTarget, id]
      )
      await trx.commit()
      response.json(await this.reqJson(reqt))
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── SYSTEM FOLDERS ──────────────────────────────────────────────────

  /** POST /api/systems/:id/folders */
  async createSystemFolder({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { name, category, note = '' } = body
      if (!name || !category) {
        response.status(400).json({ error: 'name and category are required' })
        return
      }
      const { rows: [folder] } = await q(
        'INSERT INTO system_folders (system_id, name, category, note) VALUES (?, ?, ?, ?) RETURNING *',
        [id, name, category, note]
      )
      response.status(201).json({ id: folder.id, name: folder.name, category: folder.category, note: folder.note })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/systems/:systemId/folders/:folderId */
  async deleteSystemFolder({ request, response }: HttpContext) {
    try {
      const { systemId, folderId } = request.params()
      const { rowCount } = await q(
        'DELETE FROM system_folders WHERE id = ? AND system_id = ?',
        [folderId, systemId]
      )
      if (rowCount === 0) {
        response.status(404).json({ error: 'Folder not found' })
        return
      }
      response.json({ ok: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── WORK SESSIONS ───────────────────────────────────────────────────

  /** GET /api/sessions */
  async listSessions({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q('SELECT * FROM work_sessions ORDER BY created_at DESC LIMIT ? OFFSET ?', [pageSize, offset]),
        q('SELECT COUNT(*)::int AS total FROM work_sessions'),
      ])
      response.json({
        items: dataResult.rows.map((r: any) => ({
          ...toEpochMs(r, 'created_at'),
          parentId: r.parent_id,
          parentType: r.parent_type,
          parentName: r.parent_name,
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page, pageSize, limit, offset,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/sessions */
  async createSession({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { parentId, parentType, parentName = '', context = '', platform = '', model = '', outcome = null, status = 'Pending' } = body
      if (!parentId || !parentType) {
        response.status(400).json({ error: 'parentId and parentType are required' })
        return
      }
      const normalizedParentType = parentType.toLowerCase()
      const { rows: [sess] } = await q(
        `INSERT INTO work_sessions (parent_id, parent_type, parent_name, context, platform, model, outcome, status)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [parentId, normalizedParentType, parentName, context, platform, model, outcome, status]
      )
      response.status(201).json({
        ...toEpochMs(sess, 'created_at'),
        parentId: sess.parent_id, parentType: sess.parent_type, parentName: sess.parent_name,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/sessions/:id */
  async updateSession({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { outcome, status } = body
      const sets: string[] = []
      const vals: any[] = []
      if (outcome !== undefined) { sets.push('outcome = ?'); vals.push(outcome) }
      if (status !== undefined) { sets.push('status = ?'); vals.push(status) }
      if (sets.length === 0) {
        response.json({ ok: true })
        return
      }
      vals.push(id)
      const { rows: [sess] } = await q(
        `UPDATE work_sessions SET ${sets.join(', ')} WHERE id = ? RETURNING *`,
        vals
      )
      if (!sess) {
        response.status(404).json({ error: 'Session not found' })
        return
      }
      response.json({
        ...toEpochMs(sess, 'created_at'),
        parentId: sess.parent_id, parentType: sess.parent_type, parentName: sess.parent_name,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/sessions/:id */
  async deleteSession({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q('DELETE FROM work_sessions WHERE id = ?', [id])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Session not found' })
        return
      }
      response.json({ ok: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── COMPLEX OPERATIONS (transactional) ──────────────────────────────

  /** POST /api/features/move */
  async moveFeature({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const body = request.body()
      const { featureId, targetSystemId, targetSubsystemId } = body
      if (!featureId || !targetSystemId || !targetSubsystemId) {
        await trx.rollback()
        response.status(400).json({ error: 'featureId, targetSystemId, and targetSubsystemId are required' })
        return
      }
      const { rows: [feat] } = await qT(trx, 'UPDATE features SET subsystem_id = ? WHERE id = ? RETURNING *', [targetSubsystemId, featureId])
      if (!feat) {
        await trx.rollback()
        response.status(404).json({ error: 'Feature not found' })
        return
      }
      await qT(trx, 'UPDATE requirements SET system_id = ?, subsystem_id = ? WHERE feature_id = ?', [targetSystemId, targetSubsystemId, featureId])
      await trx.commit()
      response.json({ ok: true })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/subsystems/move */
  async moveSubsystem({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const body = request.body()
      const { subsystemId, targetSystemId } = body
      if (!subsystemId || !targetSystemId) {
        await trx.rollback()
        response.status(400).json({ error: 'subsystemId and targetSystemId are required' })
        return
      }
      const { rows: [sub] } = await qT(trx, 'UPDATE subsystems SET system_id = ? WHERE id = ? RETURNING *', [targetSystemId, subsystemId])
      if (!sub) {
        await trx.rollback()
        response.status(404).json({ error: 'Subsystem not found' })
        return
      }
      await qT(trx, 'UPDATE requirements SET system_id = ? WHERE subsystem_id = ?', [targetSystemId, subsystemId])
      await trx.commit()
      response.json({ ok: true })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/systems/demote/:id */
  async demoteSystem({ request, response }: HttpContext) {
    const trx = await db.connection('nebula').transaction()
    try {
      const { id: sourceId } = request.params()
      const body = request.body()
      const { targetSystemId } = body
      if (!targetSystemId) {
        await trx.rollback()
        response.status(400).json({ error: 'targetSystemId is required' })
        return
      }
      const { rows: [source] } = await qT(trx, 'SELECT * FROM systems WHERE id = ?', [sourceId])
      if (!source) {
        await trx.rollback()
        response.status(404).json({ error: 'Source system not found' })
        return
      }
      const color = await getUnusedColor(targetSystemId)
      const { rows: [newSub] } = await qT(
        trx,
        'INSERT INTO subsystems (system_id, name, description, readme, color) VALUES (?, ?, ?, ?, ?) RETURNING *',
        [targetSystemId, source.name, source.description, source.readme, color]
      )
      const { rows: oldSubs } = await qT(trx, 'SELECT * FROM subsystems WHERE system_id = ?', [sourceId])
      for (const os of oldSubs) {
        const { rows: [newFeat] } = await qT(
          trx,
          'INSERT INTO features (subsystem_id, name, description, readme) VALUES (?, ?, ?, ?) RETURNING *',
          [newSub.id, os.name, os.description, os.readme]
        )
        await qT(
          trx,
          'UPDATE requirements SET system_id = ?, subsystem_id = ?, feature_id = ? WHERE subsystem_id = ?',
          [targetSystemId, newSub.id, newFeat.id, os.id]
        )
        const { rows: oldFeats } = await qT(trx, 'SELECT * FROM features WHERE subsystem_id = ?', [os.id])
        for (const ofe of oldFeats) {
          await qT(
            trx,
            'UPDATE requirements SET system_id = ?, subsystem_id = ?, feature_id = ? WHERE feature_id = ?',
            [targetSystemId, newSub.id, newFeat.id, ofe.id]
          )
        }
      }
      await qT(
        trx,
        'UPDATE requirements SET system_id = ?, subsystem_id = ?, feature_id = NULL WHERE system_id = ? AND subsystem_id IS NULL',
        [targetSystemId, newSub.id, sourceId]
      )
      // The hierarchy tables have no cascading foreign keys. Requirements now
      // point at the destination rows, so remove the demoted source subtree too.
      await qT(
        trx,
        'DELETE FROM features WHERE subsystem_id IN (SELECT id FROM subsystems WHERE system_id = ?)',
        [sourceId]
      )
      await qT(trx, 'DELETE FROM subsystems WHERE system_id = ?', [sourceId])
      await qT(trx, 'DELETE FROM systems WHERE id = ?', [sourceId])
      await trx.commit()
      response.json({ ok: true, newSubsystemId: newSub.id })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── WORKSPACES ──────────────────────────────────────────────────────

  /** GET /api/workspaces */
  async listWorkspaces({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT w.id, w.system_id, w.subsystem_id, w.workspace_path, w.created_at,
                  s.name AS system_name, sub.name AS subsystem_name
           FROM nebula.system_workspaces w
           LEFT JOIN nebula.systems s ON s.id = w.system_id
           LEFT JOIN nebula.subsystems sub ON sub.id = w.subsystem_id
           ORDER BY s.name, sub.name
           LIMIT ? OFFSET ?`,
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.system_workspaces'),
      ])
      response.json({
        items: dataResult.rows.map((r: any) => ({
          ...toEpochMs(r, 'created_at'),
          systemId: r.system_id,
          subsystemId: r.subsystem_id,
          workspacePath: r.workspace_path,
          systemName: r.system_name,
          subsystemName: r.subsystem_name,
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page, pageSize, limit, offset,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/workspaces */
  async createWorkspace({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { systemId, subsystemId = null, workspacePath } = body
      if (!systemId || !workspacePath) {
        response.status(400).json({ error: 'systemId and workspacePath are required' })
        return
      }
      const { rows: [w] } = await q(
        'INSERT INTO nebula.system_workspaces (system_id, subsystem_id, workspace_path) VALUES (?, ?, ?) RETURNING *',
        [systemId, subsystemId, workspacePath]
      )
      response.status(201).json({
        ...toEpochMs(w, 'created_at'),
        systemId: w.system_id,
        subsystemId: w.subsystem_id,
        workspacePath: w.workspace_path,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/workspaces/:id */
  async deleteWorkspace({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rowCount } = await q('DELETE FROM nebula.system_workspaces WHERE id = ?', [id])
      if (rowCount === 0) {
        response.status(404).json({ error: 'Workspace not found' })
        return
      }
      response.json({ ok: true })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/requirements/:id/compile — two-stage compiler (WorkRequest IR) */
  async compileRequirement({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { stage1Only = false, createPlan = false, dryRun = false } = request.body()

      const { rows: [reqt] } = await q(
        `SELECT req.id, req.title, req.description, req.status, req.priority, req.req_type,
                req.acceptance_criteria, req.candidate_id,
                req.system_id, req.subsystem_id, req.feature_id, req.parent_id,
                COALESCE(sys.name, '') AS system_name,
                COALESCE(sys.description, '') AS system_description,
                COALESCE(sub.name, '') AS subsystem_name,
                COALESCE(sub.description, '') AS subsystem_description,
                COALESCE(feat.name, '') AS feature_name,
                COALESCE(feat.description, '') AS feature_description
         FROM nebula.requirements req
         LEFT JOIN nebula.systems sys ON sys.id = req.system_id
         LEFT JOIN nebula.subsystems sub ON sub.id = req.subsystem_id
         LEFT JOIN nebula.features feat ON feat.id = req.feature_id
         WHERE req.id = ?`,
        [id]
      )
      if (!reqt) return response.status(404).json({ error: 'Requirement not found' })

      const hierarchyContext = {
        system: { id: reqt.system_id, name: reqt.system_name, description: reqt.system_description },
        subsystem: { id: reqt.subsystem_id, name: reqt.subsystem_name, description: reqt.subsystem_description },
        feature: { id: reqt.feature_id, name: reqt.feature_name, description: reqt.feature_description },
      }

      let normalizedCriteria: string[] = []
      const rawAC = reqt.acceptance_criteria
      if (rawAC) {
        const parsed = typeof rawAC === 'string' ? JSON.parse(rawAC) : rawAC
        if (Array.isArray(parsed)) {
          normalizedCriteria = parsed.map((item: any) =>
            typeof item === 'string' ? item.trim() :
            (item?.condition || item?.title || item?.criterion || '').trim()
          ).filter(Boolean)
        } else if (typeof parsed === 'object' && parsed?.condition) {
          normalizedCriteria = [parsed.condition]
        }
      }

      const { rows: crossRefs } = await q(
        `SELECT cr.rel_type, cr.target_type, cr.target_id,
                CASE WHEN cr.target_type = 'requirement' THEN
                  (SELECT title FROM nebula.requirements WHERE id = cr.target_id::uuid)
                ELSE cr.target_id::text END AS target_label
         FROM nebula.cross_references cr
         WHERE cr.source_type = 'requirement' AND cr.source_id = ?
         ORDER BY cr.created_at`,
        [id]
      )

      const intentParts = [reqt.title]
      if (reqt.description) intentParts.push(reqt.description)
      if (reqt.subsystem_name) intentParts.push(`Subsystem: ${reqt.subsystem_name}`)
      if (reqt.feature_name) intentParts.push(`Feature: ${reqt.feature_name}`)
      const intentSummary = intentParts.filter(Boolean).join(' — ')

      const stage1 = {
        requirement_id: id,
        title: reqt.title,
        hierarchy_context: hierarchyContext,
        normalized_criteria: normalizedCriteria,
        cross_references: crossRefs,
        intent_summary: intentSummary,
      }

      if (stage1Only) {
        return response.json({ ok: true, stage: 1, result: stage1 })
      }

      const { rows: registry } = await q(
        `SELECT id, intent_id, version, label, match_patterns, opcode_template,
                required_params, idempotency_key
         FROM nebula.op_registry
         WHERE status = 'active' AND deleted_at IS NULL`
      )

      let matchedEntry: any = null
      let bestScore = 0
      const intentText = `${reqt.title} ${intentSummary}`.toLowerCase()
      for (const entry of registry) {
        const patterns = entry.match_patterns || []
        for (const pattern of patterns) {
          try {
            const match = new RegExp(pattern, 'i').exec(intentText)
            if (match && match[0].length > bestScore) {
              bestScore = match[0].length
              matchedEntry = entry
            }
          } catch { /* skip invalid regex */ }
        }
      }

      let opSequence: any[] = []
      if (matchedEntry?.opcode_template) {
        const template = typeof matchedEntry.opcode_template === 'string'
          ? JSON.parse(matchedEntry.opcode_template) : matchedEntry.opcode_template
        if (Array.isArray(template)) {
          opSequence = template.map((step: any, i: number) => ({
            step: i + 1,
            op: step.op || 'WRITE_FILE',
            target: step.target || '',
            args: step.params || {},
            idempotency_key: `${matchedEntry.idempotency_key || ''}-${id.slice(0, 8)}`,
          }))
        }
      }
      if (opSequence.length === 0) {
        const reqShort = id.slice(0, 8)
        normalizedCriteria.slice(0, 5).forEach((criterion: string, i: number) => {
          opSequence.push({
            step: i + 1, op: 'WRITE_SOURCE_FILE',
            target: `src/${reqShort}/step_${i+1}`,
            args: { content_template: 'acceptance-criterion', criterion },
            idempotency_key: `req-${reqShort}-step-${i+1}`,
          })
        })
        opSequence.push({
          step: opSequence.length + 1, op: 'VALIDATE_SYNTAX',
          target: `src/${reqShort}/`, args: { language: 'auto' },
          idempotency_key: `req-${reqShort}-validate`,
        })
      }

      const filesAffected: string[] = []
      const fileSet = new Set<string>()
      for (const step of opSequence) {
        if (step.target && !step.target.startsWith('spec/') && !step.target.startsWith('files/')) {
          let t = step.target
          if (!t.match(/\.(py|ts|js|go|java|sql|md)$/)) t = t.replace(/\/$/, '') + '/__init__.py'
          fileSet.add(t)
        }
      }
      if (reqt.system_name) {
        let base = reqt.system_name.toLowerCase().replace(/\s/g, '-')
        if (reqt.subsystem_name) base += '/' + reqt.subsystem_name.toLowerCase().replace(/\s/g, '-')
        fileSet.add(`${base}/__init__.py`)
      }
      filesAffected.push(...Array.from(fileSet).sort())

      const dependencies = crossRefs
        .filter((r: any) => r.rel_type === 'req:depends_on' || r.rel_type === 'req:blocks')
        .map((r: any) => r.target_label)
        .filter(Boolean)

      const idempotencyKey = matchedEntry?.idempotency_key || `req-${id.slice(0, 8)}`
      const acceptanceForPlan = normalizedCriteria.slice(0, 5).length > 0
        ? normalizedCriteria.slice(0, 5)
        : [`Implement: ${reqt.title}`]

      const stage2 = {
        requirement_id: id,
        intent_id: matchedEntry?.intent_id || `REQ-${id.slice(0, 8)}`,
        registry_version: matchedEntry?.version || 'default',
        op_sequence: opSequence,
        files_affected: filesAffected,
        dependencies,
        acceptance_criteria: acceptanceForPlan,
        idempotency_key: idempotencyKey,
        matched_op_registry_id: matchedEntry?.id || null,
      }

      if (dryRun) {
        return response.json({ ok: true, stage: 2, stage1, stage2, dryRun: true })
      }

      const journalId = randomUUID()
      const now = new Date().toISOString()
      const journalContent = JSON.stringify({
        requirement_id: id,
        stage1: { normalized_criteria_count: normalizedCriteria.length, cross_references_count: crossRefs.length },
        stage2: { matched: !!matchedEntry, op_count: opSequence.length, files_count: filesAffected.length, idempotency_key: idempotencyKey },
      })
      try {
        await q(
          `INSERT INTO nebula.agent_records (id, record_type, role, title, content, tags, created_at, updated_at)
           VALUES (?::uuid, 'engineering_log', 'architect', ?, ?, ?, ?, ?)`,
          [journalId, `Requirement Compilation: ${reqt.title.slice(0, 80)}`, journalContent,
           JSON.stringify(['req-compilation', `requirement:${id.slice(0, 8)}`, 'audit']), now, now]
        )
      } catch (journalErr) {
        console.warn('[compile] Journal entry write failed:', journalErr)
      }

      let planNumber: string | null = null
      if (createPlan) {
        const project = (reqt.system_name || 'nexus').toLowerCase().replace(/\s/g, '-')
        try {
          const planResponse = await fetch('http://localhost:3101/api/plans', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: reqt.title,
              project,
              goal: intentSummary,
              acceptanceCriteria: acceptanceForPlan,
              filesAffected,
              dependencies,
            }),
          })
          const planResult = await planResponse.json() as any
          if (planResult.created && planResult.planNumber) {
            planNumber = planResult.planNumber
            await q(
              `UPDATE requirements SET conduit_plan_id = ? WHERE id = ?`,
              [planNumber, id]
            )
          }
        } catch (planErr) {
          console.warn('[compile] Conduit plan creation failed:', planErr)
        }
      }

      response.json({
        ok: true,
        stage: 2,
        stage1,
        stage2,
        journal_entry_id: journalId,
        plan_number: planNumber,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

}
