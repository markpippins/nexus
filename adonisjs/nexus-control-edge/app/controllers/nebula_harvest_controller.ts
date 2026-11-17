import type { HttpContext } from '@adonisjs/core/http'
import db from '@adonisjs/lucid/services/db'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { q, qT, toEpochMs, camelCaseRow, parsePagination, normalizeStatus, STATUS_CANONICAL, REQ_TYPES, isUuid, hasPlanRef, createSpawnsPlanCrossRef, upsertHarvestContextTab } from '../services/nebula_helpers.js'

const execFileAsync = promisify(execFile)

// AC11: scoped work-request reads resolve their requirement through the
// normalized link, never the retired single-source shortcut.
const REQUIREMENT_LINK_JOIN = `
           JOIN resolution.request_requirement_link rrl
             ON rrl.request_id = wr.id
            AND rrl.relation = 'source'
            AND rrl.valid_until = 'infinity'::timestamptz
            AND rrl.recorded_until_dt = 'infinity'::timestamptz
           JOIN nebula.requirements req ON req.id = rrl.requirement_id`

/**
 * nebula-srv (Wave 3.1) — harvest pipeline domain.
 * Ported from nexus/typescript/nebula-srv/src/routes.ts sections:
 * HARVESTS, HARVEST CANDIDATES, INTENT RECORDS, AGENDAS, SPECIFICATIONS,
 * WORK REQUESTS, SPECS, HARVEST CANDIDATE DISCOVERY.
 */

function err(e: any, status = 500) {
  return { status, body: { error: e?.message ?? String(e) } }
}

const SCRIPT_PATH = '/home/codex/dev/nexus/bin/unified_semantic_search.py'
const PYTHON_BIN = '/home/codex/dev/nexus/python/rover/.venv/bin/python3'

export default class NebulaHarvestController {
  // ── HARVESTS ────────────────────────────────────────────────────────

  /** GET /api/harvests */
  async listHarvests({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const model = qs.model as string | undefined
      const version = qs.version as string | undefined
      const sourceHash = qs.sourceHash as string | undefined
      const level = qs.level as string | undefined
      const visibilityScope = qs.visibilityScope as string | undefined
      const tag = qs.tag as string | undefined
      const sort = (qs.sort as string) || 'created_at'
      const { page, pageSize } = parsePagination(qs)

      const validSorts = ['candidate_count', 'code_blocks', 'turns', 'block_density', 'collaboration', 'created_at', 'tag_frequency', 'keyword_hits']
      if (!validSorts.includes(sort)) {
        response.status(400).json({ error: `sort must be one of: ${validSorts.join(', ')}` })
        return
      }

      const sortExpr: Record<string, string> = {
        candidate_count: 'COALESCE(h.total_candidates, 0)',
        code_blocks:      "COALESCE((h.docklang #>> '{stats,by_type,code}')\n::int, 0)",
        turns:            'COALESCE(jsonb_array_length(h.docklang -> \'discourse_units\'), 0)',
        block_density:    "CASE WHEN jsonb_array_length(h.docklang -> 'discourse_units') > 0 THEN (h.docklang #>> '{stats,total_blocks}')::numeric / jsonb_array_length(h.docklang -> 'discourse_units') ELSE 0 END",
        collaboration:    "(SELECT count(*) FROM jsonb_array_elements(h.docklang -> 'discourse_units') du WHERE du #>> '{heading}' ILIKE '%— user%' OR du #>> '{heading}' ILIKE '%- user%')",
        created_at:       'h.created_at',
        tag_frequency:    `(SELECT COALESCE(sum(f.tc), 0)
           FROM unnest(h.tags) tg
           JOIN (SELECT t AS tag, count(*) AS tc FROM nebula.harvests h2, unnest(h2.tags) AS t GROUP BY t) f
             ON f.tag = tg)`,
        keyword_hits:     `(SELECT count(*) FROM jsonb_array_elements(h.docklang -> 'discourse_units') du
            WHERE du #>> '{body}' ILIKE '%' || $1 || '%')`,
      }

      const keyword = qs.keyword as string | undefined
      if (sort === 'keyword_hits' && !keyword) {
        response.status(400).json({ error: 'keyword query parameter is required when sort=keyword_hits' })
        return
      }

      const clauses: string[] = []
      const filterParams: any[] = []
      const params: any[] = []
      let pi = 1
      if (sort === 'keyword_hits' && keyword) { params.push(keyword); pi++ }
      if (model) { clauses.push(`h.model = $${pi++}`); params.push(model); filterParams.push(model) }
      if (version) { clauses.push(`h.version = $${pi++}`); params.push(parseInt(version)); filterParams.push(parseInt(version)) }
      if (sourceHash) { clauses.push(`h.source_hash = $${pi++}`); params.push(sourceHash); filterParams.push(sourceHash) }
      if (level) { clauses.push(`h.level = $${pi++}`); params.push(parseInt(level)); filterParams.push(parseInt(level)) }
      if (visibilityScope) { clauses.push(`h.visibility_scope = $${pi++}`); params.push(visibilityScope); filterParams.push(visibilityScope) }
      if (tag) { clauses.push(`$${pi++} = ANY(h.tags)`); params.push(tag); filterParams.push(tag) }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const countWhere = clauses.length > 0
        ? 'WHERE ' + clauses.map((c, i) => c.replace(/\$\d+/, `$${i + 1}`)).join(' AND ')
        : ''

      // Replace the injected $n placeholders in sortExpr with knex ? at the
      // end — the dataQuery uses $1 for keyword already; we keep SQL verbatim
      // and let toKnex() convert. The sort expression's `?` for keyword is
      // bound via params[0] when sort=keyword_hits.
      const keywordExpr = sort === 'keyword_hits'
        ? `(SELECT count(*) FROM jsonb_array_elements(s.docklang -> 'discourse_units') du WHERE du #>> '{body}' ILIKE '%' || $1 || '%') AS keyword_hits`
        : '0::bigint AS keyword_hits'
      const tagFreqExpr = sort === 'tag_frequency'
        ? `(SELECT COALESCE(sum(freq), 0) FROM (SELECT count(*) AS freq FROM nebula.harvests h2, unnest(h2.tags) AS t WHERE t = ANY(s.tags) GROUP BY t) sub) AS tag_frequency`
        : '0::bigint AS tag_frequency'

      const dataQuery = `
        SELECT s.id, s.source_path, s.source_filename, s.model,
               s.total_candidates, s.tags, s.metadata, s.created_at,
               s.level, s.visibility_scope,
               s.source_hash, s.file_size, s.version, s.run_metadata,
               COALESCE((s.docklang #>> '{stats,by_type,code}')::int, 0) AS code_blocks,
               COALESCE(jsonb_array_length(s.docklang -> 'discourse_units'), 0) AS turns,
               CASE WHEN jsonb_array_length(s.docklang -> 'discourse_units') > 0
                    THEN (s.docklang #>> '{stats,total_blocks}')::numeric / jsonb_array_length(s.docklang -> 'discourse_units')
                    ELSE 0 END AS blocks_per_turn,
               (SELECT count(*) FROM jsonb_array_elements(s.docklang -> 'discourse_units') du
                WHERE du #>> '{heading}' ILIKE '%— user%' OR du #>> '{heading}' ILIKE '%- user%') AS user_turns,
               ${keywordExpr},
               ${tagFreqExpr}
        FROM (
          SELECT h.id, h.source_path, h.source_filename, h.model,
                 h.total_candidates, h.tags, h.metadata, h.created_at,
                 h.level, h.visibility_scope,
                 h.source_hash, h.file_size, h.version, h.run_metadata,
                 h.docklang
          FROM nebula.harvests h
          ${where}
          ORDER BY ${sortExpr[sort]} DESC NULLS LAST
          LIMIT $${pi} OFFSET $${pi + 1}
        ) s`

      const countQuery = `SELECT COUNT(*)::int AS total FROM nebula.harvests h ${countWhere}`

      params.push(pageSize, (page - 1) * pageSize)
      const [dataResult, countResult] = await Promise.all([
        q(dataQuery, params),
        q(countQuery, filterParams),
      ])

      const items = dataResult.rows.map(camelCaseRow)
      const total = parseInt(countResult.rows[0].total, 10)
      response.json({ items, harvests: items, total, count: total, page, pageSize, sort })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/harvests/distribution */
  async harvestDistribution(_ctx: HttpContext, response: any) {
    try {
      const { rows: turnBuckets } = await q(`
        SELECT
          CASE
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') = 0 THEN '0'
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 1 AND 1 THEN '1'
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 2 AND 3 THEN '2-3'
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 4 AND 6 THEN '4-6'
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 7 AND 10 THEN '7-10'
            WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 11 AND 20 THEN '11-20'
            ELSE '20+'
          END AS bucket,
          count(*) AS harvest_count
        FROM nebula.harvests h
        WHERE h.docklang IS NOT NULL AND h.docklang != '{}'::jsonb
        GROUP BY bucket
        ORDER BY min(CASE
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') = 0 THEN 0
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 1 AND 1 THEN 1
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 2 AND 3 THEN 2
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 4 AND 6 THEN 4
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 7 AND 10 THEN 7
          WHEN jsonb_array_length(h.docklang -> 'discourse_units') BETWEEN 11 AND 20 THEN 11
          ELSE 21
        END)
      `)

      const { rows: blockTypes } = await q(`
        SELECT block_type, count(*) AS cnt
        FROM nebula.harvest_blocks()
        GROUP BY block_type ORDER BY cnt DESC
      `)

      const { rows: topTags } = await q(`
        SELECT t AS tag, count(*) AS cnt
        FROM nebula.harvests h, unnest(h.tags) AS t
        WHERE h.tags IS NOT NULL AND array_length(h.tags, 1) > 0
        GROUP BY t ORDER BY cnt DESC LIMIT 20
      `)

      const { rows: [totals] } = await q(
        'SELECT count(*) AS total_harvests, sum(total_candidates)::int AS total_candidates, avg(total_candidates)::numeric(5,1) AS avg_candidates_per_harvest FROM nebula.harvests'
      )

      response.json({
        turnDistribution: turnBuckets,
        blockTypeDistribution: blockTypes,
        topTags,
        totals,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/harvests/:id */
  async getHarvest({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM nebula.harvests WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Harvest not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/harvests/:id/transcript */
  async harvestTranscript({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [harvest] } = await q(
        `SELECT id, source_filename, docklang #>> '{meta,title}' AS title FROM nebula.harvests WHERE id = ?`,
        [id]
      )
      if (!harvest) {
        response.status(404).json({ error: 'Harvest not found' })
        return
      }

      const { rows: units } = await q(`
        SELECT
          (du_elem #>> '{provenance,turn_index}')::int AS turn_index,
          du_elem #>> '{heading}' AS heading,
          du_elem #>> '{provenance,role}' AS role,
          du_elem #>> '{body}' AS body,
          (du_elem #>> '{provenance,block_count}')::int AS block_count,
          jsonb_agg(
            jsonb_build_object(
              'index', (b #>> '{provenance,block_index}')::int,
              'type', b #>> '{type}',
              'content', CASE WHEN jsonb_exists(b, 'content') THEN b #>> '{content}' ELSE NULL END,
              'items', CASE WHEN jsonb_exists(b, 'items') THEN b -> 'items' ELSE NULL END
            ) ORDER BY (b #>> '{provenance,block_index}')::int
          ) AS blocks
        FROM nebula.harvests h,
             LATERAL jsonb_array_elements(h.docklang -> 'discourse_units') AS du_elem,
             LATERAL jsonb_array_elements(du_elem -> 'blocks') AS b
        WHERE h.id = ? AND h.docklang IS NOT NULL AND jsonb_exists(h.docklang, 'discourse_units')
        GROUP BY turn_index, heading, role, body, block_count
        ORDER BY turn_index
      `, [id])

      const { rows: [stats] } = await q(
        "SELECT h.docklang -> 'stats' AS stats FROM nebula.harvests h WHERE h.id = ?",
        [id]
      )

      const { rows: candidates } = await q(
        'SELECT id, title, status, completed, system_id, intent_description FROM nebula.harvest_candidates WHERE harvest_id = ? ORDER BY created_at',
        [id]
      )

      let snapshotId: string | null = null
      let committedSegments: any[] = []
      let activeOverrides: any[] = []
      try {
        const { rows: snapshots } = await q(
          `SELECT id FROM nebula.conversation_snapshots
           WHERE conversation_id = ?
           ORDER BY snapshot_index DESC LIMIT 1`,
          [id]
        )
        if (snapshots.length > 0) {
          snapshotId = snapshots[0].id
          const { rows: segments } = await q(
            `SELECT id, conversation_id, snapshot_id, start_block_id, end_block_id,
                    start_block_index, end_block_index, segment_type, state, source,
                    title, notes_md, created_by,
                    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
             FROM nebula.segments
             WHERE snapshot_id = ?
             ORDER BY start_block_index`,
            [snapshotId]
          )
          committedSegments = segments
          const { rows: overrides } = await q(
            `SELECT id, conversation_id, snapshot_id, target_type, target_id,
                    projection_target, override_type, reason_code, notes_md,
                    source, created_by,
                    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
             FROM nebula.projection_overrides
             WHERE snapshot_id = ?
             ORDER BY created_at`,
            [snapshotId]
          )
          activeOverrides = overrides
        }
      } catch (_) {
        // snapshot tables may not exist yet — non-fatal
      }

      response.json({
        harvestId: id,
        conversationId: id,
        snapshotId,
        title: harvest.title,
        source: harvest.source_filename,
        units,
        stats: stats?.stats || null,
        candidates,
        committedSegments,
        activeOverrides,
      })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/harvest-candidates/:id/promote */
  async promoteCandidate({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { status = 'useful' } = request.body()
      const validStatuses = ['useful', 'rejected', 'promoted']
      if (!validStatuses.includes(status)) {
        response.status(400).json({ error: `status must be one of: ${validStatuses.join(', ')}` })
        return
      }
      const { rows: [result] } = await q('SELECT nebula.set_candidate_status(?, ?) AS result', [id, status])
      response.json({ ok: true, result: result?.result })
    } catch (e: any) {
      const { status, body } = err(e, 400)
      response.status(status).json(body)
    }
  }

  /** POST /api/harvest-candidates/promote-to-plan
   * RETIRED (D-2026-08-23-C): the backing procedure nebula.candidates_to_plan
   * no longer exists and must NOT be resurrected. Mirrors the nebula-srv 410
   * tombstone so callers migrate to the canonical promotion path instead of
   * 400-ing mysteriously. Canonical: POST /api/harvest-candidates/:id/spawn-plan
   */
  async promoteToPlan({ response }: HttpContext) {
    response.status(410).json({
      error:
        'promote-to-plan was retired: its backing procedure (nebula.candidates_to_plan) no longer exists and will not be restored.',
      useInstead: 'POST /api/harvest-candidates/:id/spawn-plan',
      mcpTool: 'nebula_spawn_plan_from_candidate',
      rationale:
        'Single canonical promotion flow; the legacy parallel route created two competing promotion paths. See architect ruling on to-do e68449f2.',
    })
  }

  /** POST /api/harvests */
  async createHarvest({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { sourcePath, sourceFilename, model, totalCandidates, candidates, sourceText, tags, metadata, level, visibilityScope, sourceHash, fileSize, runMetadata, docklang } = body
      if (!sourcePath) {
        await trx.rollback()
        response.status(400).json({ error: 'sourcePath is required' })
        return
      }

      const { rows: [row] } = await qT(
        trx,
        `INSERT INTO nebula.harvests (source_path, source_filename, model, total_candidates, candidates, source_text, tags, metadata, level, visibility_scope, source_hash, file_size, run_metadata, docklang)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [
          sourcePath,
          sourceFilename || '',
          model || '',
          totalCandidates || 0,
          JSON.stringify(candidates || []),
          sourceText || null,
          tags || [],
          metadata || {},
          level ?? 1,
          visibilityScope || 'all',
          sourceHash || null,
          fileSize || null,
          runMetadata || {},
          docklang || null,
        ]
      )

      const candidateList: any[] = candidates || []
      for (const c of candidateList) {
        await qT(
          trx,
          `INSERT INTO nebula.harvest_candidates (harvest_id, title, intent_description, implementation_notes, code_snippets, open_questions, tags, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            row.id,
            c.title || 'Untitled',
            c.intentDescription || c.intent_description || null,
            JSON.stringify(c.implementationNotes || c.implementation_notes || []),
            JSON.stringify(c.codeSnippets || c.code_snippets || []),
            JSON.stringify(c.openQuestions || c.open_questions || []),
            c.tags || [],
            c.status || c.promotionStatus || null,
          ]
        )
      }

      await trx.commit()
      response.status(201).json(row)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** DELETE /api/harvests/:id */
  async deleteHarvest({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      await qT(trx, 'UPDATE nebula.harvest_candidates SET valid_until = now() WHERE harvest_id = ? AND valid_until > now()', [id])
      const { rowCount } = await qT(trx, 'UPDATE nebula.harvests SET valid_until = now() WHERE id = ? AND valid_until > now()', [id])
      if (rowCount === 0) {
        await trx.rollback()
        response.status(404).json({ error: 'Harvest not found' })
        return
      }
      await trx.commit()
      response.json({ expired: true })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── HARVEST CANDIDATES ──────────────────────────────────────────────

  /** GET /api/plans/:planRef/candidates */
  async planCandidates({ request, response }: HttpContext) {
    try {
      const { planRef } = request.params()
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source,
                  cr.created_at AS linked_at
           FROM nebula.harvest_candidates hc
           JOIN nebula.cross_references cr ON cr.source_id = hc.id::text
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE cr.source_type = 'harvest_candidate'
             AND cr.target_type = 'plan'
             AND cr.target_id = ?
             AND cr.rel_type = 'ag:spawns_plan'
           ORDER BY cr.created_at DESC
           LIMIT ? OFFSET ?`,
          [planRef, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           JOIN nebula.cross_references cr ON cr.source_id = hc.id::text
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE cr.source_type = 'harvest_candidate'
             AND cr.target_type = 'plan'
             AND cr.target_id = ?
             AND cr.rel_type = 'ag:spawns_plan'`,
          [planRef]
        ),
      ])
      response.json({ planRef, items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/harvest-candidates */
  async listHarvestCandidates({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { harvestId, systemId, subsystemId, featureId } = qs
      const { offset, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (harvestId) { clauses.push('hc.harvest_id = ?'); vals.push(harvestId) }
      if (systemId) { clauses.push('hc.system_id = ?'); vals.push(systemId) }
      if (subsystemId) { clauses.push('hc.subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId) { clauses.push('hc.feature_id = ?'); vals.push(featureId) }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description, hc.status, hc.tags,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.work_request_id, hc.completed,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}
           ORDER BY hc.created_at DESC LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}`,
          vals
        ),
      ])

      const items = dataResult.rows.map(camelCaseRow)
      const total = parseInt(countResult.rows[0].total, 10)
      response.json({ items, candidates: items, total, count: total, page, pageSize })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/harvest-candidates/:id */
  async getHarvestCandidate({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM nebula.harvest_candidates WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Harvest candidate not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/candidates — alias for Assembly UI */
  async listCandidatesAlias({ request, response }: HttpContext) {
    try {
      const qs = request.qs()
      const { harvestId, systemId, subsystemId, featureId } = qs
      const { offset, limit, page, pageSize } = parsePagination(qs)

      const clauses: string[] = []
      const vals: any[] = []
      if (harvestId) { clauses.push('hc.harvest_id = ?'); vals.push(harvestId) }
      if (systemId) { clauses.push('hc.system_id = ?'); vals.push(systemId) }
      if (subsystemId) { clauses.push('hc.subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId) { clauses.push('hc.feature_id = ?'); vals.push(featureId) }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : ''

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description, hc.status, hc.tags,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.work_request_id, hc.completed,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}
           ORDER BY hc.created_at DESC LIMIT ? OFFSET ?`,
          [...vals, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}`,
          vals
        ),
      ])

      const items = dataResult.rows.map(camelCaseRow)
      const total = parseInt(countResult.rows[0].total, 10)
      response.json({ items, total, page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/candidates/:id */
  async getCandidateAlias({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q('SELECT * FROM nebula.harvest_candidates WHERE id = ?', [id])
      if (!row) {
        response.status(404).json({ error: 'Candidate not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** PATCH /api/harvest-candidates/:id */
  async updateHarvestCandidate({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const body = request.body()
      const { title, intentDescription, status, systemId, subsystemId, featureId, tags, planRef, workRequestId, completed,
              type, designRationale, provenanceBlockIndices, needsNewNode, proposedParent, proposedName, placementReason } = body

      const sets: string[] = []
      const vals: any[] = []
      if (title !== undefined) { sets.push('title = ?'); vals.push(title) }
      if (intentDescription !== undefined) { sets.push('intent_description = ?'); vals.push(intentDescription) }
      if (status !== undefined) { sets.push('status = ?'); vals.push(status) }
      if (systemId !== undefined) { sets.push('system_id = ?'); vals.push(systemId) }
      if (subsystemId !== undefined) { sets.push('subsystem_id = ?'); vals.push(subsystemId) }
      if (featureId !== undefined) { sets.push('feature_id = ?'); vals.push(featureId) }
      if (tags !== undefined) { sets.push('tags = ?'); vals.push(tags) }
      if (workRequestId !== undefined) { sets.push('work_request_id = ?'); vals.push(workRequestId) }
      if (completed !== undefined) { sets.push('completed = ?'); vals.push(completed) }
      if (type !== undefined) { sets.push('type = ?'); vals.push(type) }
      if (designRationale !== undefined) { sets.push('design_rationale = ?'); vals.push(JSON.stringify(designRationale)) }
      if (provenanceBlockIndices !== undefined) { sets.push('provenance_block_indices = ?'); vals.push(JSON.stringify(provenanceBlockIndices)) }
      if (needsNewNode !== undefined) { sets.push('needs_new_node = ?'); vals.push(needsNewNode) }
      if (proposedParent !== undefined) { sets.push('proposed_parent = ?'); vals.push(proposedParent) }
      if (proposedName !== undefined) { sets.push('proposed_name = ?'); vals.push(proposedName) }
      if (placementReason !== undefined) { sets.push('placement_reason = ?'); vals.push(placementReason) }

      const planRefProvided = hasPlanRef(planRef)
      const hasChanges = sets.length > 0 || planRefProvided
      if (!hasChanges) {
        await trx.commit()
        response.json({ ok: true })
        return
      }

      let row: any
      if (sets.length > 0) {
        vals.push(id)
        const result = await qT(trx, `UPDATE nebula.harvest_candidates SET ${sets.join(', ')} WHERE id = ? RETURNING *`, vals)
        row = result.rows[0]
      } else {
        const result = await qT(trx, 'SELECT * FROM nebula.harvest_candidates WHERE id = ?', [id])
        row = result.rows[0]
      }
      if (!row) {
        await trx.rollback()
        response.status(404).json({ error: 'Harvest candidate not found' })
        return
      }

      const shouldUpsert = systemId !== undefined && row.system_id && row.intent_description
      if (shouldUpsert) {
        await upsertHarvestContextTab(trx, row.system_id, row)
      }

      await createSpawnsPlanCrossRef(trx, row.id, planRef, {
        candidateTitle: row.title,
        harvestId: row.harvest_id,
        systemId: row.system_id,
        linkedAt: new Date().toISOString(),
      })

      await trx.commit()
      response.json(row)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/harvest-candidates/:id/spawn-plan */
  async spawnPlan({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const { id } = request.params()
      const body = request.body()
      const {
        systemId,
        subsystemId = null,
        featureId = null,
        planRef,
        priority = 'Medium',
        status = 'Backlog',
        title,
        description,
        parentId = null,
        reqType = null,
        acceptanceCriteria = null,
      } = body

      if (!systemId) {
        await trx.rollback()
        response.status(400).json({ error: 'systemId is required' })
        return
      }
      if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) {
        await trx.rollback()
        response.status(400).json({ error: `reqType must be one of: ${REQ_TYPES.join(', ')}` })
        return
      }

      const { rows: [candidate] } = await qT(trx, 'SELECT * FROM nebula.harvest_candidates WHERE id = ?', [id])
      if (!candidate) {
        await trx.rollback()
        response.status(404).json({ error: 'Harvest candidate not found' })
        return
      }

      const { rows: [updatedCandidate] } = await qT(
        trx,
        `UPDATE nebula.harvest_candidates
         SET system_id = ?, subsystem_id = ?, feature_id = ?
         WHERE id = ? RETURNING *`,
        [systemId, subsystemId, featureId, id]
      )

      if (candidate.intent_description) {
        await upsertHarvestContextTab(trx, systemId, candidate)
      }

      const reqTitle = title || candidate.title
      const reqDescription = description || candidate.intent_description || ''
      const normalizedStatus = normalizeStatus(status)
      if (!normalizedStatus) {
        await trx.rollback()
        response.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` })
        return
      }
      const planRefStr = hasPlanRef(planRef) ? String(planRef).trim() : null
      const { rows: [requirement] } = await qT(
        trx,
        `INSERT INTO requirements (system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date, parent_id, req_type, acceptance_criteria, candidate_id, conduit_plan_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [systemId, subsystemId, featureId, reqTitle, reqDescription, normalizedStatus, priority, null, null, parentId, reqType, acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null, candidate.id, planRefStr]
      )

      const crossRef = await createSpawnsPlanCrossRef(trx, candidate.id, planRef, {
        candidateTitle: candidate.title,
        harvestId: candidate.harvest_id,
        systemId,
        requirementId: requirement.id,
        linkedAt: new Date().toISOString(),
      })

      await trx.commit()

      response.status(201).json({
        candidate: updatedCandidate,
        requirement: {
          ...toEpochMs(requirement, 'created_at'),
          systemId: requirement.system_id,
          subsystemId: requirement.subsystem_id,
          featureId: requirement.feature_id,
          startDate: requirement.start_date,
          completionDate: requirement.completion_date,
          parentId: requirement.parent_id,
          reqType: requirement.req_type,
          acceptanceCriteria: requirement.acceptance_criteria,
          candidateId: requirement.candidate_id,
          conduitPlanId: requirement.conduit_plan_id,
        },
        crossReference: crossRef
          ? { ...toEpochMs(crossRef, 'created_at'), sourceType: crossRef.source_type, sourceId: crossRef.source_id, targetType: crossRef.target_type, targetId: crossRef.target_id, relType: crossRef.rel_type }
          : null,
      })
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/harvest-candidates */
  async createHarvestCandidate({ request, response }: HttpContext) {
    const trx = await db.transaction()
    try {
      const body = request.body()
      const { harvestId, title, intentDescription, implementationNotes, codeSnippets, openQuestions, tags, status, systemId, subsystemId, featureId, planRef,
              type, designRationale, provenanceBlockIndices, needsNewNode, proposedParent, proposedName, placementReason } = body
      if (!harvestId || !title) {
        await trx.rollback()
        response.status(400).json({ error: 'harvestId and title are required' })
        return
      }

      const { rows: [row] } = await qT(
        trx,
        `INSERT INTO nebula.harvest_candidates (harvest_id, title, intent_description, implementation_notes, code_snippets, open_questions, tags, status, system_id, subsystem_id, feature_id,
                                               type, design_rationale, provenance_block_indices, needs_new_node, proposed_parent, proposed_name, placement_reason)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
        [
          harvestId, title,
          intentDescription || null,
          JSON.stringify(implementationNotes || []),
          JSON.stringify(codeSnippets || []),
          JSON.stringify(openQuestions || []),
          tags || [], status || null,
          systemId || null, subsystemId || null, featureId || null,
          type || 'requirement',
          JSON.stringify(designRationale || []),
          JSON.stringify(provenanceBlockIndices || []),
          needsNewNode || false,
          proposedParent || null,
          proposedName || null,
          placementReason || null,
        ]
      )

      if (row.system_id && row.intent_description) {
        await upsertHarvestContextTab(trx, row.system_id, row)
      }

      await createSpawnsPlanCrossRef(trx, row.id, planRef, {
        candidateTitle: row.title,
        harvestId: row.harvest_id,
        systemId: row.system_id,
        linkedAt: new Date().toISOString(),
      })

      await trx.commit()
      response.status(201).json(row)
    } catch (e: any) {
      await trx.rollback().catch(() => {})
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/specifications/:id/link-requirements */
  async linkSpecRequirements({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [spec] } = await q('SELECT id, item_snapshot FROM nebula.specifications WHERE id = ?', [id])
      if (!spec) {
        response.status(404).json({ error: 'Specification not found' })
        return
      }

      const directCandidateIds: string[] = []
      const items = spec.item_snapshot || []
      for (const item of items) {
        if (!item.source_id) continue
        if (item.source_type === 'harvest_candidate') {
          directCandidateIds.push(item.source_id)
        }
      }

      const candidateIds = [...new Set(directCandidateIds)]

      if (candidateIds.length === 0) {
        response.json({ ok: true, linked: 0, message: 'No harvest_candidate items in snapshot' })
        return
      }

      const { rows: reqs } = await q('SELECT id, title FROM nebula.requirements WHERE candidate_id = ANY(?::uuid[])', [candidateIds])

      let linked = 0
      const trx = await db.transaction()
      try {
        for (const req of reqs) {
          const { rowCount } = await qT(
            trx,
            `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
             SELECT 'specification', ?, 'requirement', ?, 'spec:defines_req', '{}'::jsonb
             WHERE NOT EXISTS (
               SELECT 1 FROM nebula.cross_references_history
               WHERE source_type = 'specification'
                 AND source_id = ?
                 AND target_type = 'requirement'
                 AND target_id = ?
                 AND rel_type = 'spec:defines_req'
                 AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
             )
             ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
               WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
             DO NOTHING`,
            [id, req.id, id, req.id]
          )
          linked += rowCount ?? 0
        }
        await trx.commit()
      } catch (txErr) {
        await trx.rollback().catch(() => {})
        throw txErr
      }

      response.json({ ok: true, linked, candidate_count: candidateIds.length, requirement_count: reqs.length })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── AGENDAS ─────────────────────────────────────────────────────────

  /** GET /api/agendas */
  async listAgendas({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT a.*,
                  (SELECT jsonb_agg(jsonb_build_object(
                    'id', ai.id, 'source_type', ai.source_type, 'source_id', ai.source_id,
                    'title', ai.title, 'body', ai.body, 'decisions', ai.decisions,
                    'open_questions', ai.open_questions, 'supporting_refs', ai.supporting_refs,
                    'included', ai.included, 'planner_note', ai.planner_note,
                    'created_at', ai.created_at
                  ) ORDER BY ai.created_at)
                   FROM nebula.agenda_items ai WHERE ai.agenda_id = a.id) AS items,
                  (SELECT count(*) FROM nebula.agenda_items ai WHERE ai.agenda_id = a.id) AS item_count
           FROM nebula.agendas a
           ORDER BY a.created_at DESC
           LIMIT ? OFFSET ?`,
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.agendas'),
      ])
      response.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/agendas/:id */
  async getAgenda({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q(
        `SELECT a.*,
                (SELECT jsonb_agg(jsonb_build_object(
                  'id', ai.id, 'source_type', ai.source_type, 'source_id', ai.source_id,
                  'title', ai.title, 'body', ai.body, 'decisions', ai.decisions,
                  'open_questions', ai.open_questions, 'supporting_refs', ai.supporting_refs,
                  'included', ai.included, 'planner_note', ai.planner_note,
                  'created_at', ai.created_at
                ) ORDER BY ai.created_at)
                 FROM nebula.agenda_items ai WHERE ai.agenda_id = a.id) AS items,
                (SELECT count(*) FROM nebula.agenda_items ai WHERE ai.agenda_id = a.id) AS item_count
         FROM nebula.agendas a
         WHERE a.id = ?`,
        [id]
      )
      if (!row) {
        response.status(404).json({ error: 'Agenda not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  private async scopedAgendas(ctx: HttpContext, response: any, scopeField: string) {
    try {
      const { id } = ctx.request.params()
      const { offset, limit, page, pageSize } = parsePagination(ctx.request.qs())

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT DISTINCT a.id, a.title, a.scope, a.status, a.cohesion_score,
                  a.overlap_matrix, a.source_count, a.planner_analysis,
                  a.planner_conflicts, a.planner_gaps, a.metadata,
                  a.created_at, a.updated_at
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.${scopeField} = ?
           ORDER BY a.created_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(DISTINCT a.id)::int AS total
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.${scopeField} = ?`,
          [id]
        ),
      ])

      const items = dataResult.rows
      for (const a of items) {
        const { rows: agendaItems } = await q(
          `SELECT ai.id, ai.source_type, ai.source_id, ai.title, ai.body,
                  ai.decisions, ai.open_questions, ai.supporting_refs,
                  ai.included, ai.planner_note, ai.created_at, ai.updated_at
           FROM nebula.agenda_items ai
           WHERE ai.agenda_id = ?
           ORDER BY ai.created_at ASC`,
          [a.id]
        )
        a.items = agendaItems
        a.item_count = agendaItems.length
      }

      response.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/agendas */
  async systemAgendas(ctx: HttpContext, response: any) { return this.scopedAgendas(ctx, response, 'system_id') }
  /** GET /api/subsystems/:id/agendas */
  async subsystemAgendas(ctx: HttpContext, response: any) { return this.scopedAgendas(ctx, response, 'subsystem_id') }
  /** GET /api/features/:id/agendas */
  async featureAgendas(ctx: HttpContext, response: any) { return this.scopedAgendas(ctx, response, 'feature_id') }

  /** DELETE /api/agendas/:id/items */
  async deleteAgendaItem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const sourceId = request.qs().sourceId as string
      if (!sourceId) {
        response.status(400).json({ error: 'sourceId query parameter is required' })
        return
      }
      const { rowCount } = await q(
        `UPDATE nebula.agenda_items SET valid_until = now() WHERE agenda_id = ? AND source_id = ? AND valid_until > now()`,
        [id, sourceId]
      )
      if (rowCount === 0) {
        response.status(404).json({ error: 'Agenda item not found' })
        return
      }
      response.json({ ok: true, deleted: rowCount })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/agendas/:id/finalize */
  async finalizeAgenda({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { revisionType = 'created' } = request.body()
      const { rows: [result] } = await q(
        'SELECT nebula.agenda_to_specification(?, ?) AS spec_id',
        [id, revisionType]
      )
      response.status(201).json({ ok: true, spec_id: result.spec_id })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** POST /api/agendas/:id/items */
  async addAgendaItem({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const body = request.body()
      const { sourceType, sourceId, title, body: itemBody, decisions, openQuestions, supportingRefs, included, plannerNote } = body
      if (!sourceType || !sourceId || !title) {
        response.status(400).json({ error: 'sourceType, sourceId, and title are required' })
        return
      }
      const { rows: [item] } = await q(
        'INSERT INTO nebula.agenda_items (agenda_id, source_type, source_id, title, body, decisions, open_questions, supporting_refs, included, planner_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *',
        [id, sourceType, sourceId, title, itemBody || null, JSON.stringify(decisions || []), JSON.stringify(openQuestions || []), JSON.stringify(supportingRefs || []), included ?? true, plannerNote || null]
      )
      response.status(201).json({ ok: true, item })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── SPECIFICATIONS ──────────────────────────────────────────────────

  /** GET /api/specifications */
  async listSpecifications({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT s.id, s.agenda_id,
                 s.item_snapshot AS items,
                 (SELECT count(*) FROM nebula.cross_references cr
                  WHERE cr.source_type = 'specification'
                    AND cr.source_id = s.id::text
                    AND cr.rel_type = 'spec:defines_req')::int AS linked_requirement_count,
                  s.valid_from AS item_created_at,
                  s.created_at AS item_updated_at,
                  s.revision_number,
                  s.revision_type,
                  s.change_summary,
                  s.agenda_title,
                  s.agenda_status
           FROM nebula.active_specifications s
           ORDER BY s.created_at DESC
           LIMIT ? OFFSET ?`,
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.active_specifications'),
      ])
      response.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/specifications/:id */
  async getSpecification({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q(
        `SELECT s.id, s.agenda_id,
               s.item_snapshot AS items,
               (SELECT count(*) FROM nebula.cross_references cr
                WHERE cr.source_type = 'specification'
                  AND cr.source_id = s.id::text
                  AND cr.rel_type = 'spec:defines_req')::int AS linked_requirement_count,
                s.valid_from AS item_created_at,
                s.created_at AS item_updated_at,
                s.revision_number,
                s.revision_type,
                s.change_summary,
                s.agenda_title,
                s.agenda_status
         FROM nebula.active_specifications s
         WHERE s.id = ?`,
        [id]
      )
      if (!row) {
        response.status(404).json({ error: 'Specification not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  private async scopedSpecifications(ctx: HttpContext, response: any, scopeField: string) {
    try {
      const { id } = ctx.request.params()
      const { offset, limit, page, pageSize } = parsePagination(ctx.request.qs())

      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT s.id, s.agenda_id,
                 s.item_snapshot AS items,
                 (SELECT count(*) FROM nebula.cross_references cr
                  WHERE cr.source_type = 'specification'
                    AND cr.source_id = s.id::text
                    AND cr.rel_type = 'spec:defines_req')::int AS linked_requirement_count,
                  s.valid_from AS item_created_at,
                  s.created_at AS item_updated_at,
                  s.revision_number,
                  s.revision_type,
                  s.change_summary,
                  s.agenda_title,
                  s.agenda_status
           FROM nebula.active_specifications s
           LEFT JOIN nebula.requirements req ON EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_id' = req.id::text)
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_type' = 'requirement')
           WHERE req.${scopeField} = ?
           ORDER BY s.created_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.active_specifications s
           LEFT JOIN nebula.requirements req ON EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_id' = req.id::text)
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_type' = 'requirement')
           WHERE req.${scopeField} = ?`,
          [id]
        ),
      ])

      response.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/specifications */
  async systemSpecifications(ctx: HttpContext, response: any) { return this.scopedSpecifications(ctx, response, 'system_id') }
  /** GET /api/subsystems/:id/specifications */
  async subsystemSpecifications(ctx: HttpContext, response: any) { return this.scopedSpecifications(ctx, response, 'subsystem_id') }
  /** GET /api/features/:id/specifications */
  async featureSpecifications(ctx: HttpContext, response: any) { return this.scopedSpecifications(ctx, response, 'feature_id') }

  // ── WORK REQUESTS ───────────────────────────────────────────────────

  /** GET /api/work-requests */
  async listWorkRequests({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT wr.*
           FROM nebula.work_requests wr
           ORDER BY wr.created_at DESC
           LIMIT ? OFFSET ?`,
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.work_requests'),
      ])
      response.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/work-requests/:id */
  async getWorkRequest({ request, response }: HttpContext) {
    try {
      const { id } = request.params()
      const { rows: [row] } = await q(`SELECT wr.* FROM nebula.work_requests wr WHERE wr.id = ?`, [id])
      if (!row) {
        response.status(404).json({ error: 'Work request not found' })
        return
      }
      response.json(row)
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  private async scopedWorkRequests(ctx: HttpContext, response: any, scopeField: string) {
    try {
      const { id } = ctx.request.params()
      const { offset, limit, page, pageSize } = parsePagination(ctx.request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
           `SELECT DISTINCT ON (wr.id) wr.*
           FROM nebula.work_requests wr
${REQUIREMENT_LINK_JOIN}
           WHERE req.${scopeField} = ?
           ORDER BY wr.id, wr.created_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(DISTINCT wr.id)::int AS total
           FROM nebula.work_requests wr
${REQUIREMENT_LINK_JOIN}
           WHERE req.${scopeField} = ?`,
          [id]
        ),
      ])
      response.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/work-requests */
  async systemWorkRequests(ctx: HttpContext, response: any) { return this.scopedWorkRequests(ctx, response, 'system_id') }
  /** GET /api/subsystems/:id/work-requests */
  async subsystemWorkRequests(ctx: HttpContext, response: any) { return this.scopedWorkRequests(ctx, response, 'subsystem_id') }
  /** GET /api/features/:id/work-requests */
  async featureWorkRequests(ctx: HttpContext, response: any) { return this.scopedWorkRequests(ctx, response, 'feature_id') }

  // ── HIERARCHY-SCOPED HARVEST CANDIDATES ─────────────────────────────

  private async scopedHarvestCandidates(ctx: HttpContext, response: any, scopeField: string) {
    try {
      const { id } = ctx.request.params()
      const { offset, limit, page, pageSize } = parsePagination(ctx.request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.${scopeField} = ?
           ORDER BY hc.created_at DESC
           LIMIT ? OFFSET ?`,
          [id, pageSize, offset]
        ),
        q(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.${scopeField} = ?`,
          [id]
        ),
      ])
      const items = dataResult.rows.map(camelCaseRow)
      const total = parseInt(countResult.rows[0].total, 10)
      response.json({ [scopeField.replace(/_id$/, 'Id')]: id, items, candidates: items, total, count: total, page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/systems/:id/harvest-candidates */
  async systemHarvestCandidates(ctx: HttpContext, response: any) { return this.scopedHarvestCandidates(ctx, response, 'system_id') }
  /** GET /api/subsystems/:id/harvest-candidates */
  async subsystemHarvestCandidates(ctx: HttpContext, response: any) { return this.scopedHarvestCandidates(ctx, response, 'subsystem_id') }
  /** GET /api/features/:id/harvest-candidates */
  async featureHarvestCandidates(ctx: HttpContext, response: any) { return this.scopedHarvestCandidates(ctx, response, 'feature_id') }

  // ── SPECS (flattened agenda_items) ──────────────────────────────────

  private specItem(row: any) {
    return {
      id: row.id,
      agendaId: row.agenda_id,
      sourceType: row.source_type || null,
      sourceId: row.source_id || null,
      title: row.title,
      body: row.body || null,
      decisions: row.decisions || null,
      openQuestions: row.open_questions || null,
      supportingRefs: row.supporting_refs || null,
      included: row.included != null ? row.included : null,
      plannerNote: row.planner_note || null,
      agendaTitle: row.agenda_title || null,
      agendaStatus: row.agenda_status || null,
      createdAt: new Date(row.item_created_at).toISOString(),
      updatedAt: new Date(row.item_updated_at).toISOString(),
    }
  }

  /** GET /api/specs */
  async listSpecs({ request, response }: HttpContext) {
    try {
      const { offset, limit, page, pageSize } = parsePagination(request.qs())
      const [dataResult, countResult] = await Promise.all([
        q(
          `SELECT
            id, agenda_id, source_type, source_id, title, body,
            decisions, open_questions, supporting_refs, included,
            planner_note, item_created_at, item_updated_at,
            agenda_title, agenda_status
          FROM nebula.specs
          ORDER BY item_created_at DESC
          LIMIT ? OFFSET ?`,
          [pageSize, offset]
        ),
        q('SELECT COUNT(*)::int AS total FROM nebula.specs'),
      ])
      response.json({ items: dataResult.rows.map((r: any) => this.specItem(r)), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset })
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  /** GET /api/specs/:id */
  async getSpec({ request, response }: HttpContext) {
    try {
      const id = request.params().id as string
      if (!isUuid(id)) {
        response.status(400).json({ error: 'id must be a UUID' })
        return
      }
      const { rows } = await q(
        `SELECT
          id, agenda_id, source_type, source_id, title, body,
          decisions, open_questions, supporting_refs, included,
          planner_note, item_created_at, item_updated_at,
          agenda_title, agenda_status
        FROM nebula.specs
        WHERE id = ?`,
        [id]
      )
      if (rows.length === 0) {
        response.status(404).json({ error: 'Spec item not found' })
        return
      }
      response.json(this.specItem(rows[0]))
    } catch (e: any) {
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }

  // ── HARVEST CANDIDATE DISCOVERY ─────────────────────────────────────

  /** POST /api/harvest-candidates/discover */
  async discoverCandidates({ request, response }: HttpContext) {
    try {
      const body = request.body()
      const { candidateIds, limit = 50, threshold = 0.75 } = body

      const candidateLimit = Math.min(parseInt(String(limit)) || 50, 200)
      const rawThreshold = parseFloat(String(threshold))
      const matchThreshold = !isNaN(rawThreshold) && rawThreshold >= 0 && rawThreshold <= 1 ? rawThreshold : 0.75

      if (isNaN(rawThreshold) || rawThreshold < 0 || rawThreshold > 1) {
        response.status(400).json({ error: 'threshold must be a number between 0 and 1' })
        return
      }

      let candidateQuery = `
        SELECT hc.id, hc.title, hc.intent_description, hc.harvest_id,
               h.source_filename AS harvest_source
        FROM nebula.harvest_candidates hc
        LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
        WHERE hc.system_id IS NULL
          AND hc.subsystem_id IS NULL
          AND hc.feature_id IS NULL
          AND NOW() >= hc.valid_from
          AND NOW() < hc.valid_until
      `
      const candidateParams: any[] = []

      if (candidateIds && Array.isArray(candidateIds) && candidateIds.length > 0) {
        candidateQuery += ` AND hc.id = ANY(?::uuid[])`
        candidateParams.push(candidateIds)
      }

      candidateQuery += ` ORDER BY hc.created_at DESC LIMIT ?`
      candidateParams.push(candidateLimit)

      const { rows: candidates } = await q(candidateQuery, candidateParams)

      if (candidates.length === 0) {
        response.json({ candidateCount: 0, matchThreshold, matches: [], undocumented: [] })
        return
      }

      const [systemsRes, subsystemsRes, featuresRes] = await Promise.all([
        q('SELECT id, name, description FROM systems ORDER BY name'),
        q('SELECT s.id, s.name, s.description, s.system_id, sys.name AS system_name FROM subsystems s LEFT JOIN systems sys ON sys.id = s.system_id ORDER BY s.name'),
        q('SELECT f.id, f.name, f.description, f.subsystem_id, sub.name AS subsystem_name, sub.system_id FROM features f LEFT JOIN subsystems sub ON sub.id = f.subsystem_id ORDER BY f.name'),
      ])

      const allSystems = systemsRes.rows
      const allSubsystems = subsystemsRes.rows
      const allFeatures = featuresRes.rows

      const searchPromises = candidates.map(async (cand: any) => {
        const searchQuery = [cand.title, cand.intent_description].filter(Boolean).join(' ').slice(0, 500)

        if (!searchQuery) {
          return {
            candidateId: cand.id,
            candidateTitle: cand.title,
            candidateIntent: cand.intent_description,
            harvestSource: cand.harvest_source,
            curatedMatches: [],
            hierarchyMatches: [],
            topSimilarity: 0,
          }
        }

        let curatedMatches: any[] = []
        let searchFailed = false
        try {
          const { stdout } = await execFileAsync(
            PYTHON_BIN,
            [SCRIPT_PATH, searchQuery, '--limit', '15', '--layers', 'harvest,kg', '--json'],
            { timeout: 60000, maxBuffer: 10 * 1024 * 1024 }
          )
          const parsed = JSON.parse(stdout)
          const results: any[] = parsed.results || []
          curatedMatches = results
            .filter((r: any) => r.provenance === 'curated')
            .map((r: any) => ({
              entityId: r.id,
              entityName: r.title,
              section: r.section || '',
              entityType: r.entity_type || '',
              description: (r.description || '').slice(0, 300),
              similarity: r.similarity,
            }))
        } catch (searchErr: any) {
          console.error(`[discover] Semantic search failed for candidate ${cand.id}:`, searchErr.message)
          searchFailed = true
        }

        const hierarchyMatches: any[] = []
        const searchTokens = searchQuery.toLowerCase().split(/\s+/).filter((t) => t.length > 2)

        for (const sys of allSystems) {
          const sysName = (sys.name || '').toLowerCase()
          const sysDesc = (sys.description || '').toLowerCase()
          const tokenHits = searchTokens.filter((t) => sysName.includes(t) || sysDesc.includes(t)).length
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'system', id: sys.id, name: sys.name,
              description: (sys.description || '').slice(0, 200), parentInfo: undefined,
            })
          }
        }
        for (const sub of allSubsystems) {
          const subName = (sub.name || '').toLowerCase()
          const subDesc = (sub.description || '').toLowerCase()
          const tokenHits = searchTokens.filter((t) => subName.includes(t) || subDesc.includes(t)).length
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'subsystem', id: sub.id, name: sub.name,
              description: (sub.description || '').slice(0, 200),
              parentInfo: `System: ${sub.system_name || 'unknown'}`,
            })
          }
        }
        for (const feat of allFeatures) {
          const featName = (feat.name || '').toLowerCase()
          const featDesc = (feat.description || '').toLowerCase()
          const tokenHits = searchTokens.filter((t) => featName.includes(t) || featDesc.includes(t)).length
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'feature', id: feat.id, name: feat.name,
              description: (feat.description || '').slice(0, 200),
              parentInfo: `Subsystem: ${feat.subsystem_name || 'unknown'}`,
            })
          }
        }

        const topCuratedSimilarity = curatedMatches.length > 0
          ? Math.max(...curatedMatches.map((m) => m.similarity))
          : 0

        const result: any = {
          candidateId: cand.id,
          candidateTitle: cand.title,
          candidateIntent: cand.intent_description,
          harvestSource: cand.harvest_source,
          curatedMatches: curatedMatches.slice(0, 5),
          hierarchyMatches: hierarchyMatches.slice(0, 5),
          topSimilarity: topCuratedSimilarity,
        }
        if (searchFailed) result.searchFailed = true
        return result
      })

      const MAX_CONCURRENT = 8
      const allResults: any[] = []
      for (let i = 0; i < searchPromises.length; i += MAX_CONCURRENT) {
        const batch = searchPromises.slice(i, i + MAX_CONCURRENT)
        const batchResults = await Promise.all(batch)
        allResults.push(...batchResults)
      }

      const matched: any[] = []
      const undocumented: any[] = []
      for (const result of allResults) {
        if (result.topSimilarity >= matchThreshold) {
          matched.push(result)
        } else {
          undocumented.push(result)
        }
      }

      response.json({ candidateCount: candidates.length, matchThreshold, matches: matched, undocumented })
    } catch (e: any) {
      console.error('[discover] Error:', e)
      const { status, body } = err(e)
      response.status(status).json(body)
    }
  }
}
