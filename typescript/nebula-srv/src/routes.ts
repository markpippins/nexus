import { Request, Response, Router } from 'express';
import { Pool } from 'pg';
import * as fs from 'fs';
import * as path from 'path';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { randomUUID } from 'crypto';
import * as bs from './block-segmentation.service';
import * as bsRedis from './services/block-segmentation-redis.service';
import { CrossReferenceType } from './crossref-taxonomy';

const execFileAsync = promisify(execFile);

// ── Color Palette (matches client) ────────────────────────────────
const COLOR_PALETTE = [
  '#EF4444', '#F97316', '#F59E0B', '#10B981', '#06B6D4',
  '#3B82F6', '#6366F1', '#8B5CF6', '#EC4899', '#F43F5E',
  '#84CC16', '#14B8A6',
];

// ── Helpers ───────────────────────────────────────────────────────
function titleCase(s: string): string {
  return s.split(/\s+/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('');
}

function isUuid(v: string) { return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v); }

/**
 * Bitemporal upsert of a harvest_context info tab on a system.
 * Synthesizes the candidate's title, intent_description, and harvest source
 * into a markdown block and writes it to nebula.system_info_tabs_history.
 *
 * Must be called inside an active transaction (uses the provided client).
 */
/** Returns true when planRef is a non-empty string (after trim). */
function hasPlanRef(planRef: any): boolean {
  return planRef !== undefined && planRef !== null && String(planRef).trim() !== '';
}

/**
 * Create a cross-reference from a harvest_candidate to a conduit plan
 * with rel_type='ag:spawns_plan'. Uses WHERE NOT EXISTS for idempotency
 * (cross_references has no unique constraint on the composite key).
 *
 * Must be called inside an active transaction (uses the provided client).
 * Returns the created row, or null if planRef is empty or the link already exists.
 */
async function createSpawnsPlanCrossRef(
  client: import('pg').PoolClient,
  candidateId: string,
  planRef: string | null | undefined,
  extraMetadata?: Record<string, any>,
): Promise<any | null> {
  if (!hasPlanRef(planRef)) return null;
  const planRefStr = String(planRef).trim();
  const { rows: [xref] } = await client.query(
    `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
     SELECT 'harvest_candidate', $1, 'plan', $2, 'ag:spawns_plan', $3
     WHERE NOT EXISTS (
       SELECT 1 FROM nebula.cross_references_history
       WHERE source_type = 'harvest_candidate'
         AND source_id = $1
         AND target_type = 'plan'
         AND target_id = $2
         AND rel_type = 'ag:spawns_plan'
         AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
     )
     ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
       WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
     DO NOTHING
     RETURNING *`,
    [candidateId, planRefStr, JSON.stringify(extraMetadata || {})]
  );
  return xref || null;
}

async function upsertHarvestContextTab(
  client: import('pg').PoolClient,
  systemId: string,
  candidate: { harvest_id: string; title: string; status: string | null; id: string; intent_description: string },
) {
  const { rows: [harvest] } = await client.query(
    'SELECT source_filename FROM nebula.harvests WHERE id = $1',
    [candidate.harvest_id]
  );

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
  ].join('\n');

  await client.query(
    `UPDATE nebula.system_info_tabs_history
     SET recorded_until_dt = NOW()
     WHERE system_id = $1 AND tab_id = 'harvest_context'
       AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
    [systemId]
  );
  await client.query(
    `INSERT INTO nebula.system_info_tabs_history
     (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
     VALUES ($1, 'harvest_context', $2, NOW(), '9999-12-31 23:59:59+00')`,
    [systemId, tabContent]
  );
}

// ── Status Normalization (Plan 0132) ─────────────────────────
// Canonical eight accepted by the requirements table and the nebula-mcp
// zod enum (see typescript/nebula-mcp/src/tools/index.ts).
const STATUS_CANONICAL = new Set([
  'Backlog', 'ToDo', 'InProgress', 'Active',
  'Blocked', 'Done', 'Cancelled', 'Accepted',
]);
// Lookups keyed on trim+lowercased input. Includes the canonical values
// themselves plus common case / separator / phrasing variants. Returns
// null on empty or unrecognized input — callers turn that into a 400.
const STATUS_NORMALIZATION: Record<string, string> = {
  // Canonical (pass-through)
  backlog:   'Backlog',
  todo:      'ToDo',
  inprogress:'InProgress',
  active:    'Active',
  blocked:   'Blocked',
  done:      'Done',
  cancelled: 'Cancelled',
  accepted:  'Accepted',
  // Common variants
  'to-do':       'ToDo',
  'to do':       'ToDo',
  'in progress': 'InProgress',
  'in-progress': 'InProgress',
  in_progress:   'InProgress',
  cancel:        'Cancelled',
  canceled:      'Cancelled',
  accept:        'Accepted',
  complete:      'Done',
  completed:     'Done',
  resolved:      'Done',
  wip:           'InProgress',
  new:           'Backlog',
};
function normalizeStatus(input: string | null | undefined): string | null {
  if (input === undefined || input === null) return null;
  const key = String(input).trim().toLowerCase();
  if (!key) return null;
  return STATUS_NORMALIZATION[key] ?? null;
}

// ── Requirement Type Constants ────────────────────────────────
const REQ_TYPES = ['Epic', 'Story', 'Task', 'Bug'] as const;
type ReqType = typeof REQ_TYPES[number];
function normalizeReqType(input: string | null | undefined): string | null {
  if (input === undefined || input === null) return null;
  const key = String(input).trim();
  if (!key) return null;
  // Case-sensitive match (DB CHECK constraint is case-sensitive)
  return (REQ_TYPES as readonly string[]).includes(key) ? key : null;
}

function toEpochMs(row: any, ...cols: string[]): any {
  const out = { ...row };
  for (const col of cols) {
    if (out[col] && typeof out[col] === 'object' && out[col].getTime) {
      out[col] = out[col].getTime();
    }
  }
  return out;
}

/** Convert snake_case DB row keys to camelCase and Date values to epoch ms */
function camelCaseRow(row: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [key, value] of Object.entries(row)) {
    const camelKey = key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
    if (value instanceof Date) {
      out[camelKey] = value.getTime();
    } else {
      out[camelKey] = value;
    }
  }
  return out;
}

async function getUnusedColor(systemId: string, pool: Pool): Promise<string> {
  const { rows } = await pool.query(
    'SELECT color FROM subsystems WHERE system_id = $1',
    [systemId]
  );
  const used = new Set(rows.map(r => r.color));
  for (const c of COLOR_PALETTE) {
    if (!used.has(c)) return c;

  }
  return COLOR_PALETTE[0]; // all used — pick first
}


// ── Pagination helper — supports both page/pageSize and limit/offset ──
function parsePagination(query: any): { offset: number; limit: number; page: number; pageSize: number } {
  const rawLimit = query.limit !== undefined ? parseInt(String(query.limit), 10) : NaN;
  const rawOffset = query.offset !== undefined ? parseInt(String(query.offset), 10) : NaN;
  if (!isNaN(rawLimit) || !isNaN(rawOffset)) {
    const limit = Math.min(100, Math.max(1, isNaN(rawLimit) ? 100 : rawLimit));
    const offset = Math.max(0, isNaN(rawOffset) ? 0 : rawOffset);
    return { offset, limit, page: Math.floor(offset / limit) + 1, pageSize: limit };
  }
  const page = Math.max(1, parseInt(String(query.page || '1'), 10));
  const pageSize = Math.min(100, Math.max(1, parseInt(String(query.pageSize || '100'), 10)));
  return { offset: (page - 1) * pageSize, limit: pageSize, page, pageSize };
}

// ── Role lifecycle event emitter (D-2026-08-16-009 R3) ─────────────
// Role transitions are written to cascade.events (the LIVE durable bus) so
// every lifecycle change — grant / revoke / expire / capability change — has
// a replayable, deduplicated audit trail. The mutation is the source of
// truth for the transition; the event is the audit trail. Awaited (not
// fire-and-forget) so conformance tests can assert the event exists
// immediately after the API returns, but best-effort: a failed event write
// never fails the caller's response.
const ROLE_LIFECYCLE_EVENT_TYPES = [
  'role.granted',
  'role.revoked',
  'role.expired',
  'capability.changed',
] as const;
type RoleLifecycleEventType = (typeof ROLE_LIFECYCLE_EVENT_TYPES)[number];

async function emitRoleLifecycleEvent(
  pool: Pool,
  eventType: RoleLifecycleEventType,
  source: string,
  aggregateType: string,
  aggregateId: string,
  payload: Record<string, unknown>,
): Promise<void> {
  try {
    await pool.query(
      `INSERT INTO cascade.events
         (event_type, source, event_timestamp, payload, aggregate_type, aggregate_id, actor_type)
       VALUES ($1, $2, NOW(), $3::jsonb, $4, $5, 'system')`,
      [eventType, source, JSON.stringify(payload), aggregateType, aggregateId],
    );
  } catch (err: any) {
    // best-effort — the transition already committed; don't 500 the caller
    console.error(`[role-lifecycle] failed to emit ${eventType}: ${err?.message}`);
  }
}

/**
 * Route tags for role-lease lifecycle agent records (revoked / swept /
 * exhausted). wr-conf / leased-builder conformance suites create synthetic
 * test leases (roles like `wr-conf-002` / `wr_conf_*`, models like `test/...`);
 * their lifecycle records must NOT tag real interactive roles — that floods
 * real-role inboxes (R17 pointer checks) with harness noise (to-do 41505e71).
 * Synthetic leases are routed to a `to:wr-conf-observer` tag instead; the
 * domain tags (`type:*`, `role:*`, `domain:*`) and the record itself (audit
 * trail) are unchanged.
 */
function roleLeaseRecordTags(
  baseTags: string[],
  role: string,
  model: string | null | undefined,
): string[] {
  const synthetic =
    /^wr[_-]conf/i.test(role) ||
    (typeof model === 'string' && model.trim().startsWith('test/'));
  if (!synthetic) return baseTags;
  const REAL_TO =
    /^to:(architect|engineer|engineer-ii|planner|reviewer|analyst|devops|topologist|inspector|critic)$/;
  const domain = baseTags.filter((t) => !REAL_TO.test(t));
  return [...domain, 'to:wr-conf-observer'];
}

export function createRoutes(pool: Pool): Router {
  const router = Router();

  // ════════════════════════════════════════════════════════════════
  //  SYSTEMS
  // ════════════════════════════════════════════════════════════════

  // GET /api/systems — full nested hierarchy with pagination
  router.get('/systems', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [countResult, { rows: systems }] = await Promise.all([
        pool.query('SELECT COUNT(*)::int AS total FROM systems'),
        pool.query('SELECT * FROM systems ORDER BY name LIMIT $1 OFFSET $2', [pageSize, offset]),
      ]);

      const result = [];
      for (const sys of systems) {
        // Folders
        const { rows: folders } = await pool.query(
          'SELECT * FROM system_folders WHERE system_id = $1 ORDER BY name',
          [sys.id]
        );
        // Subsystems → Features
        const { rows: subs } = await pool.query(
          'SELECT * FROM subsystems WHERE system_id = $1 ORDER BY name',
          [sys.id]
        );
        const subsystems = [];
        for (const sub of subs) {
          const { rows: feats } = await pool.query(
            'SELECT * FROM features WHERE subsystem_id = $1 ORDER BY name',
            [sub.id]
          );
          subsystems.push({
            ...camelCaseRow(sub),
            systemId: sub.system_id,
            features: feats.map((f: any) => ({ ...camelCaseRow(f), subsystemId: f.subsystem_id })),
          });
        }
        // Asset relations — system→service edges (V075 migration)
        // Replaces the deprecated system_external_ids junction.
        let assetRelations: any[] = [];
        try {
          const { rows } = await pool.query(
            `SELECT ar.id, ar.relation_type AS "relationType",
                    ar.effective_at AS "effectiveAt",
                    json_build_object(
                      'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                      'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
                    ) AS "relatedAsset"
             FROM semantics.asset_relation ar
             JOIN semantics.canonical_asset ca ON ca.id = ar.to_asset_id AND ca.expired_at IS NULL
             WHERE ar.from_asset_id = $1 AND ar.expired_at IS NULL
             ORDER BY ar.relation_type, ar.effective_at DESC`,
            [sys.asset_id]
          );
          assetRelations = rows;
        } catch {
          // semantics schema may not be accessible in all environments
        }
        result.push({
          ...camelCaseRow(sys),
          folders: folders.map((f: any) => ({ ...f, id: f.id, name: f.name, category: f.category, note: f.note })),
          subsystems,
          externalIds: assetRelations,
        });
      }
      res.json({
        items: result,
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/systems
  router.post('/systems', async (req: Request, res: Response) => {
    try {
      const { name, description = '', readme = null, architecture = null, path = null } = req.body;
      if (!name) return res.status(400).json({ error: 'name is required' });
      const { rows: [sys] } = await pool.query(
        'INSERT INTO systems (name, description, readme, architecture, path) VALUES ($1, $2, $3, $4, $5) RETURNING *',
        [name, description, readme, architecture, path]
      );
      res.status(201).json({ ...toEpochMs(sys, 'created_at'), folders: [], subsystems: [] });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/systems/:id — name, description, readme, architecture
  router.patch('/systems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { name, description, readme, architecture, path } = req.body;
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (name !== undefined) { sets.push(`name = $${i++}`); vals.push(name); }
      if (description !== undefined) { sets.push(`description = $${i++}`); vals.push(description); }
      if (readme !== undefined) { sets.push(`readme = $${i++}`); vals.push(readme); }
      if (architecture !== undefined) { sets.push(`architecture = $${i++}`); vals.push(architecture); }
      if (path !== undefined) { sets.push(`path = $${i++}`); vals.push(path); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [sys] } = await pool.query(
        `UPDATE systems SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!sys) return res.status(404).json({ error: 'System not found' });
      res.json({ ...toEpochMs(sys, 'created_at'), name: sys.name, description: sys.description, readme: sys.readme, architecture: sys.architecture, path: sys.path });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/systems/:id — cascade deletes subsystems, features, folders, requirements
  router.delete('/systems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      // Also delete associated work_sessions (parentId matches)
      await pool.query('DELETE FROM work_sessions WHERE parent_id = $1', [id]);
      const { rowCount } = await pool.query('DELETE FROM systems WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'System not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id — single system with full nested hierarchy
  router.get('/systems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [sys] } = await pool.query('SELECT * FROM systems WHERE id = $1', [id]);
      if (!sys) return res.status(404).json({ error: 'System not found' });

      // Folders
      const { rows: folders } = await pool.query(
        'SELECT * FROM system_folders WHERE system_id = $1 ORDER BY name',
        [sys.id]
      );
      // Subsystems → Features
      const { rows: subs } = await pool.query(
        'SELECT * FROM subsystems WHERE system_id = $1 ORDER BY name',
        [sys.id]
      );
      const subsystems = [];
      for (const sub of subs) {
        const { rows: feats } = await pool.query(
          'SELECT * FROM features WHERE subsystem_id = $1 ORDER BY name',
          [sub.id]
        );
        subsystems.push({
          ...toEpochMs(sub, 'created_at'),
          systemId: sub.system_id,
          features: feats.map((f: any) => ({ ...toEpochMs(f, 'created_at'), subsystemId: f.subsystem_id })),
        });
      }
      // Asset relations — system→service edges (V075 migration)
      let assetRelations: any[] = [];
      try {
        const { rows } = await pool.query(
          `SELECT ar.id, ar.relation_type AS "relationType",
                  ar.effective_at AS "effectiveAt",
                  json_build_object(
                    'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                    'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
                  ) AS "relatedAsset"
           FROM semantics.asset_relation ar
           JOIN semantics.canonical_asset ca ON ca.id = ar.to_asset_id AND ca.expired_at IS NULL
           WHERE ar.from_asset_id = $1 AND ar.expired_at IS NULL
           ORDER BY ar.relation_type, ar.effective_at DESC`,
          [sys.asset_id]
        );
        assetRelations = rows;
      } catch {
        // semantics schema may not be accessible in all environments
      }
      res.json({
        ...toEpochMs(sys, 'created_at'),
        folders: folders.map((f: any) => ({ ...f, id: f.id, name: f.name, category: f.category, note: f.note })),
        subsystems,
        externalIds: assetRelations,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SUBSYSTEMS
  // ════════════════════════════════════════════════════════════════

  // POST /api/subsystems
  router.post('/subsystems', async (req: Request, res: Response) => {
    try {
      const { systemId, name, description = '', readme = null, path = null } = req.body;
      if (!systemId || !name) return res.status(400).json({ error: 'systemId and name are required' });
      // Server-side color deduplication (Plan 0093)
      const color = await getUnusedColor(systemId, pool);
      const { rows: [sub] } = await pool.query(
        'INSERT INTO subsystems (system_id, name, description, readme, color, path) VALUES ($1, $2, $3, $4, $5, $6) RETURNING *',
        [systemId, name, description, readme, color, path]
      );
      res.status(201).json({
        ...toEpochMs(sub, 'created_at'),
        systemId: sub.system_id,
        features: [],
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/subsystems/:id
  router.patch('/subsystems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { name, description, readme, color, path } = req.body;
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (name !== undefined) { sets.push(`name = $${i++}`); vals.push(name); }
      if (description !== undefined) { sets.push(`description = $${i++}`); vals.push(description); }
      if (readme !== undefined) { sets.push(`readme = $${i++}`); vals.push(readme); }
      if (color !== undefined) { sets.push(`color = $${i++}`); vals.push(color); }
      if (path !== undefined) { sets.push(`path = $${i++}`); vals.push(path); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [sub] } = await pool.query(
        `UPDATE subsystems SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!sub) return res.status(404).json({ error: 'Subsystem not found' });
      res.json({ ...toEpochMs(sub, 'created_at'), systemId: sub.system_id, name: sub.name, description: sub.description, readme: sub.readme, color: sub.color, path: sub.path });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/subsystems/:id — cascade deletes features and requirements
  router.delete('/subsystems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      await pool.query('DELETE FROM requirements WHERE subsystem_id = $1', [id]);
      const { rowCount } = await pool.query('DELETE FROM subsystems WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Subsystem not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id — single subsystem with features
  router.get('/subsystems/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [sub] } = await pool.query(
        'SELECT * FROM subsystems WHERE id = $1',
        [id]
      );
      if (!sub) return res.status(404).json({ error: 'Subsystem not found' });
      const { rows: feats } = await pool.query(
        'SELECT * FROM features WHERE subsystem_id = $1 ORDER BY name',
        [sub.id]
      );
      res.json({
        ...toEpochMs(sub, 'created_at'),
        systemId: sub.system_id,
        features: feats.map((f: any) => ({ ...toEpochMs(f, 'created_at'), subsystemId: f.subsystem_id })),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  FEATURES
  // ════════════════════════════════════════════════════════════════

  // POST /api/features
  router.post('/features', async (req: Request, res: Response) => {
    try {
      const { subsystemId, name, description = '', readme = null, path = null } = req.body;
      if (!subsystemId || !name) return res.status(400).json({ error: 'subsystemId and name are required' });
      const { rows: [feat] } = await pool.query(
        'INSERT INTO features (subsystem_id, name, description, readme, path) VALUES ($1, $2, $3, $4, $5) RETURNING *',
        [subsystemId, name, description, readme, path]
      );
      res.status(201).json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/features/:id
  router.patch('/features/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { name, description, readme, path } = req.body;
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (name !== undefined) { sets.push(`name = $${i++}`); vals.push(name); }
      if (description !== undefined) { sets.push(`description = $${i++}`); vals.push(description); }
      if (readme !== undefined) { sets.push(`readme = $${i++}`); vals.push(readme); }
      if (path !== undefined) { sets.push(`path = $${i++}`); vals.push(path); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [feat] } = await pool.query(
        `UPDATE features SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!feat) return res.status(404).json({ error: 'Feature not found' });
      res.json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id, name: feat.name, description: feat.description, readme: feat.readme, path: feat.path });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/features/:id — cascade deletes requirements with feature_id
  router.delete('/features/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      await pool.query('DELETE FROM requirements WHERE feature_id = $1', [id]);
      const { rowCount } = await pool.query('DELETE FROM features WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Feature not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id — single feature
  router.get('/features/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [feat] } = await pool.query(
        'SELECT * FROM features WHERE id = $1',
        [id]
      );
      if (!feat) return res.status(404).json({ error: 'Feature not found' });
      res.json({ ...toEpochMs(feat, 'created_at'), subsystemId: feat.subsystem_id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  REQUIREMENTS
  // ════════════════════════════════════════════════════════════════

  // GET /api/requirements — filterable with pagination
  router.get('/requirements', async (req: Request, res: Response) => {
    try {
      const { systemId, subsystemId, featureId } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (systemId) { clauses.push(`system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId) { clauses.push(`subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId) { clauses.push(`feature_id = $${i++}`); vals.push(featureId); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM requirements ${where} ORDER BY created_at DESC LIMIT $${i} OFFSET $${i+1}`,
          [...vals, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM requirements ${where}`,
          vals
        ),
      ]);

      const items = dataResult.rows.map((r: any) => ({
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
      }));

      // Fetch question counts for all returned requirements
      if (items.length > 0) {
        const ids = items.map((it: any) => it.id);
        const { rows: qcRows } = await pool.query(
          `SELECT requirement_id,
                  COUNT(*)::int AS total,
                  COUNT(*) FILTER (WHERE status = 'OPEN')::int AS open_count,
                  COUNT(*) FILTER (WHERE status = 'OPEN' AND blocking = true)::int AS blocking_count
           FROM nebula.open_questions
           WHERE requirement_id = ANY($1::uuid[])
           GROUP BY requirement_id`,
          [ids]
        );
        const qcMap = new Map(qcRows.map((r: any) => [r.requirement_id, r]));
        for (const item of items) {
          const qc = qcMap.get(item.id);
          item.questionCounts = qc
            ? { total: qc.total, openCount: qc.open_count, blockingCount: qc.blocking_count }
            : { total: 0, openCount: 0, blockingCount: 0 };
        }
      }

      res.json({
        items,
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/requirements/:id — single requirement by ID
  router.get('/requirements/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [reqt] } = await pool.query(
        'SELECT * FROM requirements WHERE id = $1',
        [id]
      );
      if (!reqt) return res.status(404).json({ error: 'Requirement not found' });

      // Fetch question counts
      const { rows: qcRows } = await pool.query(
        `SELECT COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'OPEN')::int AS open_count,
                COUNT(*) FILTER (WHERE status = 'OPEN' AND blocking = true)::int AS blocking_count
         FROM nebula.open_questions
         WHERE requirement_id = $1`,
        [id]
      );

      res.json({
        ...toEpochMs(reqt, 'created_at'),
        systemId: reqt.system_id,
        subsystemId: reqt.subsystem_id,
        featureId: reqt.feature_id,
        startDate: reqt.start_date,
        completionDate: reqt.completion_date,
        parentId: reqt.parent_id,
        reqType: reqt.req_type,
        acceptanceCriteria: reqt.acceptance_criteria,
        candidateId: reqt.candidate_id,
        conduitPlanId: reqt.conduit_plan_id,
        questionCounts: qcRows.length > 0
          ? { total: qcRows[0].total, openCount: qcRows[0].open_count, blockingCount: qcRows[0].blocking_count }
          : { total: 0, openCount: 0, blockingCount: 0 },
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/requirements/:id/children — fetch direct child requirements with pagination
  router.get('/requirements/:id/children', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM requirements WHERE parent_id = $1 ORDER BY created_at ASC LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          'SELECT COUNT(*)::int AS total FROM requirements WHERE parent_id = $1',
          [id]
        ),
      ]);

      res.json({
        items: dataResult.rows.map((r: any) => ({
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
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/requirements/:id/dependencies — list blockers and blocked-by with pagination
  router.get('/requirements/:id/dependencies', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT cr.id, cr.source_type, cr.source_id, cr.target_type, cr.target_id,
                  cr.rel_type, cr.metadata, cr.created_at,
                  CASE WHEN cr.source_id = $1 THEN cr.target_id ELSE cr.source_id END AS other_id,
                  CASE WHEN cr.source_id = $1 THEN 'outgoing' ELSE 'incoming' END AS direction
           FROM nebula.cross_references cr
           WHERE ((cr.source_type = 'requirement' AND cr.source_id = $1)
              OR (cr.target_type = 'requirement' AND cr.target_id = $1))
             AND cr.rel_type IN ('req:blocks', 'req:depends_on')
           ORDER BY cr.created_at ASC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.cross_references cr
           WHERE ((cr.source_type = 'requirement' AND cr.source_id = $1)
              OR (cr.target_type = 'requirement' AND cr.target_id = $1))
             AND cr.rel_type IN ('req:blocks', 'req:depends_on')`,
          [id]
        ),
      ]);

      res.json({
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
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/requirements/:id/dependencies — create a dependency link
  router.post('/requirements/:id/dependencies', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { targetId, relType = 'req:blocks' } = req.body;
      if (!targetId) return res.status(400).json({ error: 'targetId is required' });
      if (id === targetId) return res.status(400).json({ error: 'A requirement cannot depend on itself' });

      const validRelTypes = [CrossReferenceType.REQ_BLOCKS, CrossReferenceType.REQ_DEPENDS_ON];
      if (!validRelTypes.includes(relType)) {
        return res.status(400).json({ error: `relType must be one of: ${validRelTypes.join(', ')}` });
      }

      // Verify both requirements exist
      const { rows } = await pool.query(
        'SELECT id FROM requirements WHERE id = ANY($1::uuid[])',
        [[id, targetId]]
      );
      if (rows.length !== 2) {
        return res.status(404).json({ error: 'One or both requirements not found' });
      }

      // Idempotent insert (WHERE NOT EXISTS)
      const { rows: [xref] } = await pool.query(
        `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
         SELECT 'requirement', $1, 'requirement', $2, $3, '{}'
         WHERE NOT EXISTS (
           SELECT 1 FROM nebula.cross_references_history
           WHERE source_type = 'requirement'
             AND source_id = $1
             AND target_type = 'requirement'
             AND target_id = $2
             AND rel_type = $3
             AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
         )
         ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
           WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
         DO NOTHING
         RETURNING *`,
        [id, targetId, relType]
      );

      res.status(201).json(xref || { ok: true, message: 'Dependency already exists' });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/requirements/:id/dependencies/:depId — remove a dependency link
  router.delete('/requirements/:id/dependencies/:depId', async (req: Request, res: Response) => {
    try {
      const { id, depId } = req.params;
      const { rowCount } = await pool.query(
        `UPDATE nebula.cross_references
         SET valid_until = now()
         WHERE id = $1
           AND source_type = 'requirement'
           AND target_type = 'requirement'
           AND rel_type IN ('req:blocks', 'req:depends_on')
           AND (source_id = $2 OR target_id = $2)
           AND valid_until > now()`,
        [depId, id]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Dependency not found' });
      res.json({ expired: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/requirements
  router.post('/requirements', async (req: Request, res: Response) => {
    try {
      const { systemId, subsystemId = null, featureId = null, title, description = '', status = 'Backlog', priority = 'Medium', startDate = null, completionDate = null, parentId = null, reqType = null, acceptanceCriteria = null, candidateId = null } = req.body;
      if (!systemId || !title) return res.status(400).json({ error: 'systemId and title are required' });
      const normalizedStatus = normalizeStatus(status);
      if (!normalizedStatus) return res.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` });
      // Validate reqType if provided
      if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) {
        return res.status(400).json({ error: `reqType, if provided, must be one of: ${REQ_TYPES.join(', ')}` });
      }
      const { rows: [reqt] } = await pool.query(
        `INSERT INTO requirements (system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date, parent_id, req_type, acceptance_criteria, candidate_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) RETURNING *`,
        [systemId, subsystemId, featureId, title, description, normalizedStatus, priority, startDate, completionDate, parentId, reqType, acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null, candidateId]
      );
      res.status(201).json({
        ...toEpochMs(reqt, 'created_at'),
        systemId: reqt.system_id,
        subsystemId: reqt.subsystem_id,
        featureId: reqt.feature_id,
        startDate: reqt.start_date,
        completionDate: reqt.completion_date,
        parentId: reqt.parent_id,
        reqType: reqt.req_type,
        acceptanceCriteria: reqt.acceptance_criteria,
        candidateId: reqt.candidate_id,
        conduitPlanId: reqt.conduit_plan_id,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/requirements/batch — batch status update (BEFORE /:id!)
  router.patch('/requirements/batch', async (req: Request, res: Response) => {
    try {
      const { ids, status } = req.body;
      if (!ids || !Array.isArray(ids) || !status) return res.status(400).json({ error: 'ids (array) and status are required' });
      const normalizedStatus = normalizeStatus(status);
      if (!normalizedStatus) return res.status(400).json({ error: `status must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` });
      const { rowCount } = await pool.query(
        'UPDATE requirements SET status = $1 WHERE id = ANY($2::uuid[])',
        [normalizedStatus, ids]
      );
      res.json({ ok: true, updated: rowCount });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/requirements/:id
  router.patch('/requirements/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { title, description, status, priority, startDate, completionDate, systemId, subsystemId, featureId, parentId, reqType, acceptanceCriteria, candidateId, conduitPlanId } = req.body;
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (title !== undefined) { sets.push(`title = $${i++}`); vals.push(title); }
      if (description !== undefined) { sets.push(`description = $${i++}`); vals.push(description); }
      if (status !== undefined) {
      const normalizedStatus = normalizeStatus(status);
      if (!normalizedStatus) return res.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` });
      sets.push(`status = $${i++}`); vals.push(normalizedStatus);
    }
      if (priority !== undefined) { sets.push(`priority = $${i++}`); vals.push(priority); }
      if (startDate !== undefined) { sets.push(`start_date = $${i++}`); vals.push(startDate); }
      if (completionDate !== undefined) { sets.push(`completion_date = $${i++}`); vals.push(completionDate); }
      if (systemId !== undefined) { sets.push(`system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId !== undefined) { sets.push(`subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId !== undefined) { sets.push(`feature_id = $${i++}`); vals.push(featureId); }
      if (parentId !== undefined) { sets.push(`parent_id = $${i++}`); vals.push(parentId); }
      if (reqType !== undefined) {
        if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) return res.status(400).json({ error: `reqType must be one of: ${REQ_TYPES.join(', ')}` });
        sets.push(`req_type = $${i++}`); vals.push(reqType);
      }
      if (acceptanceCriteria !== undefined) { sets.push(`acceptance_criteria = $${i++}`); vals.push(acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null); }
      if (candidateId !== undefined) { sets.push(`candidate_id = $${i++}`); vals.push(candidateId); }
      if (conduitPlanId !== undefined) { sets.push(`conduit_plan_id = $${i++}`); vals.push(conduitPlanId); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [reqt] } = await pool.query(
        `UPDATE requirements SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!reqt) return res.status(404).json({ error: 'Requirement not found' });
      // ── Backlog→ToDo auto-compile trigger (Plan 1062) ────────────
      // When a requirement transitions to ToDo, fire-and-forget the
      // two-stage compiler to generate WorkRequest IR. D2 (CP-2): compile
      // is now pre-row — it no longer implies a conduit plan row. Plan
      // creation is a separate release-time step (CP-9 release gate).
      if (status !== undefined && reqt.status === 'ToDo') {
        fetch(`http://localhost:3101/api/requirements/${id}/compile`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ createPlan: false }),
        }).catch(() => { /* compilation is best-effort */ });
      }
      res.json({
        ...toEpochMs(reqt, 'created_at'),
        systemId: reqt.system_id, subsystemId: reqt.subsystem_id, featureId: reqt.feature_id,
        startDate: reqt.start_date, completionDate: reqt.completion_date,
        parentId: reqt.parent_id, reqType: reqt.req_type,
        acceptanceCriteria: reqt.acceptance_criteria, candidateId: reqt.candidate_id,
        conduitPlanId: reqt.conduit_plan_id,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/requirements/:id
  router.delete('/requirements/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query('DELETE FROM requirements WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Requirement not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM FOLDERS
  // ════════════════════════════════════════════════════════════════

  // POST /api/requirements/:id/move — kanban-friendly single-id status move (Plan 0131)
  router.post('/requirements/:id/move', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id } = req.params;
      const { targetStatus, expectedCurrentStatus } = req.body;
      const allowedList = Array.from(STATUS_CANONICAL).join(', ');
      const expectedSupplied = expectedCurrentStatus !== undefined && expectedCurrentStatus !== null;
      const normalizedTarget = normalizeStatus(targetStatus);
      const normalizedExpected = expectedSupplied ? normalizeStatus(expectedCurrentStatus) : undefined;
      if (!normalizedTarget) {
        return res.status(400).json({ error: `targetStatus is required and must be one of: ${allowedList}` });
      }
      if (expectedSupplied && normalizedExpected === null) {
        return res.status(400).json({ error: `expectedCurrentStatus, if provided, must be one of: ${allowedList}` });
      }
      await client.query('BEGIN');
      // Lock the row to detect concurrent moves
      const { rows: [currentRow] } = await client.query(
        'SELECT id, status FROM nebula.requirements_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\' FOR UPDATE',
        [id]
      );
      if (!currentRow) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'Requirement not found' });
      }
      if (normalizedExpected !== undefined && currentRow.status !== normalizedExpected) {
        await client.query('ROLLBACK');
        return res.status(409).json({
          error: 'Current status does not match expectedCurrentStatus',
          currentStatus: currentRow.status,
          expectedCurrentStatus: normalizedExpected,
        });
      }
      const { rows: [reqt] } = await client.query(
        'UPDATE requirements SET status = $1 WHERE id = $2 RETURNING *',
        [normalizedTarget, id]
      );
      await client.query('COMMIT');
      res.json({
        ...toEpochMs(reqt, 'created_at'),
        systemId: reqt.system_id, subsystemId: reqt.subsystem_id, featureId: reqt.feature_id,
        startDate: reqt.start_date, completionDate: reqt.completion_date,
        parentId: reqt.parent_id, reqType: reqt.req_type,
        acceptanceCriteria: reqt.acceptance_criteria, candidateId: reqt.candidate_id,
        conduitPlanId: reqt.conduit_plan_id,
      });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  REQUIREMENT → WORKREQUEST COMPILATION (Plan 1062)
  // ════════════════════════════════════════════════════════════════

  // POST /api/requirements/:id/compile — compile a requirement into a WorkRequest IR
  // Runs the two-stage compiler (Stage 1 normalization + Stage 2 op_registry compilation).
  // Optionally creates a conduit plan if createPlan=true.
  router.post('/requirements/:id/compile', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { stage1Only = false, createPlan = false, dryRun = false, deliverable } = req.body;

      // Fetch requirement with hierarchy context
      const { rows: [reqt] } = await pool.query(
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
         WHERE req.id = $1`,
        [id]
      );
      if (!reqt) return res.status(404).json({ error: 'Requirement not found' });

      // ── Stage 1: Semantic Normalization ──────────────────────────
      const hierarchyContext = {
        system: { id: reqt.system_id, name: reqt.system_name, description: reqt.system_description },
        subsystem: { id: reqt.subsystem_id, name: reqt.subsystem_name, description: reqt.subsystem_description },
        feature: { id: reqt.feature_id, name: reqt.feature_name, description: reqt.feature_description },
      };

      // Normalize acceptance criteria
      let normalizedCriteria: string[] = [];
      const rawAC = reqt.acceptance_criteria;
      if (rawAC) {
        const parsed = typeof rawAC === 'string' ? JSON.parse(rawAC) : rawAC;
        if (Array.isArray(parsed)) {
          normalizedCriteria = parsed.map((item: any) =>
            typeof item === 'string' ? item.trim() :
            (item?.condition || item?.title || item?.criterion || '').trim()
          ).filter(Boolean);
        } else if (typeof parsed === 'object' && parsed?.condition) {
          normalizedCriteria = [parsed.condition];
        }
      }

      // Resolve cross-references
      const { rows: crossRefs } = await pool.query(
        `SELECT cr.rel_type, cr.target_type, cr.target_id,
                CASE WHEN cr.target_type = 'requirement' THEN
                  (SELECT title FROM nebula.requirements WHERE id = cr.target_id::uuid)
                ELSE cr.target_id::text END AS target_label
         FROM nebula.cross_references cr
         WHERE cr.source_type = 'requirement' AND cr.source_id = $1
         ORDER BY cr.created_at`,
        [id]
      );

      // Synthesize intent summary
      const intentParts = [reqt.title];
      if (reqt.description) intentParts.push(reqt.description);
      if (reqt.subsystem_name) intentParts.push(`Subsystem: ${reqt.subsystem_name}`);
      if (reqt.feature_name) intentParts.push(`Feature: ${reqt.feature_name}`);
      const intentSummary = intentParts.filter(Boolean).join(' — ');

      const stage1 = {
        requirement_id: id,
        title: reqt.title,
        hierarchy_context: hierarchyContext,
        normalized_criteria: normalizedCriteria,
        cross_references: crossRefs,
        intent_summary: intentSummary,
      };

      if (stage1Only) {
        return res.json({ ok: true, stage: 1, result: stage1 });
      }

      // ── Stage 2: Engineering Compilation ─────────────────────────
      // Match against op_registry
      const { rows: registry } = await pool.query(
        `SELECT id, intent_id, version, label, match_patterns, opcode_template,
                required_params, idempotency_key
         FROM nebula.op_registry
         WHERE status = 'active' AND deleted_at IS NULL`
      );

      let matchedEntry: any = null;
      let bestScore = 0;
      const intentText = `${reqt.title} ${intentSummary}`.toLowerCase();
      for (const entry of registry) {
        const patterns = entry.match_patterns || [];
        for (const pattern of patterns) {
          try {
            const match = new RegExp(pattern, 'i').exec(intentText);
            if (match && match[0].length > bestScore) {
              bestScore = match[0].length;
              matchedEntry = entry;
            }
          } catch { /* skip invalid regex */ }
        }
      }

      // Generate opcode sequence
      let opSequence: any[] = [];
      if (matchedEntry?.opcode_template) {
        const template = typeof matchedEntry.opcode_template === 'string'
          ? JSON.parse(matchedEntry.opcode_template) : matchedEntry.opcode_template;
        if (Array.isArray(template)) {
          opSequence = template.map((step: any, i: number) => ({
            step: i + 1,
            op: step.op || 'WRITE_FILE',
            target: step.target || '',
            args: step.params || {},
            idempotency_key: `${matchedEntry.idempotency_key || ''}-${id.slice(0, 8)}`,
          }));
        }
      }
      if (opSequence.length === 0) {
        // Default: generate from acceptance criteria
        const reqShort = id.slice(0, 8);
        normalizedCriteria.slice(0, 5).forEach((criterion: string, i: number) => {
          opSequence.push({
            step: i + 1, op: 'WRITE_SOURCE_FILE',
            target: `src/${reqShort}/step_${i+1}`,
            args: { content_template: 'acceptance-criterion', criterion },
            idempotency_key: `req-${reqShort}-step-${i+1}`,
          });
        });
        opSequence.push({
          step: opSequence.length + 1, op: 'VALIDATE_SYNTAX',
          target: `src/${reqShort}/`, args: { language: 'auto' },
          idempotency_key: `req-${reqShort}-validate`,
        });
      }

      // Resolve files affected
      const filesAffected: string[] = [];
      const fileSet = new Set<string>();
      for (const step of opSequence) {
        if (step.target && !step.target.startsWith('spec/') && !step.target.startsWith('files/')) {
          let t = step.target;
          if (!t.match(/\.(py|ts|js|go|java|sql|md)$/)) t = t.replace(/\/$/, '') + '/__init__.py';
          fileSet.add(t);
        }
      }
      if (reqt.system_name) {
        let base = reqt.system_name.toLowerCase().replace(/\s/g, '-');
        if (reqt.subsystem_name) base += '/' + reqt.subsystem_name.toLowerCase().replace(/\s/g, '-');
        fileSet.add(`${base}/__init__.py`);
      }
      filesAffected.push(...Array.from(fileSet).sort());

      // Resolve dependencies from cross-refs
      const dependencies = crossRefs
        .filter((r: any) => r.rel_type === 'req:depends_on' || r.rel_type === 'req:blocks')
        .map((r: any) => r.target_label)
        .filter(Boolean);

      const idempotencyKey = matchedEntry?.idempotency_key || `req-${id.slice(0, 8)}`;
      const acceptanceForPlan = normalizedCriteria.slice(0, 5).length > 0
        ? normalizedCriteria.slice(0, 5)
        : [`Implement: ${reqt.title}`];

      const stage2 = {
        requirement_id: id,
        intent_id: matchedEntry?.intent_id || `REQ-${id.slice(0, 8)}`,
        registry_version: matchedEntry?.version || 'default',
        op_sequence: opSequence,
        files_affected: filesAffected,
        // D1: first-class deliverable for read-only/recon nodes — NOT folded
        // into files_affected (the mutation surface).
        deliverable: typeof deliverable === 'string' && deliverable.trim() ? deliverable : null,
        dependencies,
        acceptance_criteria: acceptanceForPlan,
        idempotency_key: idempotencyKey,
        matched_op_registry_id: matchedEntry?.id || null,
      };

      if (dryRun) {
        return res.json({ ok: true, stage: 2, stage1, stage2, dryRun: true });
      }

      // ── Audit: journal entry via agent_records ───────────────────
      const journalId = randomUUID();
      const now = new Date().toISOString();
      const journalContent = JSON.stringify({
        requirement_id: id,
        stage1: { normalized_criteria_count: normalizedCriteria.length, cross_references_count: crossRefs.length },
        stage2: { matched: !!matchedEntry, op_count: opSequence.length, files_count: filesAffected.length, idempotency_key: idempotencyKey },
      });
      try {
        await pool.query(
          `INSERT INTO nebula.agent_records (id, record_type, role, title, content, tags, created_at, updated_at)
           VALUES ($1::uuid, 'engineering_log', 'architect', $2, $3, $4, $5, $5)`,
          [journalId, `Requirement Compilation: ${reqt.title.slice(0, 80)}`, journalContent,
           JSON.stringify(['req-compilation', `requirement:${id.slice(0, 8)}`, 'audit']), now]
        );
      } catch (journalErr) {
        console.warn('[compile] Journal entry write failed:', journalErr);
      }

      // ── Optional: create conduit plan ────────────────────────────
      let planNumber: string | null = null;
      if (createPlan) {
        const project = (reqt.system_name || 'nexus').toLowerCase().replace(/\s/g, '-');
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
              deliverable: typeof deliverable === 'string' && deliverable.trim() ? deliverable : undefined,
            }),
          });
          const planResult = await planResponse.json() as any;
          if (planResult.created && planResult.planNumber) {
            planNumber = planResult.planNumber;
            // Requirement → plan linkage is column-based (requirements.conduit_plan_id)
            // per T22 Step 5.4 ruling — no parallel compiles_to edge.
            await pool.query(
              `UPDATE requirements SET conduit_plan_id = $2 WHERE id = $1`,
              [id, planNumber]
            );
          }
        } catch (planErr) {
          console.warn('[compile] Conduit plan creation failed:', planErr);
        }
      }

      res.json({
        ok: true,
        stage: 2,
        stage1,
        stage2,
        journal_entry_id: journalId,
        plan_number: planNumber,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/systems/:id/folders
  router.post('/systems/:id/folders', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { name, category, note = '' } = req.body;
      if (!name || !category) return res.status(400).json({ error: 'name and category are required' });
      const { rows: [folder] } = await pool.query(
        'INSERT INTO system_folders (system_id, name, category, note) VALUES ($1, $2, $3, $4) RETURNING *',
        [id, name, category, note]
      );
      res.status(201).json({ id: folder.id, name: folder.name, category: folder.category, note: folder.note });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/systems/:systemId/folders/:folderId
  router.delete('/systems/:systemId/folders/:folderId', async (req: Request, res: Response) => {
    try {
      const { systemId, folderId } = req.params;
      const { rowCount } = await pool.query(
        'DELETE FROM system_folders WHERE id = $1 AND system_id = $2',
        [folderId, systemId]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Folder not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  WORK SESSIONS
  // ════════════════════════════════════════════════════════════════

  // GET /api/sessions — list with pagination
  router.get('/sessions', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          'SELECT * FROM work_sessions ORDER BY created_at DESC LIMIT $1 OFFSET $2',
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM work_sessions'),
      ]);

      const items = dataResult.rows.map((r: any) => ({
        ...toEpochMs(r, 'created_at'),
        parentId: r.parent_id,
        parentType: r.parent_type,
        parentName: r.parent_name,
      }));

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/sessions
  router.post('/sessions', async (req: Request, res: Response) => {
    try {
      const { parentId, parentType, parentName = '', context = '', platform = '', model = '', outcome = null, status = 'Pending' } = req.body;
      if (!parentId || !parentType) return res.status(400).json({ error: 'parentId and parentType are required' });
      // Normalize parent_type to lowercase to match DB CHECK constraint
      const normalizedParentType = parentType.toLowerCase();
      const { rows: [sess] } = await pool.query(
        `INSERT INTO work_sessions (parent_id, parent_type, parent_name, context, platform, model, outcome, status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *`,
        [parentId, normalizedParentType, parentName, context, platform, model, outcome, status]
      );
      res.status(201).json({
        ...toEpochMs(sess, 'created_at'),
        parentId: sess.parent_id, parentType: sess.parent_type, parentName: sess.parent_name,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/sessions/:id
  router.patch('/sessions/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { outcome, status } = req.body;
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (outcome !== undefined) { sets.push(`outcome = $${i++}`); vals.push(outcome); }
      if (status !== undefined) { sets.push(`status = $${i++}`); vals.push(status); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [sess] } = await pool.query(
        `UPDATE work_sessions SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!sess) return res.status(404).json({ error: 'Session not found' });
      res.json({
        ...toEpochMs(sess, 'created_at'),
        parentId: sess.parent_id, parentType: sess.parent_type, parentName: sess.parent_name,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  COMPLEX OPERATIONS (transactional)
  // ════════════════════════════════════════════════════════════════

  // POST /api/features/move — re-parent a feature to a different subsystem
  router.post('/features/move', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { featureId, targetSystemId, targetSubsystemId } = req.body;
      if (!featureId || !targetSystemId || !targetSubsystemId) return res.status(400).json({ error: 'featureId, targetSystemId, and targetSubsystemId are required' });
      await client.query('BEGIN');
      // Update the feature's subsystem_id
      const { rows: [feat] } = await client.query(
        'UPDATE features SET subsystem_id = $1 WHERE id = $2 RETURNING *',
        [targetSubsystemId, featureId]
      );
      if (!feat) { await client.query('ROLLBACK'); return res.status(404).json({ error: 'Feature not found' }); }
      // Update all requirements that reference this feature
      await client.query(
        'UPDATE requirements SET system_id = $1, subsystem_id = $2 WHERE feature_id = $3',
        [targetSystemId, targetSubsystemId, featureId]
      );
      await client.query('COMMIT');
      res.json({ ok: true });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/subsystems/move — re-parent a subsystem to a different system
  router.post('/subsystems/move', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { subsystemId, targetSystemId } = req.body;
      if (!subsystemId || !targetSystemId) return res.status(400).json({ error: 'subsystemId and targetSystemId are required' });
      await client.query('BEGIN');
      const { rows: [sub] } = await client.query(
        'UPDATE subsystems SET system_id = $1 WHERE id = $2 RETURNING *',
        [targetSystemId, subsystemId]
      );
      if (!sub) { await client.query('ROLLBACK'); return res.status(404).json({ error: 'Subsystem not found' }); }
      // Update requirements
      await client.query(
        'UPDATE requirements SET system_id = $1 WHERE subsystem_id = $2',
        [targetSystemId, subsystemId]
      );
      await client.query('COMMIT');
      res.json({ ok: true });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/systems/demote/:id — demote a system into a subsystem of another system
  router.post('/systems/demote/:id', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id: sourceId } = req.params;
      const { targetSystemId } = req.body;
      if (!targetSystemId) return res.status(400).json({ error: 'targetSystemId is required' });
      await client.query('BEGIN');
      // Get source system
      const { rows: [source] } = await client.query('SELECT * FROM systems WHERE id = $1', [sourceId]);
      if (!source) { await client.query('ROLLBACK'); return res.status(404).json({ error: 'Source system not found' }); }
      // Create new subsystem from the system
      const color = await getUnusedColor(targetSystemId, pool);
      const { rows: [newSub] } = await client.query(
        'INSERT INTO subsystems (system_id, name, description, readme, color) VALUES ($1, $2, $3, $4, $5) RETURNING *',
        [targetSystemId, source.name, source.description, source.readme, color]
      );
      // Move source's subsystems as features of the new subsystem
      const { rows: oldSubs } = await client.query('SELECT * FROM subsystems WHERE system_id = $1', [sourceId]);
      for (const os of oldSubs) {
        const { rows: [newFeat] } = await client.query(
          'INSERT INTO features (subsystem_id, name, description, readme) VALUES ($1, $2, $3, $4) RETURNING *',
          [newSub.id, os.name, os.description, os.readme]
        );
        // Move requirements from old subsystem to new feature
        await client.query(
          'UPDATE requirements SET system_id = $1, subsystem_id = $2, feature_id = $3 WHERE subsystem_id = $4',
          [targetSystemId, newSub.id, newFeat.id, os.id]
        );
        // Move feature-level requirements from old subsystem's features
        const { rows: oldFeats } = await client.query('SELECT * FROM features WHERE subsystem_id = $1', [os.id]);
        for (const of of oldFeats) {
          await client.query(
            'UPDATE requirements SET system_id = $1, subsystem_id = $2, feature_id = $3 WHERE feature_id = $4',
            [targetSystemId, newSub.id, newFeat.id, of.id]
          );
        }
      }
      // Move system-level requirements to new subsystem
      await client.query(
        'UPDATE requirements SET system_id = $1, subsystem_id = $2, feature_id = NULL WHERE system_id = $3 AND subsystem_id IS NULL',
        [targetSystemId, newSub.id, sourceId]
      );
      // Delete source system (cascade deletes its subsystems/features/folders)
      await client.query('DELETE FROM systems WHERE id = $1', [sourceId]);
      await client.query('COMMIT');
      res.json({ ok: true, newSubsystemId: newSub.id });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // DELETE /api/sessions/:id
  router.delete('/sessions/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query('DELETE FROM work_sessions WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Session not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  WORKSPACES
  // ════════════════════════════════════════════════════════════════

  // GET /api/workspaces — list all workspace paths with pagination
  router.get('/workspaces', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT w.id, w.system_id, w.subsystem_id, w.workspace_path, w.created_at,
                  s.name AS system_name, sub.name AS subsystem_name
           FROM nebula.system_workspaces w
           LEFT JOIN nebula.systems s ON s.id = w.system_id
           LEFT JOIN nebula.subsystems sub ON sub.id = w.subsystem_id
           ORDER BY s.name, sub.name
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.system_workspaces'),
      ]);

      const items = dataResult.rows.map((r: any) => ({
        ...toEpochMs(r, 'created_at'),
        systemId: r.system_id,
        subsystemId: r.subsystem_id,
        workspacePath: r.workspace_path,
        systemName: r.system_name,
        subsystemName: r.subsystem_name,
      }));

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/workspaces
  router.post('/workspaces', async (req: Request, res: Response) => {
    try {
      const { systemId, subsystemId = null, workspacePath } = req.body;
      if (!systemId || !workspacePath) return res.status(400).json({ error: 'systemId and workspacePath are required' });
      const { rows: [w] } = await pool.query(
        'INSERT INTO nebula.system_workspaces (system_id, subsystem_id, workspace_path) VALUES ($1, $2, $3) RETURNING *',
        [systemId, subsystemId, workspacePath]
      );
      res.status(201).json({
        ...toEpochMs(w, 'created_at'),
        systemId: w.system_id,
        subsystemId: w.subsystem_id,
        workspacePath: w.workspace_path,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/workspaces/:id
  router.delete('/workspaces/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query('DELETE FROM nebula.system_workspaces WHERE id = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Workspace not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  DOCS FILES (read from disk)
  // ════════════════════════════════════════════════════════════════

  const NEXUS_ROOT = path.resolve('/home/codex/dev/nexus');
  const KNOWN_FILES = ['README.md', 'ARCHITECTURE.md', 'README.markdown', 'SPEC.md', 'REFERENCE.md'];

  // ── Shared helper: read known doc files from a workspace directory ──
  function readDocFiles(workspacePath: string): { filename: string; content: string }[] {
    const resolved = path.resolve(NEXUS_ROOT, workspacePath);
    if (!resolved.startsWith(NEXUS_ROOT)) return [];
    if (!fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) return [];

    const files: { filename: string; content: string }[] = [];
    for (const fname of KNOWN_FILES) {
      const fpath = path.join(resolved, fname);
      if (fs.existsSync(fpath) && fs.statSync(fpath).isFile()) {
        files.push({ filename: fname, content: fs.readFileSync(fpath, 'utf-8') });
      }
    }
    return files;
  }

  // GET /api/docs — read README.md and ARCHITECTURE.md from workspace directory
  // Query params: workspacePath (relative to nexus root), e.g. typescript/conduit-mcp
  router.get('/docs', async (req: Request, res: Response) => {
    try {
      const workspacePath = req.query.workspacePath as string;
      if (!workspacePath) return res.status(400).json({ error: 'workspacePath query parameter is required' });
      // Security: prevent directory traversal (explicit 403 for this endpoint)
      const resolved = path.resolve(NEXUS_ROOT, workspacePath);
      if (!resolved.startsWith(NEXUS_ROOT)) {
        return res.status(403).json({ error: 'Path traversal denied' });
      }
      if (!fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) {
        return res.status(404).json({ error: 'Workspace directory not found on disk' });
      }

      const files = readDocFiles(workspacePath);
      res.json({ workspacePath, files, found: files.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/docs — read docs from workspace path for a subsystem
  router.get('/subsystems/:id/docs', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      const { rows: workspaces } = await pool.query(
        'SELECT id, workspace_path FROM nebula.system_workspaces WHERE subsystem_id = $1',
        [id]
      );

      const docs: {
        workspacePath: string;
        files: { filename: string; content: string }[];
      }[] = [];

      for (const ws of workspaces) {
        const files = readDocFiles(ws.workspace_path);
        if (files.length > 0) {
          docs.push({ workspacePath: ws.workspace_path, files });
        }
      }

      res.json({ subsystemId: id, docs, found: docs.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/docs — read docs from all workspaces for a system
  router.get('/systems/:id/docs', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      // Get all workspace paths for this system (including subsystem workspaces)
      const { rows: workspaces } = await pool.query(
        'SELECT id, workspace_path, subsystem_id FROM nebula.system_workspaces WHERE system_id = $1',
        [id]
      );

      const docs: {
        workspacePath: string;
        subsystemId: string | null;
        files: { filename: string; content: string }[];
      }[] = [];

      for (const ws of workspaces) {
        const files = readDocFiles(ws.workspace_path);
        if (files.length > 0) {
          docs.push({
            workspacePath: ws.workspace_path,
            subsystemId: ws.subsystem_id,
            files,
          });
        }
      }

      res.json({ systemId: id, docs, found: docs.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  PLANS DISPLAY (Plan 0134)
  // ════════════════════════════════════════════════════════════════

  // GET /api/plans — list implementation plans with pagination
  // ?status=archived|pending|... & ?page=N & ?pageSize=N
  router.get('/plans', async (req: Request, res: Response) => {
    try {
      const { status } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (status && status !== 'all') {
        clauses.push(`p.status = $${i++}`);
        vals.push(status);
      }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT p.plan_number AS id, p.title, p.goal, p.content,
                  p.files_affected, p.acceptance_criteria, p.dependencies,
                  p.status, p.metadata, p.created_at, p.updated_at,
                  char_length(p.content)::int AS "sizeBytes",
                  p.updated_at AS "modifiedAt"
           FROM nebula.implementation_plans p
           ${where}
           ORDER BY p.updated_at DESC, p.id DESC
           LIMIT $${i} OFFSET $${i+1}`,
          [...vals, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.implementation_plans p ${where}`,
          vals
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/plans/:id — fetch a single implementation plan by plan_number
  router.get('/plans/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [plan] } = await pool.query(
        `SELECT p.plan_number AS id, p.title, p.goal, p.content,
                p.files_affected, p.acceptance_criteria, p.dependencies,
                p.status, p.metadata, p.created_at, p.updated_at,
                char_length(p.content)::int AS "sizeBytes",
                p.updated_at AS "modifiedAt"
         FROM nebula.implementation_plans p
         WHERE p.plan_number = $1`,
        [id]
      );
      if (!plan) return res.status(404).json({ error: `Plan ${id} not found` });
      res.json(plan);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/plans — create a new implementation plan
  // Writes directly to nebula.implementation_plans (the TABLE, not the view).
  // Receipts and tickets are handled downstream by conduit-mcp.
  router.post('/plans', async (req: Request, res: Response) => {
    try {
      const { title, project = 'nexus', goal = '', filesAffected = [], acceptanceCriteria = [], dependencies = [], promptRef = '' } = req.body;
      if (!title) return res.status(400).json({ error: 'title is required' });

      // Slugify title into a filename-safe slug (computed once, reused in retry loop)
      const slug = title
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, '')
        .trim()
        .replace(/\s+/g, '-')
        .slice(0, 50) || 'plan'; // fallback for all-symbol titles

      // Build metadata (promptRef stored in jsonb per upsertPlan convention)
      const metadata: Record<string, any> = {};
      if (promptRef) metadata.prompt_ref = promptRef;

      const now = new Date().toISOString();

      // Retry loop: plan_number has a UNIQUE constraint, so concurrent
      // inserts could collide on MAX(plan_number) + 1. Retry up to 5 times.
      const maxRetries = 5;
      for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
          // Generate plan_number: MAX + 1, zero-padded to 4 digits
          const { rows: [maxRow] } = await pool.query(
            // Only numeric plan_numbers participate in MAX+1 generation; non-numeric
            // rows (e.g. wrconf010-9202bb09 test plans) would break the ::int cast.
            `SELECT MAX(NULLIF(regexp_replace(plan_number, '^0+', ''), '')::int) AS max_id
             FROM nebula.implementation_plans
             WHERE plan_number ~ '^[0-9]+$'`
          );
          const nextId = String((maxRow?.max_id || 0) + 1).padStart(4, '0');
          const fileName = `${slug}-v${nextId}.md`;

          const { rows: [plan] } = await pool.query(
            `INSERT INTO nebula.implementation_plans
             (plan_number, title, goal, content, files_affected, acceptance_criteria, dependencies, status, metadata, created_at, updated_at)
             VALUES ($1, $2, $3, '', $4::text[], $5::jsonb, $6::text[], 'pending', $7::jsonb, $8, $8)
             RETURNING *`,
            [nextId, title, goal,
             filesAffected,  // text[] — pass array directly (pg auto-casts)
             JSON.stringify(acceptanceCriteria),  // jsonb
             dependencies,  // text[]
             JSON.stringify(metadata),  // jsonb
             now]
          );

          return res.status(201).json({
            created: true,
            planNumber: plan.plan_number,
            fileName,
            title: plan.title,
            goal: plan.goal,
            status: plan.status,
            timestamp: now,
          });
        } catch (insertErr: any) {
          // 23505 = unique_violation — another request grabbed the same plan_number
          if (insertErr.code === '23505' && attempt < maxRetries - 1) {
            continue; // retry with a fresh MAX query
          }
          throw insertErr;
        }
      }

      // Should never reach here (last attempt throws), but satisfy TypeScript
      throw new Error('Failed to create plan after max retries');
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/implementation-plans/statuses — distinct status values for filter tabs
  router.get('/implementation-plans/statuses', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT DISTINCT status FROM nebula.implementation_plans ORDER BY status`
      );
      res.json({ statuses: rows.map((r: any) => r.status) });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/implementation-plans — plans linked to a system via cross-refs
  router.get('/systems/:id/implementation-plans', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE hc.system_id = $1
           ORDER BY p.updated_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.system_id = $1`,
          [id]
        ),
      ]);

      res.json({
        systemId: id,
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/implementation-plans — plans linked to a subsystem
  router.get('/subsystems/:id/implementation-plans', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE hc.subsystem_id = $1
           ORDER BY p.updated_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.subsystem_id = $1`,
          [id]
        ),
      ]);

      res.json({
        subsystemId: id,
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id/implementation-plans — plans linked to a feature
  router.get('/features/:id/implementation-plans', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE hc.feature_id = $1
           ORDER BY p.updated_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT p.plan_number)::int AS total
           FROM nebula.implementation_plans p
           JOIN nebula.cross_references cr ON cr.target_type = 'plan'
               AND cr.target_id = p.plan_number
               AND cr.rel_type = 'ag:spawns_plan'
           JOIN nebula.harvest_candidates hc ON hc.id = cr.source_id
               AND cr.source_type = 'harvest_candidate'
           WHERE hc.feature_id = $1`,
          [id]
        ),
      ]);

      res.json({
        featureId: id,
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  AUDIT FILES
  // ════════════════════════════════════════════════════════════════

  const AUDIT_ROOT = path.resolve('/home/codex/dev/nexus/audit');

  // ── Helper: recursively scan audit directory for .md files ──
  function scanAuditDir(dir: string, baseDir: string): { filePath: string; absPath: string; sizeBytes: number; mtime: string }[] {
    const results: { filePath: string; absPath: string; sizeBytes: number; mtime: string }[] = [];
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return results;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const absPath = path.join(dir, entry.name);
      const relPath = path.relative(baseDir, absPath);
      if (entry.isDirectory()) {
        results.push(...scanAuditDir(absPath, baseDir));
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        const st = fs.statSync(absPath);
        results.push({ filePath: relPath, absPath, sizeBytes: st.size, mtime: st.mtime.toISOString() });
      }
    }
    return results;
  }

  // ── Helper: upsert audit files into DB (bulk, with stale cleanup) ──
  async function syncAuditFilesToDb(): Promise<{ id: string; filePath: string; content: string; sizeBytes: number; recordedOn: string }[]> {
    let client: import('pg').PoolClient | undefined;
    try {
      const scanned = scanAuditDir(AUDIT_ROOT, AUDIT_ROOT);
      const scannedPaths = new Set(scanned.map(f => f.filePath));
      client = await pool.connect();
      await client.query('BEGIN');

      // Remove stale entries (files deleted from disk)
      if (scannedPaths.size > 0) {
        await client.query(
          'DELETE FROM audit_files WHERE file_path != ALL($1::text[])',
          [Array.from(scannedPaths)]
        );
      } else {
        await client.query('DELETE FROM audit_files');
      }

      // Upsert each file
      const results: { id: string; filePath: string; content: string; sizeBytes: number; recordedOn: string }[] = [];
      for (const file of scanned) {
        try {
          const content = await fs.promises.readFile(file.absPath, 'utf-8');
          await client.query(
            `UPDATE nebula.audit_files_history
             SET recorded_until_dt = NOW()
             WHERE file_path = $1
               AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
            [file.filePath]
          );
          const { rows: [row] } = await client.query(
            `INSERT INTO nebula.audit_files_history (file_path, content, size_bytes, recorded_on_dt, recorded_until_dt)
             VALUES ($1, $2, $3, NOW(), '9999-12-31 23:59:59+00')
             RETURNING id, file_path, content, size_bytes, recorded_on_dt`,
            [file.filePath, content, file.sizeBytes]
          );
          results.push({
            id: row.id,
            filePath: row.file_path,
            content: row.content,
            sizeBytes: row.size_bytes,
            recordedOn: row.recorded_on_dt,
          });
        } catch (fileErr: any) {
          // Per-file failure: log and skip this file, continue with the rest.
          // This prevents one unreadable file from aborting the entire sync.
          console.warn(`[audit/sync] Skipping ${file.filePath}: ${fileErr.message}`);
        }
      }

      await client.query('COMMIT');
      return results;
    } catch (err) {
      if (client) {
        try { await client.query('ROLLBACK'); } catch { /* connection already gone */ }
      }
      throw err;
    } finally {
      if (client) client.release();
    }
  }

  // GET /api/audit — list all audit files with pagination
  router.get('/audit', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          'SELECT id, file_path, size_bytes, recorded_on_dt FROM audit_files ORDER BY file_path LIMIT $1 OFFSET $2',
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM audit_files'),
      ]);

      res.json({
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
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/audit/graph — agent records as nodes, cross-references as edges
  // ⚠ MUST be before /audit/:id to avoid Express matching 'graph' as a UUID
  router.get('/audit/graph', async (req: Request, res: Response) => {
    try {
      const limit = Math.min(parseInt(req.query.limit as string) || 200, 500);

      const [records, crossRefs] = await Promise.all([
        pool.query(
          `SELECT id, record_type AS entity_type, role, title AS name,
                  substring(content, 1, 300) AS description_abbr,
                  tags, created_at
           FROM nebula.agent_records
           ORDER BY created_at DESC
           LIMIT $1`,
          [limit]
        ),
        pool.query(
          `SELECT id, source_type AS relation_type,
                  source_type AS source_section, source_id,
                  target_type AS target_section, target_id,
                  rel_type, metadata
           FROM nebula.cross_references
           LIMIT $1`,
          [limit]
        ),
      ]);
      res.json({
        entities: records.rows,
        edges: crossRefs.rows,
        entityCount: records.rows.length,
        edgeCount: crossRefs.rows.length,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/audit/:id — get single audit file with content
  router.get('/audit/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT id, file_path, content, size_bytes, recorded_on_dt FROM audit_files WHERE id = $1',
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Audit file not found' });
      res.json({
        id: row.id,
        filePath: row.file_path,
        content: row.content,
        sizeBytes: row.size_bytes,
        updatedAt: new Date(row.recorded_on_dt).getTime(),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/audit/sync — scan filesystem and upsert all audit files
  router.post('/audit/sync', async (_req: Request, res: Response) => {
    try {
      const files = await syncAuditFilesToDb();
      res.json({
        files: files.map(f => ({
          id: f.id,
          filePath: f.filePath,
          content: '',
          sizeBytes: f.sizeBytes,
          recordedOn: new Date(f.recordedOn).getTime(),
        })),
        count: files.length,
      });
    } catch (err: any) {
      // Guard against non-Error objects (e.g. PG internal state, symbol-keyed dumps)
      const message = err?.message ?? String(err ?? 'unknown error');
      console.error('[audit/sync] failed:', message);
      res.status(500).json({ error: message });
    }
  });

  // POST /api/audit/:id/regenerate — re-read this specific file from disk into DB
  router.post('/audit/:id/regenerate', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [existing] } = await pool.query(
        'SELECT file_path FROM audit_files WHERE id = $1', [id]
      );
      if (!existing) return res.status(404).json({ error: 'Audit file not found' });

      const absPath = path.resolve(AUDIT_ROOT, existing.file_path);
      if (!absPath.startsWith(AUDIT_ROOT)) {
        return res.status(403).json({ error: 'Path traversal denied' });
      }
      if (!fs.existsSync(absPath) || !fs.statSync(absPath).isFile()) {
        return res.status(404).json({ error: 'Source file not found on disk' });
      }
      const content = fs.readFileSync(absPath, 'utf-8');
      const st = fs.statSync(absPath);
      const { rows: [updated] } = await pool.query(
        'UPDATE audit_files SET content = $1, size_bytes = $2 WHERE id = $3 RETURNING id, file_path, content, size_bytes, recorded_on_dt',
        [content, st.size, id]
      );
      res.json({
        id: updated.id,
        filePath: updated.file_path,
        content: updated.content,
        sizeBytes: updated.size_bytes,
        updatedAt: new Date(updated.recorded_on_dt).getTime(),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  USER PREFERENCES
  // ════════════════════════════════════════════════════════════════

  // GET /api/preferences — get all preferences for the default user
  router.get('/preferences', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        'SELECT key, value FROM user_preferences WHERE user_id = $1',
        ['default']
      );
      const prefs: Record<string, any> = {};
      rows.forEach(r => { prefs[r.key] = r.value; });
      res.json(prefs);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/preferences/:key — set a single preference
  router.put('/preferences/:key', async (req: Request, res: Response) => {
    try {
      const { key } = req.params;
      const { value } = req.body;
      if (value === undefined) return res.status(400).json({ error: 'value is required' });
      await pool.query(
        `UPDATE nebula.user_preferences_history
         SET recorded_until_dt = NOW()
         WHERE user_id = $1 AND key = $2
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        ['default', key]
      );
      await pool.query(
        `INSERT INTO nebula.user_preferences_history (user_id, key, value, recorded_on_dt, recorded_until_dt)
         VALUES ($1, $2, $3, NOW(), '9999-12-31 23:59:59+00')`,
        ['default', key, JSON.stringify(value)]
      );
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/preferences/:key — delete a single preference (reset to default)
  router.delete('/preferences/:key', async (req: Request, res: Response) => {
    try {
      const { key } = req.params;
      const { rowCount } = await pool.query(
        'DELETE FROM user_preferences WHERE user_id = $1 AND key = $2',
        ['default', key]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Preference not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM INFO TABS
  // ════════════════════════════════════════════════════════════════

  // GET /api/systems/:id/info — get all info tabs for a system with pagination
  router.get('/systems/:id/info', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          'SELECT tab_id, content FROM system_info_tabs WHERE system_id = $1 ORDER BY tab_id LIMIT $2 OFFSET $3',
          [id, pageSize, offset]
        ),
        pool.query(
          'SELECT COUNT(*)::int AS total FROM system_info_tabs WHERE system_id = $1',
          [id]
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/systems/:id/info/:tabId — save an info tab
  router.put('/systems/:id/info/:tabId', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id, tabId } = req.params;
      const { content } = req.body;

      await client.query('BEGIN');

      await client.query(
        `UPDATE nebula.system_info_tabs_history
         SET recorded_until_dt = NOW()
         WHERE system_id = $1 AND tab_id = $2
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        [id, tabId]
      );
      await client.query(
        `INSERT INTO nebula.system_info_tabs_history (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
         VALUES ($1, $2, $3, NOW(), '9999-12-31 23:59:59+00')`,
        [id, tabId, content || '']
      );

      // Reverse link: when a harvest_context tab is cleared (empty content),
      // unlink all candidates from this system.
      if (tabId === 'harvest_context' && (!content || !String(content).trim())) {
        await client.query(
          'UPDATE nebula.harvest_candidates SET system_id = NULL WHERE system_id = $1',
          [id]
        );
      }

      await client.query('COMMIT');
      res.json({ ok: true });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // DELETE /api/systems/:id/info/:tabId — delete an info tab
  // When tabId='harvest_context', also unlinks all candidates from this system.
  router.delete('/systems/:id/info/:tabId', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id, tabId } = req.params;

      await client.query('BEGIN');

      // Close the current row in the history table
      const { rowCount } = await client.query(
        `UPDATE nebula.system_info_tabs_history
         SET recorded_until_dt = NOW()
         WHERE system_id = $1 AND tab_id = $2
           AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
        [id, tabId]
      );

      if (rowCount === 0) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'Info tab not found' });
      }

      // Reverse link: unlinking the harvest_context tab removes the
      // system association from all candidates linked to this system.
      if (tabId === 'harvest_context') {
        await client.query(
          'UPDATE nebula.harvest_candidates SET system_id = NULL WHERE system_id = $1',
          [id]
        );
      }

      await client.query('COMMIT');
      res.json({ ok: true });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  IMPORT / SEED
  // ════════════════════════════════════════════════════════════════

  // POST /api/import — bulk import from localStorage migration
  router.post('/import', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { systems, requirements, workSessions, preferences, infoTabs } = req.body;
      await client.query('BEGIN');
      let count = 0;
      if (systems && Array.isArray(systems)) {
        for (const sys of systems) {
          await client.query(
            'INSERT INTO systems (id, name, description, readme) SELECT $1, $2, $3, $4 WHERE NOT EXISTS (SELECT 1 FROM nebula.systems_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\')',
            [sys.id, sys.name, sys.description || '', sys.readme || null]
          );
          if (sys.folders) {
            for (const f of sys.folders) {
              await client.query(
                'INSERT INTO system_folders (id, system_id, name, category, note) SELECT $1, $2, $3, $4, $5 WHERE NOT EXISTS (SELECT 1 FROM nebula.system_folders_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\')',
                [f.id, sys.id, f.name, f.category, f.note || '']
              );
            }
          }
          if (sys.subsystems) {
            for (const sub of sys.subsystems) {
              await client.query(
                'INSERT INTO subsystems (id, system_id, name, description, readme, color) SELECT $1, $2, $3, $4, $5, $6 WHERE NOT EXISTS (SELECT 1 FROM nebula.subsystems_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\')',
                [sub.id, sys.id, sub.name, sub.description || '', sub.readme || null, sub.color || '#3B82F6']
              );
              if (sub.features) {
                for (const feat of sub.features) {
                  await client.query(
                    'INSERT INTO features (id, subsystem_id, name, description, readme) SELECT $1, $2, $3, $4, $5 WHERE NOT EXISTS (SELECT 1 FROM nebula.features_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\')',
                    [feat.id, sub.id, feat.name, feat.description || '', feat.readme || null]
                  );
                }
              }
            }
          }
          count++;
        }
      }
      if (requirements && Array.isArray(requirements)) {
        for (const r of requirements) {
          await client.query(
            `INSERT INTO requirements (id, system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date, candidate_id)
             SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11
             WHERE NOT EXISTS (SELECT 1 FROM nebula.requirements_history WHERE id = $1 AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
            [r.id, r.systemId, r.subsystemId, r.featureId || null, r.title, r.description || '', r.status || 'Backlog', r.priority || 'Medium', r.startDate || null, r.completionDate || null, r.candidateId || null]
          );
        }
      }
      if (workSessions && Array.isArray(workSessions)) {
        for (const ws of workSessions) {
          await client.query(
            `INSERT INTO work_sessions (id, parent_id, parent_type, parent_name, context, platform, model, outcome, status)
             SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9
             WHERE NOT EXISTS (SELECT 1 FROM nebula.work_sessions_history WHERE id = $1 AND recorded_until_dt = '9999-12-31 23:59:59+00')`,
            [ws.id, ws.parentId, ws.parentType, ws.parentName || '', ws.context || '', ws.platform || '', ws.model || '', ws.outcome || null, ws.status || 'Pending']
          );
        }
      }
      // Migrate preferences
      if (preferences && typeof preferences === 'object') {
        for (const [key, value] of Object.entries(preferences)) {
          await client.query(
            `UPDATE nebula.user_preferences_history
             SET recorded_until_dt = NOW()
             WHERE user_id = $1 AND key = $2
               AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
            ['default', key]
          );
          await client.query(
            `INSERT INTO nebula.user_preferences_history (user_id, key, value, recorded_on_dt, recorded_until_dt)
             VALUES ($1, $2, $3, NOW(), '9999-12-31 23:59:59+00')`,
            ['default', key, JSON.stringify(value)]
          );
        }
      }
      // Migrate info tabs
      if (infoTabs && typeof infoTabs === 'object') {
        for (const [systemId, tabs] of Object.entries(infoTabs)) {
          if (typeof tabs === 'object' && tabs !== null) {
            for (const [tabId, content] of Object.entries(tabs as Record<string, string>)) {
              await client.query(
                `UPDATE nebula.system_info_tabs_history
                 SET recorded_until_dt = NOW()
                 WHERE system_id = $1 AND tab_id = $2
                   AND recorded_until_dt = '9999-12-31 23:59:59+00'`,
                [systemId, tabId]
              );
              await client.query(
                `INSERT INTO nebula.system_info_tabs_history (system_id, tab_id, content, recorded_on_dt, recorded_until_dt)
                 VALUES ($1, $2, $3, NOW(), '9999-12-31 23:59:59+00')`,
                [systemId, tabId, content]
              );
            }
          }
        }
      }
      await client.query('COMMIT');
      res.json({ ok: true, systemsImported: count });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/seed — seed default example data (Plan 0087, idempotent, atomic)
  router.post('/seed', async (_req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');

      // Idempotency check inside transaction — atomic with insert
      const existing = await client.query("SELECT id FROM systems WHERE name = 'E-Commerce Platform'");
      if (existing.rows.length > 0) {
        await client.query('COMMIT');
        return res.json({ ok: true, message: 'Already seeded', systemId: existing.rows[0].id });
      }
      const { rows: [sys] } = await client.query(
        "INSERT INTO systems (name, description, readme) VALUES ('E-Commerce Platform', 'Main customer facing retail platform', '# E-Commerce Platform Architecture\\nThis system handles all customer-facing interactions.\\n\\n## Tech Stack\\n- Angular 21\\n- Node.js API\\n- PostgreSQL') RETURNING *"
      );
      const { rows: [f1] } = await client.query(
        "INSERT INTO system_folders (system_id, name, category, note) VALUES ($1, 'webapp', 'UI', 'Main storefront angular app') RETURNING *",
        [sys.id]
      );
      const { rows: [f2] } = await client.query(
        "INSERT INTO system_folders (system_id, name, category, note) VALUES ($1, 'api-gateway', 'Service', 'BFF for mobile and web') RETURNING *",
        [sys.id]
      );
      const { rows: [sub] } = await client.query(
        "INSERT INTO subsystems (system_id, name, description, readme, color) VALUES ($1, 'Checkout', 'Payment and Order processing', '## Checkout Flow\\n1. Cart validation\\n2. User auth check\\n3. Shipping address\\n4. Payment processing', '#10B981') RETURNING *",
        [sys.id]
      );
      const { rows: [feat] } = await client.query(
        "INSERT INTO features (subsystem_id, name, description, readme) VALUES ($1, 'Payment Gateway', 'Stripe and PayPal integration', 'Integration requirements for Stripe v3 API.') RETURNING *",
        [sub.id]
      );
      await client.query('COMMIT');
      res.status(201).json({
        ok: true,
        systemId: sys.id,
        subsystemId: sub.id,
        featureId: feat.id,
      });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  HARVESTS — database-first harvest pipeline output
  // ════════════════════════════════════════════════════════════════

  // GET /api/harvests — list all harvests with sort/filter support + pagination
  // sort options: candidate_count, code_blocks, turns, block_density, collaboration, created_at
  router.get('/harvests', async (req: Request, res: Response) => {
    try {
      const model = req.query.model as string | undefined;
      const version = req.query.version as string | undefined;
      const sourceHash = req.query.sourceHash as string | undefined;
      const level = req.query.level as string | undefined;
      const visibilityScope = req.query.visibilityScope as string | undefined;
      const tag = req.query.tag as string | undefined;
      const search = req.query.search as string | undefined;
      const systemId = req.query.systemId as string | undefined;
      const subsystemId = req.query.subsystemId as string | undefined;
      const featureId = req.query.featureId as string | undefined;
      // Sort direction: a trailing `_asc` suffix selects ascending order
      // (the nebula-ui harvests view has offered 'created_at_asc'/'Oldest'
      // since fc07c18); everything else defaults to DESC like before.
      const rawSort = (req.query.sort as string) || 'created_at';
      const sortAsc = rawSort.endsWith('_asc');
      const sort = sortAsc ? rawSort.slice(0, -'_asc'.length) : rawSort;
      const sortDir = sortAsc ? 'ASC' : 'DESC';
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const validSorts = ['candidate_count', 'code_blocks', 'turns', 'block_density', 'collaboration', 'created_at', 'tag_frequency', 'keyword_hits'];
      if (!validSorts.includes(sort)) {
        return res.status(400).json({ error: `sort must be one of: ${validSorts.join(', ')}` });
      }

      // NOTE: candidate_count is computed LIVE from harvest_candidates (correlated
      // subquery), NOT from the stored harvests.total_candidates column — that column
      // was stale (only 75/1566 candidate-bearing harvests had it set).
      const liveCandidateCount = `(SELECT count(*) FROM nebula.harvest_candidates c WHERE c.harvest_id = h.id)`;

      // Compute analytics via docklang for sortable metrics
      const sortExpr: Record<string, string> = {
        candidate_count: liveCandidateCount,
        code_blocks:      "COALESCE((h.docklang #>> '{stats,by_type,code}')\n::int, 0)",
        turns:            'COALESCE(jsonb_array_length(h.docklang -> \'discourse_units\'), 0)',
        block_density:    "CASE WHEN jsonb_array_length(h.docklang -> 'discourse_units') > 0 THEN (h.docklang #>> '{stats,total_blocks}')::numeric / jsonb_array_length(h.docklang -> 'discourse_units') ELSE 0 END",
        collaboration:    "(SELECT count(*) FROM jsonb_array_elements(h.docklang -> 'discourse_units') du WHERE du #>> '{provenance,role}' = 'user')",
        created_at:       'h.created_at',
        tag_frequency:    `(SELECT COALESCE(sum(f.tc), 0)
           FROM unnest(h.tags) tg
           JOIN (SELECT t AS tag, count(*) AS tc FROM nebula.harvests h2, unnest(h2.tags) AS t GROUP BY t) f
             ON f.tag = tg)`,
        keyword_hits:     `(SELECT count(*) FROM jsonb_array_elements(h.docklang -> 'discourse_units') du
            WHERE du #>> '{body}' ILIKE '%' || $1 || '%')`,
      };

      // Handle keyword_hits: keyword must be first param so it references $1
      const keyword = req.query.keyword as string | undefined;

      if (sort === 'keyword_hits' && !keyword) {
        return res.status(400).json({ error: 'keyword query parameter is required when sort=keyword_hits' });
      }

      const clauses: string[] = [];
      const filterParams: any[] = [];
      const params: any[] = [];
      let pi = 1;
      if (sort === 'keyword_hits' && keyword) { params.push(keyword); pi++; }  // keyword is $1
      if (model) { clauses.push(`h.model = $${pi++}`); params.push(model); filterParams.push(model); }
      if (version) { clauses.push(`h.version = $${pi++}`); params.push(parseInt(version)); filterParams.push(parseInt(version)); }
      if (sourceHash) { clauses.push(`h.source_hash = $${pi++}`); params.push(sourceHash); filterParams.push(sourceHash); }
      if (level) { clauses.push(`h.level = $${pi++}`); params.push(parseInt(level)); filterParams.push(parseInt(level)); }
      if (visibilityScope) { clauses.push(`h.visibility_scope = $${pi++}`); params.push(visibilityScope); filterParams.push(visibilityScope); }
      if (tag) { clauses.push(`$${pi++} = ANY(h.tags)`); params.push(tag); filterParams.push(tag); }
      if (search) {
        // Concatenate the searchable fields into ONE string and match it with a
        // single placeholder — the count-query renumbering below assumes each
        // clause has exactly one placeholder.
        clauses.push(`(COALESCE(h.source_filename, '') || ' ' || COALESCE(h.source_path, '') || ' ' || COALESCE(h.model, '')) ILIKE '%' || $${pi} || '%'`);
        params.push(search); filterParams.push(search); pi++;
      }
      // Hierarchy filter — a harvest belongs to a tree node if it has ≥1 candidate
      // linked to that system/subsystem/feature. Multiple levels OR together so the
      // tree's deepest selection (e.g. a feature) also matches its ancestor levels.
      if (systemId || subsystemId || featureId) {
        const ors: string[] = [];
        if (systemId) { ors.push(`c.system_id = $${pi++}`); params.push(systemId); filterParams.push(systemId); }
        if (subsystemId) { ors.push(`c.subsystem_id = $${pi++}`); params.push(subsystemId); filterParams.push(subsystemId); }
        if (featureId) { ors.push(`c.feature_id = $${pi++}`); params.push(featureId); filterParams.push(featureId); }
        clauses.push(`EXISTS (SELECT 1 FROM nebula.harvest_candidates c WHERE c.harvest_id = h.id AND (${ors.join(' OR ')}))`);
      }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      // Count-query WHERE: renumber clause placeholders sequentially so the count
      // statement matches filterParams ordering. Handles multi-placeholder clauses
      // (e.g. the hierarchy EXISTS) via a running counter, not per-clause replace.
      let countWhere = '';
      if (clauses.length > 0) {
        let ci = 1;
        countWhere = 'WHERE ' + clauses.map(c => c.replace(/\$\d+/g, () => `$${ci++}`)).join(' AND ');
      }

      // NOTE (2026-07-31): restructured to inner-select + LIMIT first, then compute the
      // expensive docklang analytics only on the returned page. Previously the per-row
      // JSONB subqueries (user_turns etc.) ran across ALL harvests before LIMIT applied,
      // making /api/harvests take ~22s and time out in the Nebula UI.
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
                WHERE du #>> '{provenance,role}' = 'user') AS user_turns,
               ${sort === 'keyword_hits' ? "(SELECT count(*) FROM jsonb_array_elements(s.docklang -> 'discourse_units') du WHERE du #>> '{body}' ILIKE '%' || $1 || '%') AS keyword_hits" : '0::bigint AS keyword_hits'},
               ${sort === 'tag_frequency' ? "(SELECT COALESCE(sum(freq), 0) FROM (SELECT count(*) AS freq FROM nebula.harvests h2, unnest(h2.tags) AS t WHERE t = ANY(s.tags) GROUP BY t) sub) AS tag_frequency" : '0::bigint AS tag_frequency'}
        FROM (
          SELECT h.id, h.source_path, h.source_filename, h.model,
                 ${liveCandidateCount} AS total_candidates, h.tags, h.metadata, h.created_at,
                 h.level, h.visibility_scope,
                 h.source_hash, h.file_size, h.version, h.run_metadata,
                 h.docklang
          FROM nebula.harvests h
          ${where}
          ORDER BY ${sort === 'created_at' ? `h.created_at ${sortDir} NULLS LAST, h.id ${sortDir}` : `${sortExpr[sort]} ${sortDir} NULLS LAST`}
          LIMIT $${pi} OFFSET $${pi + 1}
        ) s`;

      // Parallel count query with same filters (no sort expression needed).
      // NOTE (2026-07-31): countParams must contain ONLY the WHERE-clause params — the
      // sort-only keyword param ($1) used for keyword_hits caused a bind mismatch
      // ("supplies 1 parameters, but prepared statement requires 0") whenever the
      // keyword filter was absent.
      const countQuery = `SELECT COUNT(*)::int AS total FROM nebula.harvests h ${countWhere}`;

      params.push(pageSize, (page - 1) * pageSize);
      const [dataResult, countResult] = await Promise.all([
        pool.query(dataQuery, params),
        pool.query(countQuery, filterParams),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      // Dual-shape response: {items,total} is the canonical paginated shape;
      // {harvests,count} is the legacy shape the nebula-ui DataService reads
      // (listHarvests → fetchHarvests sets this.harvests.set(data.harvests || [])).
      // Same backward-compat pattern as the harvest-candidates endpoints.
      res.json({ items, harvests: items, total, count: total, page, pageSize, sort });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/harvests/distribution — analytics histograms across all harvests
  router.get('/harvests/distribution', async (_req: Request, res: Response) => {
    try {
      // Turn count histogram
      const { rows: turnBuckets } = await pool.query(`
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
      `);

      // Block type totals across all harvests
      const { rows: blockTypes } = await pool.query(`
        SELECT block_type, count(*) AS cnt
        FROM nebula.harvest_blocks()
        GROUP BY block_type ORDER BY cnt DESC
      `);

      // Top tags across all harvests
      const { rows: topTags } = await pool.query(`
        SELECT t AS tag, count(*) AS cnt
        FROM nebula.harvests h, unnest(h.tags) AS t
        WHERE h.tags IS NOT NULL AND array_length(h.tags, 1) > 0
        GROUP BY t ORDER BY cnt DESC LIMIT 20
      `);

      // Totals
      const { rows: [totals] } = await pool.query(
        'SELECT count(*) AS total_harvests, sum(total_candidates)::int AS total_candidates, avg(total_candidates)::numeric(5,1) AS avg_candidates_per_harvest FROM nebula.harvests'
      );

      res.json({
        turnDistribution: turnBuckets,
        blockTypeDistribution: blockTypes,
        topTags,
        totals,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/harvests/:id — full harvest with candidates
  router.get('/harvests/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.harvests WHERE id = $1', [id]
      );
      if (!row) return res.status(404).json({ error: 'Harvest not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/harvests/:id/transcript — reconstructed conversation with code/diagrams
  router.get('/harvests/:id/transcript', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [harvest] } = await pool.query(
        'SELECT id, source_filename, docklang #>> \'{meta,title}\' AS title FROM nebula.harvests WHERE id = $1', [id]
      );
      if (!harvest) return res.status(404).json({ error: 'Harvest not found' });

      const { rows: units } = await pool.query(`
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
              'content', CASE WHEN b ? 'content' THEN b #>> '{content}' ELSE NULL END,
              'items', CASE WHEN b ? 'items' THEN b -> 'items' ELSE NULL END
            ) ORDER BY (b #>> '{provenance,block_index}')::int
          ) AS blocks
        FROM nebula.harvests h,
             LATERAL jsonb_array_elements(h.docklang -> 'discourse_units') AS du_elem,
             LATERAL jsonb_array_elements(du_elem -> 'blocks') AS b
        WHERE h.id = $1 AND h.docklang IS NOT NULL AND h.docklang ? 'discourse_units'
        GROUP BY turn_index, heading, role, body, block_count
        ORDER BY turn_index
      `, [id]);

      // Count block types
      const { rows: [stats] } = await pool.query(
        "SELECT h.docklang -> 'stats' AS stats FROM nebula.harvests h WHERE h.id = $1", [id]
      );

      // Get candidates
      const { rows: candidates } = await pool.query(
        'SELECT id, title, status, completed, system_id, intent_description FROM nebula.harvest_candidates WHERE harvest_id = $1 ORDER BY created_at', [id]
      );

      // Get snapshot context for the segment/override integration
      // convention: conversation_id = harvest_id (set by auto-segment trigger)
      let snapshotId: string | null = null;
      let committedSegments: any[] = [];
      let activeOverrides: any[] = [];
      try {
        const { rows: snapshots } = await pool.query(
          `SELECT id FROM nebula.conversation_snapshots
           WHERE conversation_id = $1
           ORDER BY snapshot_index DESC LIMIT 1`,
          [id]
        );
        if (snapshots.length > 0) {
          snapshotId = snapshots[0].id;
          const { rows: segments } = await pool.query(
            `SELECT id, conversation_id, snapshot_id, start_block_id, end_block_id,
                    start_block_index, end_block_index, segment_type, state, source,
                    title, notes_md, created_by,
                    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
             FROM nebula.segments
             WHERE snapshot_id = $1
             ORDER BY start_block_index`,
            [snapshotId]
          );
          committedSegments = segments;
          const { rows: overrides } = await pool.query(
            `SELECT id, conversation_id, snapshot_id, target_type, target_id,
                    projection_target, override_type, reason_code, notes_md,
                    source, created_by,
                    to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at
             FROM nebula.projection_overrides
             WHERE snapshot_id = $1
             ORDER BY created_at`,
            [snapshotId]
          );
          activeOverrides = overrides;
        }
      } catch (_) {
        // snapshot table may not exist or no snapshots yet — non-fatal
      }

      res.json({
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
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/harvest-candidates/:id/promote — mark candidate as useful
  router.post('/harvest-candidates/:id/promote', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { status = 'useful' } = req.body;
      const validStatuses = ['useful', 'rejected', 'promoted'];
      if (!validStatuses.includes(status)) {
        return res.status(400).json({ error: `status must be one of: ${validStatuses.join(', ')}` });
      }
      const { rows: [result] } = await pool.query(
        'SELECT nebula.set_candidate_status($1, $2) AS result', [id, status]
      );
      res.json({ ok: true, result: result?.result });
    } catch (err: any) {
      res.status(400).json({ error: err.message });
    }
  });

  // POST /api/harvest-candidates/promote-to-plan — RETIRED (architect ruling on
  // to-do e68449f2): the backing nebula.candidates_to_plan() procedure was dropped
  // in a migration and must NOT be resurrected — spawn_requirement_from_candidate is
  // the canonical promotion path (MCP-surfaced, sets requirements.candidate_id).
  // This route now fails loudly so callers migrate instead of 400-ing mysteriously.
  router.post('/harvest-candidates/promote-to-plan', async (_req: Request, res: Response) => {
    res.status(410).json({
      error: 'promote-to-plan was retired: its backing procedure (nebula.candidates_to_plan) no longer exists and will not be restored.',
      useInstead: 'POST /api/harvest-candidates/:id/spawn-requirement',
      mcpTool: 'nebula_spawn_requirement_from_candidate',
      rationale: 'Single canonical promotion flow; the legacy parallel route created two competing promotion paths. See architect ruling on to-do e68449f2; verb renamed per decision 319defa5.',
    });
  });

  // POST /api/harvests — create a new harvest record AND unpack candidates
  // into harvest_candidates (dual-write: JSONB preserved for Rover + relational for linking)
  router.post('/harvests', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { sourcePath, sourceFilename, model, totalCandidates, candidates, sourceText, tags, metadata, level, visibilityScope, sourceHash, fileSize, runMetadata, docklang } = req.body;
      if (!sourcePath) return res.status(400).json({ error: 'sourcePath is required' });
      await client.query('BEGIN');

      // 1. Insert the harvest (trigger auto-computes version and source_hash)
      const { rows: [row] } = await client.query(
        `INSERT INTO nebula.harvests (source_path, source_filename, model, total_candidates, candidates, source_text, tags, metadata, level, visibility_scope, source_hash, file_size, run_metadata, docklang)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) RETURNING *`,
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
      );

      // 2. Unpack each candidate into harvest_candidates (redundant but non-destructive)
      const candidateList: any[] = candidates || [];
      for (const c of candidateList) {
        await client.query(
          `INSERT INTO nebula.harvest_candidates (harvest_id, title, intent_description, implementation_notes, code_snippets, open_questions, tags, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
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
        );
      }

      await client.query('COMMIT');
      res.status(201).json(row);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // DELETE /api/harvests/:id
  router.delete('/harvests/:id', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id } = req.params;
      await client.query('BEGIN');
      // Manual cascade: harvest_candidates has no FK because harvests is a view
      await client.query('UPDATE nebula.harvest_candidates SET valid_until = now() WHERE harvest_id = $1 AND valid_until > now()', [id]);
      const { rowCount } = await client.query('UPDATE nebula.harvests SET valid_until = now() WHERE id = $1 AND valid_until > now()', [id]);
      if (rowCount === 0) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'Harvest not found' });
      }
      await client.query('COMMIT');
      res.json({ expired: true });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  HARVEST CANDIDATES — normalized relational access to harvest data
  // ════════════════════════════════════════════════════════════════

  // GET /api/plans/:planRef/candidates — reverse lookup: find all
  // harvest_candidates linked to a given conduit plan via cross_references.
  router.get('/plans/:planRef/candidates', async (req: Request, res: Response) => {
    try {
      const { planRef } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.open_questions,
                  hc.implementation_notes, hc.code_snippets,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source,
                  cr.created_at AS linked_at
           FROM nebula.harvest_candidates hc
           JOIN nebula.cross_references cr ON cr.source_id = hc.id::text
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE cr.source_type = 'harvest_candidate'
             AND cr.target_type = 'plan'
             AND cr.target_id = $1
             AND cr.rel_type = 'ag:spawns_plan'
           ORDER BY cr.created_at DESC
           LIMIT $2 OFFSET $3`,
          [planRef, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           JOIN nebula.cross_references cr ON cr.source_id = hc.id::text
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE cr.source_type = 'harvest_candidate'
             AND cr.target_type = 'plan'
             AND cr.target_id = $1
             AND cr.rel_type = 'ag:spawns_plan'`,
          [planRef]
        ),
      ]);

      res.json({ planRef, items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/harvest-candidates — list all harvest candidates
  // linked to a specific system (direct filter by system_id).
  router.get('/systems/:id/harvest-candidates', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.open_questions,
                  hc.implementation_notes, hc.code_snippets,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.system_id = $1
           ORDER BY hc.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.system_id = $1`,
          [id]
        ),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      res.json({ systemId: id, items, candidates: items, total, count: total, page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/harvest-candidates — list all harvest
  // candidates linked to a specific subsystem (filter by subsystem_id).
  router.get('/subsystems/:id/harvest-candidates', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.open_questions,
                  hc.implementation_notes, hc.code_snippets,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.subsystem_id = $1
           ORDER BY hc.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.subsystem_id = $1`,
          [id]
        ),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      res.json({ subsystemId: id, items, candidates: items, total, count: total, page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id/harvest-candidates — list all harvest
  // candidates linked to a specific feature (filter by feature_id).
  router.get('/features/:id/harvest-candidates', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description,
                  hc.status, hc.completed, hc.tags, hc.open_questions,
                  hc.implementation_notes, hc.code_snippets,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.feature_id = $1
           ORDER BY hc.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           WHERE hc.feature_id = $1`,
          [id]
        ),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      res.json({ featureId: id, items, candidates: items, total, count: total, page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  //  AGENDAS (scoped by hierarchy via agenda_items → requirements)
  // ════════════════════════════════════════════════════════════════


  // POST /api/agendas — create a new agenda. INTERIM scheme 2026-08-25:
  // the engineering loop's spec-row path is agenda → items → finalize
  // (agenda_to_specification); superseded by Wind doctrine.
  router.post('/agendas', async (req: Request, res: Response) => {
    try {
      const { title, scope = null, status = 'draft', metadata = {} } = req.body;
      if (!title) return res.status(400).json({ error: 'title is required' });
      const VALID = ['draft', 'ready_for_review', 'in_review', 'specified', 'archived'];
      if (!VALID.includes(status)) {
        return res.status(400).json({ error: `status must be one of: ${VALID.join(', ')}` });
      }
      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.agendas (title, scope, status, metadata)
         VALUES ($1, $2, $3, $4) RETURNING *`,
        [title, scope || null, status, JSON.stringify(metadata || {})]
      );
      res.status(201).json({ ok: true, agenda: row });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/agendas — list ALL agendas (unscoped, when no hierarchy selected)
  router.get('/agendas', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.agendas'),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
    // GET /api/agendas/:id — single agenda with items
  router.get('/agendas/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
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
         WHERE a.id = $1`,
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Agenda not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/agendas — list agendas scoped to a system, with nested items
  router.get('/systems/:id/agendas', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT a.id, a.title, a.scope, a.status, a.cohesion_score,
                  a.overlap_matrix, a.source_count, a.planner_analysis,
                  a.planner_conflicts, a.planner_gaps, a.metadata,
                  a.created_at, a.updated_at
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.system_id = $1
           ORDER BY a.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT a.id)::int AS total
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.system_id = $1`,
          [id]
        ),
      ]);

      // Fetch items for each agenda
      const items = dataResult.rows;
      for (const a of items) {
        const { rows: agendaItems } = await pool.query(
          `SELECT ai.id, ai.source_type, ai.source_id, ai.title, ai.body,
                  ai.decisions, ai.open_questions, ai.supporting_refs,
                  ai.included, ai.planner_note, ai.created_at, ai.updated_at
           FROM nebula.agenda_items ai
           WHERE ai.agenda_id = $1
           ORDER BY ai.created_at ASC`,
          [a.id]
        );
        a.items = agendaItems;
        a.item_count = agendaItems.length;
      }

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/agendas — list agendas scoped to a subsystem, with nested items
  router.get('/subsystems/:id/agendas', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT a.id, a.title, a.scope, a.status, a.cohesion_score,
                  a.overlap_matrix, a.source_count, a.planner_analysis,
                  a.planner_conflicts, a.planner_gaps, a.metadata,
                  a.created_at, a.updated_at
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.subsystem_id = $1
           ORDER BY a.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT a.id)::int AS total
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.subsystem_id = $1`,
          [id]
        ),
      ]);

      const items = dataResult.rows;
      for (const a of items) {
        const { rows: agendaItems } = await pool.query(
          `SELECT ai.id, ai.source_type, ai.source_id, ai.title, ai.body,
                  ai.decisions, ai.open_questions, ai.supporting_refs,
                  ai.included, ai.planner_note, ai.created_at, ai.updated_at
           FROM nebula.agenda_items ai
           WHERE ai.agenda_id = $1
           ORDER BY ai.created_at ASC`,
          [a.id]
        );
        a.items = agendaItems;
        a.item_count = agendaItems.length;
      }

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id/agendas — list agendas scoped to a feature, with nested items
  router.get('/features/:id/agendas', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT a.id, a.title, a.scope, a.status, a.cohesion_score,
                  a.overlap_matrix, a.source_count, a.planner_analysis,
                  a.planner_conflicts, a.planner_gaps, a.metadata,
                  a.created_at, a.updated_at
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.feature_id = $1
           ORDER BY a.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(DISTINCT a.id)::int AS total
           FROM nebula.agendas a
           JOIN nebula.agenda_items ai ON ai.agenda_id = a.id
           LEFT JOIN nebula.requirements req ON req.id = ai.source_id AND ai.source_type = 'requirement'
           WHERE req.feature_id = $1`,
          [id]
        ),
      ]);

      const items = dataResult.rows;
      for (const a of items) {
        const { rows: agendaItems } = await pool.query(
          `SELECT ai.id, ai.source_type, ai.source_id, ai.title, ai.body,
                  ai.decisions, ai.open_questions, ai.supporting_refs,
                  ai.included, ai.planner_note, ai.created_at, ai.updated_at
           FROM nebula.agenda_items ai
           WHERE ai.agenda_id = $1
           ORDER BY ai.created_at ASC`,
          [a.id]
        );
        a.items = agendaItems;
        a.item_count = agendaItems.length;
      }

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  // ════════════════════════════════════════════════════════════════
  // DELETE /api/agendas/:id/items — remove an agenda item by source_id
  // Query: ?sourceId=<uuid> — finds and deletes the item matching that source
  router.delete('/agendas/:id/items', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const sourceId = req.query.sourceId as string;
      if (!sourceId) return res.status(400).json({ error: 'sourceId query parameter is required' });
      const { rowCount } = await pool.query(
        `UPDATE nebula.agenda_items SET valid_until = now() WHERE agenda_id = $1 AND source_id = $2 AND valid_until > now()`,
        [id, sourceId]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Agenda item not found' });
      res.json({ ok: true, deleted: rowCount });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/agendas/:id/finalize — create a specification from an agenda
  router.post('/agendas/:id/finalize', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { revisionType = 'created' } = req.body;
      const { rows: [result] } = await pool.query(
        'SELECT nebula.agenda_to_specification($1, $2) AS spec_id',
        [id, revisionType]
      );
      res.status(201).json({ ok: true, spec_id: result.spec_id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/agendas/:id/items — add a single item to an existing agenda
  router.post('/agendas/:id/items', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { sourceType, sourceId, title, body, decisions, openQuestions, supportingRefs, included, plannerNote } = req.body;
      if (!sourceType || !sourceId || !title) {
        return res.status(400).json({ error: 'sourceType, sourceId, and title are required' });
      }
      const { rows: [item] } = await pool.query(
        'INSERT INTO nebula.agenda_items (agenda_id, source_type, source_id, title, body, decisions, open_questions, supporting_refs, included, planner_note) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *',
        [id, sourceType, sourceId, title, body || null, JSON.stringify(decisions || []), JSON.stringify(openQuestions || []), JSON.stringify(supportingRefs || []), included ?? true, plannerNote || null]
      );
      res.status(201).json({ ok: true, item });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  //  SPECIFICATIONS (settled output from agendas — scoped via specs view)
  // ════════════════════════════════════════════════════════════════



  // GET /api/specifications — list ALL specification revisions (unscoped, from nebula.specifications versioned snapshots)
  router.get('/specifications', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.active_specifications'),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
    // GET /api/specifications/:id — single specification revision
  router.get('/specifications/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
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
         WHERE s.id = $1`,
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Specification not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/specifications — list specification revisions scoped to a system
  router.get('/systems/:id/specifications', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE req.system_id = $1
           ORDER BY s.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.active_specifications s
           LEFT JOIN nebula.requirements req ON EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_id' = req.id::text)
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_type' = 'requirement')
           WHERE req.system_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/specifications — list specification revisions scoped to a subsystem
  router.get('/subsystems/:id/specifications', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE req.subsystem_id = $1
           ORDER BY s.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.active_specifications s
           LEFT JOIN nebula.requirements req ON EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_id' = req.id::text)
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_type' = 'requirement')
           WHERE req.subsystem_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id/specifications — list specification revisions scoped to a feature
  router.get('/features/:id/specifications', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
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
           WHERE req.feature_id = $1
           ORDER BY s.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.active_specifications s
           LEFT JOIN nebula.requirements req ON EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_id' = req.id::text)
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(s.item_snapshot) AS item WHERE item->>'source_type' = 'requirement')
           WHERE req.feature_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  // ════════════════════════════════════════════════════════════════
  //  WORK REQUESTS (scoped via requirements OR specifications → agenda items → harvest_candidates)
  // ════════════════════════════════════════════════════════════════


  // GET /api/work-requests — list ALL work requests with pagination
  router.get('/work-requests', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT wr.*
           FROM nebula.work_requests wr
           ORDER BY wr.created_at DESC
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.work_requests'),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
    // GET /api/work-requests/:id — single work request
  router.get('/work-requests/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        `SELECT wr.*
         FROM nebula.work_requests wr
         WHERE wr.id = $1`,
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Work request not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/systems/:id/work-requests — list work requests scoped to a system
  router.get('/systems/:id/work-requests', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT ON (wr.id) wr.*
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.system_id = $1
           ORDER BY wr.id, wr.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.system_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/subsystems/:id/work-requests — list work requests scoped to a subsystem
  router.get('/subsystems/:id/work-requests', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT ON (wr.id) wr.*
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.subsystem_id = $1
           ORDER BY wr.id, wr.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.subsystem_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/features/:id/work-requests — list work requests scoped to a feature
  router.get('/features/:id/work-requests', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT DISTINCT ON (wr.id) wr.*
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.feature_id = $1
           ORDER BY wr.id, wr.created_at DESC
           LIMIT $2 OFFSET $3`,
          [id, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.work_requests wr
           LEFT JOIN nebula.requirements req ON req.id = wr.source_requirement_id
           LEFT JOIN nebula.specifications spec ON spec.id = wr.source_specification_id
           LEFT JOIN nebula.agenda_items ai ON ai.agenda_id = spec.agenda_id AND ai.included = true
           WHERE req.feature_id = $1`,
          [id]
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });
  // GET /api/harvest-candidates — list candidates, filterable by harvest or hierarchy
  // Sweep-tooling extras (to-do 7e2d116f): ?completed=true|false, ?status=<value>,
  // and an endpoint-local page cap of 1000 (global default stays 100) so a
  // sweep can pull the full ~100+ candidate set in one call. `total` is always
  // returned so callers know when more pages exist.
  router.get('/harvest-candidates', async (req: Request, res: Response) => {
    try {
      const { harvestId, systemId, subsystemId, featureId } = req.query;
      const { completed, status } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);
      const requestedSize = parseInt(String(req.query.pageSize ?? req.query.limit ?? ''), 10);
      const effPageSize = !isNaN(requestedSize) ? Math.min(1000, Math.max(1, requestedSize)) : pageSize;
      const effOffset = page > 1 ? (page - 1) * effPageSize : offset;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (harvestId) { clauses.push(`hc.harvest_id = $${i++}`); vals.push(harvestId); }
      if (systemId) { clauses.push(`hc.system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId) { clauses.push(`hc.subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId) { clauses.push(`hc.feature_id = $${i++}`); vals.push(featureId); }
      if (completed === 'true' || completed === 'false') { clauses.push(`hc.completed = $${i++}`); vals.push(completed === 'true'); }
      if (status) { clauses.push(`hc.status = $${i++}`); vals.push(status); }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description, hc.status, hc.tags,
                  hc.implementation_notes, hc.code_snippets, hc.open_questions,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.work_request_id, hc.completed,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}
           ORDER BY hc.created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, effPageSize, effOffset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}`,
          vals
        ),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      res.json({
        items,
        candidates: items,
        total,
        count: total,
        page,
        pageSize: effPageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/harvest-candidates/:id — full candidate with all fields
  router.get('/harvest-candidates/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.harvest_candidates WHERE id = $1', [id]
      );
      if (!row) return res.status(404).json({ error: 'Harvest candidate not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Completion-sweep tooling (planner fc9ebf7b / architect 83d2fd5c) ──────
  // Per candidate: linked plans (ag:spawns_plan cross-ref → plan status),
  // linked work requests (folded state), title-similar agent records,
  // and an aggregate `completed` verdict. Replaces planners' hand-rolled
  // completion sweeps with one call. DBA owns indexes (pg_trgm etc.);
  // the interface is stable under index hardening.
  const RECORD_MATCH_LIMIT = 5;
  const RECORD_MATCH_MIN_SIMILARITY = 0.55;

  // ── Archive-pointer resolution (planner to-do 3c204f0c / sweep-tooling 6/6) ──
  // Historical conduit plans (e.g. 1058, 1234) are not in nebula.implementation_plans;
  // their history lives only as filesystem projections under audit/IMPLEMENTATION_PLANS/.
  // Xref targets that don't resolve to a live plan row resolve instead to an explicit
  // archive pointer (status 'archived:<relative-path>') so sweeps deterministically find
  // the projection. Pointers are data, not authority — DB-first doctrine intact.
  const PLAN_ARCHIVE_ROOT = process.env.NEBULA_PLAN_ARCHIVE_ROOT
    || '/home/codex/dev/nexus/audit/IMPLEMENTATION_PLANS';
  const PLAN_ARCHIVE_DIRS = ['completed', 'active', 'pending', 'planning', 'proposed'];

  function resolvePlanArchivePointer(planNumber: string): string | null {
    try {
      // Token match with delimiters so '105' never matches '1058-...'.
      // Try both raw and zero-padded forms (files pad to 4 digits).
      const tokens = [...new Set([planNumber, planNumber.padStart(4, '0')])];
      const tokenRes = tokens.map((t) => new RegExp(`(^|-)${t}(-|\\.md$)`, 'i'));
      for (const dir of PLAN_ARCHIVE_DIRS) {
        const dirPath = path.join(PLAN_ARCHIVE_ROOT, dir);
        if (!fs.existsSync(dirPath)) continue;
        const hit = fs.readdirSync(dirPath).find((f) => f.endsWith('.md') && tokenRes.some((re) => re.test(f)));
        if (hit) return `${dir}/${hit}`;
      }
    } catch {
      // Best-effort: an unreadable archive falls through to 'unresolved'.
    }
    return null;
  }

  async function computeCandidateCompletion(client: any, id: string): Promise<any | null> {
    const { rows: [cand] } = await client.query(
      'SELECT id, title, status, completed FROM nebula.harvest_candidates WHERE id = $1', [id]
    );
    if (!cand) return null;

    // Plans spawned by this candidate (target_id holds the plan number/ref).
    // Live plans resolve to their nebula status; historical ones resolve to an
    // explicit archive pointer (sweep-tooling 6/6) or 'unresolved' as last resort.
    const xrefTargets = await client.query(
      `SELECT cr.target_id AS number
         FROM nebula.cross_references cr
        WHERE cr.source_type = 'harvest_candidate'
          AND cr.source_id = $1
          AND cr.rel_type = 'ag:spawns_plan'
          AND cr.valid_until = '9999-12-31 00:00:00+00'::timestamptz`,
      [id]
    );
    const plans: any[] = [];
    for (const t of xrefTargets.rows) {
      const { rows: live } = await client.query(
        'SELECT ip.plan_number AS number, ip.status FROM nebula.implementation_plans ip WHERE ip.plan_number::text = $1 LIMIT 1',
        [t.number]
      );
      if (live.length > 0) {
        plans.push({ number: live[0].number, status: live[0].status });
      } else {
        const pointer = resolvePlanArchivePointer(String(t.number));
        plans.push({ number: t.number, status: pointer ? `archived:${pointer}` : 'unresolved' });
      }
    }

    // Work request folded state via the candidate's direct WR link.
    const workRequests = await client.query(
      `SELECT wr.wr_id AS "wrId", wr.status AS "foldedState"
         FROM nebula.harvest_candidates hc
         JOIN vision.work_requests wr ON wr.wr_id = hc.work_request_id::text
        WHERE hc.id = $1 AND hc.work_request_id IS NOT NULL`,
      [id]
    );

    // Title-similar agent records (pg_trgm). Optional enrichment; empty is fine.
    let recordMatches: any[] = [];
    if (cand.title) {
      const recs = await client.query(
        `SELECT ar.id AS "recordId", ar.title,
                public.similarity(ar.title, $1::text) AS score
           FROM nebula.agent_records ar
          WHERE ar.title OPERATOR(public.%) $1::text
             OR public.similarity(ar.title, $1::text) > $2
          ORDER BY public.similarity(ar.title, $1::text) DESC
          LIMIT $3`,
        [cand.title, RECORD_MATCH_MIN_SIMILARITY, RECORD_MATCH_LIMIT]
      );
      recordMatches = recs.rows;
    }

    const DONE_PLAN_STATUSES = new Set(['completed', 'archived']);
    const planCompleted = plans.some((p: any) => String(p.status || '').toLowerCase().startsWith('archived:')
      || DONE_PLAN_STATUSES.has(String(p.status || '').toLowerCase()));
    const wrCompleted = workRequests.rows.some((w: any) =>
      ['settled'].includes(String(w.foldedState || '').toLowerCase()));

    return {
      candidateId: cand.id,
      title: cand.title,
      status: cand.status,
      plans,
      workRequests: workRequests.rows,
      recordMatches,
      completed: Boolean(cand.completed) || planCompleted || wrCompleted,
    };
  }

  router.get('/harvest-candidates/:id/completion', async (req: Request, res: Response) => {
    try {
      const result = await computeCandidateCompletion(pool, String(req.params.id));
      if (!result) return res.status(404).json({ error: 'Harvest candidate not found' });
      res.json(result);
    } catch (err: any) {
      // 22P02 (invalid uuid/text representation) → treat as not found
      if (err?.code === '22P02') return res.status(404).json({ error: 'Harvest candidate not found' });
      res.status(500).json({ error: err.message });
    }
  });

  // Batch variant: POST /api/harvest-candidates/completion-sweep { ids: [...] }
  // Missing/unknown ids are reported per-id rather than failing the batch.
  router.post('/harvest-candidates/completion-sweep', async (req: Request, res: Response) => {
    try {
      const ids: unknown = req.body?.ids;
      if (!Array.isArray(ids) || ids.length === 0 ||
          !ids.every((v) => typeof v === 'string' && v.length > 0)) {
        return res.status(400).json({ error: 'body must be { ids: string[] } (non-empty)' });
      }
      if (ids.length > 500) {
        return res.status(400).json({ error: 'batch limited to 500 ids' });
      }
      const results: any[] = [];
      for (const id of ids as string[]) {
        try {
          const r = await computeCandidateCompletion(pool, id);
          results.push(r ?? { candidateId: id, missing: true });
        } catch (e: any) {
          results.push({ candidateId: id, error: e.message });
        }
      }
      res.json({ count: results.length, completedCount: results.filter((r) => r.completed === true).length, results });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  // ── /candidates alias — mirrors /harvest-candidates for Assembly UI ──────

  router.get('/candidates', async (req: Request, res: Response) => {
    try {
      const { harvestId, systemId, subsystemId, featureId } = req.query;
      const { completed, status } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);
      // Same sweep-tooling cap raise as /harvest-candidates (to-do 7e2d116f).
      const requestedSize = parseInt(String(req.query.pageSize ?? req.query.limit ?? ''), 10);
      const effPageSize = !isNaN(requestedSize) ? Math.min(1000, Math.max(1, requestedSize)) : pageSize;
      const effOffset = page > 1 ? (page - 1) * effPageSize : offset;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (harvestId) { clauses.push(`hc.harvest_id = $${i++}`); vals.push(harvestId); }
      if (systemId) { clauses.push(`hc.system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId) { clauses.push(`hc.subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId) { clauses.push(`hc.feature_id = $${i++}`); vals.push(featureId); }
      if (completed === 'true' || completed === 'false') { clauses.push(`hc.completed = $${i++}`); vals.push(completed === 'true'); }
      if (status) { clauses.push(`hc.status = $${i++}`); vals.push(status); }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT hc.id, hc.harvest_id, hc.title, hc.intent_description, hc.status, hc.tags,
                  hc.implementation_notes, hc.code_snippets, hc.open_questions,
                  hc.system_id, hc.subsystem_id, hc.feature_id,
                  hc.work_request_id, hc.completed,
                  hc.valid_from, hc.valid_until, hc.created_at, hc.updated_at,
                  h.source_filename AS harvest_source
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}
           ORDER BY hc.created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, effPageSize, effOffset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM nebula.harvest_candidates hc
           LEFT JOIN nebula.harvests h ON h.id = hc.harvest_id
           ${where}`,
          vals
        ),
      ]);

      const items = dataResult.rows.map(camelCaseRow);
      const total = parseInt(countResult.rows[0].total, 10);
      res.json({ items, total, page, pageSize: effPageSize, limit: effPageSize, offset: effOffset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.get('/candidates/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.harvest_candidates WHERE id = $1', [id]
      );
      if (!row) return res.status(404).json({ error: 'Candidate not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/harvest-candidates/:id — update candidate (primarily for linking to hierarchy)
  // When systemId is set, auto-upserts the candidate's intent into a harvest_context info tab.
  router.patch('/harvest-candidates/:id', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id } = req.params;
      const { title, intentDescription, status, systemId, subsystemId, featureId, tags, planRef, workRequestId, completed,
              type, designRationale, provenanceBlockIndices, needsNewNode, proposedParent, proposedName, placementReason } = req.body;

      await client.query('BEGIN');

      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (title !== undefined) { sets.push(`title = $${i++}`); vals.push(title); }
      if (intentDescription !== undefined) { sets.push(`intent_description = $${i++}`); vals.push(intentDescription); }
      if (status !== undefined) { sets.push(`status = $${i++}`); vals.push(status); }
      if (systemId !== undefined) { sets.push(`system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId !== undefined) { sets.push(`subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId !== undefined) { sets.push(`feature_id = $${i++}`); vals.push(featureId); }
      if (tags !== undefined) { sets.push(`tags = $${i++}`); vals.push(tags); }
      if (workRequestId !== undefined) { sets.push(`work_request_id = $${i++}`); vals.push(workRequestId); }
      if (completed !== undefined) { sets.push(`completed = $${i++}`); vals.push(completed); }
      if (type !== undefined) { sets.push(`type = $${i++}`); vals.push(type); }
      if (designRationale !== undefined) { sets.push(`design_rationale = $${i++}`); vals.push(JSON.stringify(designRationale)); }
      if (provenanceBlockIndices !== undefined) { sets.push(`provenance_block_indices = $${i++}`); vals.push(JSON.stringify(provenanceBlockIndices)); }
      if (needsNewNode !== undefined) { sets.push(`needs_new_node = $${i++}`); vals.push(needsNewNode); }
      if (proposedParent !== undefined) { sets.push(`proposed_parent = $${i++}`); vals.push(proposedParent); }
      if (proposedName !== undefined) { sets.push(`proposed_name = $${i++}`); vals.push(proposedName); }
      if (placementReason !== undefined) { sets.push(`placement_reason = $${i++}`); vals.push(placementReason); }

      // planRef creates a cross-reference but doesn't update the candidate row;
      // still count it as a "change" to avoid the early no-op return.
      const planRefProvided = hasPlanRef(planRef);
      const hasChanges = sets.length > 0 || planRefProvided;
      if (!hasChanges) {
        await client.query('COMMIT');
        return res.json({ ok: true });
      }

      // Execute UPDATE (or fetch current row if planRef-only)
      let row: any;
      if (sets.length > 0) {
        vals.push(id);
        const result = await client.query(
          `UPDATE nebula.harvest_candidates SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
          vals
        );
        row = result.rows[0];
      } else {
        // planRef-only: fetch current row so cross-reference has context
        const result = await client.query(
          'SELECT * FROM nebula.harvest_candidates WHERE id = $1', [id]
        );
        row = result.rows[0];
      }
      if (!row) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'Harvest candidate not found' });
      }

      // Auto-upsert: when a candidate is explicitly linked to a system (systemId
      // provided in the request), synthesize its intent_description into a
      // 'harvest_context' info tab on that system. Does NOT fire on status/title-only
      // patches of already-linked candidates.
      const shouldUpsert = systemId !== undefined && row.system_id && row.intent_description;
      if (shouldUpsert) {
        await upsertHarvestContextTab(client, row.system_id, row);
      }

      // Plan integration: when a planRef is provided, create a cross-reference
      // linking this harvest_candidate to a conduit plan with rel_type='ag:spawns_plan'.
      await createSpawnsPlanCrossRef(client, row.id, planRef, {
        candidateTitle: row.title,
        harvestId: row.harvest_id,
        systemId: row.system_id,
        linkedAt: new Date().toISOString(),
      });

      await client.query('COMMIT');
      res.json(row);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/harvest-candidates/:id/spawn-plan — DEPRECATED alias (decision
  // 319defa5): candidates promote ONLY to requirements; verb renamed to
  // spawn-requirement. Fails loudly with a pointer so callers migrate.
  router.post('/harvest-candidates/:id/spawn-plan', async (_req: Request, res: Response) => {
    res.status(410).json({
      error: 'spawn-plan was renamed spawn-requirement (decision 319defa5: candidates promote only to requirements; plans come from requirements).',
      useInstead: 'POST /api/harvest-candidates/:id/spawn-requirement',
      mcpTool: 'nebula_spawn_requirement_from_candidate',
      rationale: 'No candidate-to-plan path exists. Request body semantics unchanged — re-point and retry.',
    });
  });

  // POST /api/harvest-candidates/:id/spawn-requirement — full flow: link candidate
  // to system, create a requirement derived from the candidate, and optionally
  // cross-reference a conduit plan — all in one atomic transaction.
  // (Renamed from spawn-plan per decision 319defa5.)
  router.post('/harvest-candidates/:id/spawn-requirement', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { id } = req.params;
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
      } = req.body;

      if (!systemId) return res.status(400).json({ error: 'systemId is required' });
      if (reqType && !(REQ_TYPES as readonly string[]).includes(reqType)) {
        return res.status(400).json({ error: `reqType must be one of: ${REQ_TYPES.join(', ')}` });
      }
      await client.query('BEGIN');

      // 1. Fetch the harvest candidate (must exist)
      const { rows: [candidate] } = await client.query(
        'SELECT * FROM nebula.harvest_candidates WHERE id = $1', [id]
      );
      if (!candidate) {
        await client.query('ROLLBACK');
        return res.status(404).json({ error: 'Harvest candidate not found' });
      }

      // 2. Link candidate to hierarchy
      const { rows: [updatedCandidate] } = await client.query(
        `UPDATE nebula.harvest_candidates
         SET system_id = $1, subsystem_id = $2, feature_id = $3
         WHERE id = $4 RETURNING *`,
        [systemId, subsystemId, featureId, id]
      );

      // 3. Auto-upsert harvest_context info tab (same pattern as PATCH)
      if (candidate.intent_description) {
        await upsertHarvestContextTab(client, systemId, candidate);
      }

      // 4. Create a requirement derived from the candidate, linked via candidate_id
      const reqTitle = title || candidate.title;
      const reqDescription = description || candidate.intent_description || '';
      const normalizedStatus = normalizeStatus(status);
      if (!normalizedStatus) {
        await client.query('ROLLBACK');
        return res.status(400).json({ error: `status, if provided, must be one of: ${Array.from(STATUS_CANONICAL).join(', ')}` });
      }
      const planRefStr = hasPlanRef(planRef) ? String(planRef).trim() : null;
      const { rows: [requirement] } = await client.query(
        `INSERT INTO requirements (system_id, subsystem_id, feature_id, title, description, status, priority, start_date, completion_date, parent_id, req_type, acceptance_criteria, candidate_id, conduit_plan_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) RETURNING *`,
        [systemId, subsystemId, featureId, reqTitle, reqDescription, normalizedStatus, priority, null, null, parentId, reqType, acceptanceCriteria ? JSON.stringify(acceptanceCriteria) : null, candidate.id, planRefStr]
      );

      // 5. Create cross-reference: candidate → plan (if planRef provided)
      const crossRef = await createSpawnsPlanCrossRef(client, candidate.id, planRef, {
        candidateTitle: candidate.title,
        harvestId: candidate.harvest_id,
        systemId,
        requirementId: requirement.id,
        linkedAt: new Date().toISOString(),
      });

      // Requirement → plan linkage is column-based (requirements.conduit_plan_id)
      // per T22 Step 5.4 ruling — no parallel req:spawns_plan edge.

      await client.query('COMMIT');

      res.status(201).json({
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
      });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/harvest-candidates — create a standalone candidate (e.g. manually linked).
  // When systemId is set, auto-upserts a harvest_context info tab on the target system.
  router.post('/harvest-candidates', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      const { harvestId, title, intentDescription, implementationNotes, codeSnippets, openQuestions, tags, status, systemId, subsystemId, featureId, planRef,
              type, designRationale, provenanceBlockIndices, needsNewNode, proposedParent, proposedName, placementReason } = req.body;
      if (!harvestId || !title) return res.status(400).json({ error: 'harvestId and title are required' });
      await client.query('BEGIN');

      const { rows: [row] } = await client.query(
        `INSERT INTO nebula.harvest_candidates (harvest_id, title, intent_description, implementation_notes, code_snippets, open_questions, tags, status, system_id, subsystem_id, feature_id,
                                               type, design_rationale, provenance_block_indices, needs_new_node, proposed_parent, proposed_name, placement_reason)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18) RETURNING *`,
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
      );

      // Auto-upsert: when created with a systemId and intent_description,
      // synthesize into a harvest_context info tab (same pattern as PATCH).
      if (row.system_id && row.intent_description) {
        await upsertHarvestContextTab(client, row.system_id, row);
      }

      // Plan integration: create cross-reference if planRef provided
      await createSpawnsPlanCrossRef(client, row.id, planRef, {
        candidateTitle: row.title,
        harvestId: row.harvest_id,
        systemId: row.system_id,
        linkedAt: new Date().toISOString(),
      });

      await client.query('COMMIT');
      res.status(201).json(row);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ════════════════════════════════════════════════════════════════
  // POST /api/specifications/:id/link-requirements — create cross-references
  // from specification to requirements by matching candidate_ids in the item_snapshot
  router.post('/specifications/:id/link-requirements', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      // Fetch the spec and its item_snapshot
      const { rows: [spec] } = await pool.query(
        'SELECT id, item_snapshot FROM nebula.specifications WHERE id = $1',
        [id]
      );
      if (!spec) return res.status(404).json({ error: 'Specification not found' });

      // Extract candidate IDs from items (harvest_candidate items: source_id IS the candidate ID)
      const directCandidateIds: string[] = [];
      const items = spec.item_snapshot || [];
      for (const item of items) {
        if (!item.source_id) continue;
        if (item.source_type === 'harvest_candidate') {
          directCandidateIds.push(item.source_id);
        }
      }

      // Deduplicate
      const candidateIds = [...new Set(directCandidateIds)];

      if (candidateIds.length === 0) {
        return res.status(200).json({ ok: true, linked: 0, message: 'No harvest_candidate items in snapshot' });
      }

      // Find requirements matching those candidate IDs
      const { rows: reqs } = await pool.query(
        'SELECT id, title FROM nebula.requirements WHERE candidate_id = ANY($1::uuid[])',
        [candidateIds]
      );

      // Create cross-references idempotently (in a transaction for atomicity)
      let linked = 0;
      const client = await pool.connect();
      try {
        await client.query('BEGIN');
        for (const req of reqs) {
          const { rowCount } = await client.query(
          `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
           SELECT 'specification', $1, 'requirement', $2, 'spec:defines_req', '{}'::jsonb
           WHERE NOT EXISTS (
             SELECT 1 FROM nebula.cross_references_history
             WHERE source_type = 'specification'
               AND source_id = $1
               AND target_type = 'requirement'
               AND target_id = $2
               AND rel_type = 'spec:defines_req'
               AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
           )
           ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
             WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
           DO NOTHING`,
          [id, req.id]
        );
        linked += rowCount ?? 0;
        }
        await client.query('COMMIT');
      } catch (txErr) {
        await client.query('ROLLBACK');
        throw txErr;
      } finally {
        client.release();
      }

      res.json({ ok: true, linked, candidate_count: candidateIds.length, requirement_count: reqs.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  //  SPECS (flattened agenda_items WHERE included=true — distinct from /api/specifications
  //  which returns revision snapshots from nebula.active_specifications)
  // ════════════════════════════════════════════════════════════════

  // GET /api/specs — paginated list of spec items (flattened agenda_items)
  router.get('/specs', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT
            id, agenda_id, source_type, source_id, title, body,
            decisions, open_questions, supporting_refs, included,
            planner_note, item_created_at, item_updated_at,
            agenda_title, agenda_status
          FROM nebula.specs
          ORDER BY item_created_at DESC
          LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.specs'),
      ]);

      const items = dataResult.rows.map(row => ({
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
      }));

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/specs/:id — single spec item
  router.get('/specs/:id', async (req: Request, res: Response) => {
    try {
      const id = req.params.id as string;
      if (!isUuid(id)) {
        return res.status(400).json({ error: 'id must be a UUID' });
      }

      const { rows } = await pool.query(
        `SELECT
          id, agenda_id, source_type, source_id, title, body,
          decisions, open_questions, supporting_refs, included,
          planner_note, item_created_at, item_updated_at,
          agenda_title, agenda_status
        FROM nebula.specs
        WHERE id = $1`,
        [id]
      );

      if (rows.length === 0) {
        return res.status(404).json({ error: 'Spec item not found' });
      }

      const row = rows[0];
      res.json({
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
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  //  HARVEST CANDIDATE DISCOVERY — semantic search against project hierarchy
  // ════════════════════════════════════════════════════════════════

  // POST /api/harvest-candidates/discover — match unlinked candidates to systems/subsystems/features
  // via semantic search, flagging undocumented projects below confidence threshold.
  router.post('/harvest-candidates/discover', async (req: Request, res: Response) => {
    try {
      const {
        candidateIds,
        limit = 50,
        threshold = 0.75,
      } = req.body;

      const candidateLimit = Math.min(parseInt(String(limit)) || 50, 200);
      const rawThreshold = parseFloat(String(threshold));
      const matchThreshold = !isNaN(rawThreshold) && rawThreshold >= 0 && rawThreshold <= 1 ? rawThreshold : 0.75;

      // Validate threshold range at the REST level (zod validates at MCP level)
      if (isNaN(rawThreshold) || rawThreshold < 0 || rawThreshold > 1) {
        return res.status(400).json({ error: 'threshold must be a number between 0 and 1' });
      }

      // 1. Query unlinked harvest candidates (respect temporal validity)
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
      `;
      const candidateParams: any[] = [];

      if (candidateIds && Array.isArray(candidateIds) && candidateIds.length > 0) {
        candidateQuery += ` AND hc.id = ANY($1::uuid[])`;
        candidateParams.push(candidateIds);
      }

      candidateQuery += ` ORDER BY hc.created_at DESC LIMIT $${candidateParams.length + 1}`;
      candidateParams.push(candidateLimit);

      const { rows: candidates } = await pool.query(candidateQuery, candidateParams);

      if (candidates.length === 0) {
        return res.json({
          candidateCount: 0,
          matchThreshold,
          matches: [],
          undocumented: [],
        });
      }

      // 2. Fetch full hierarchy (systems, subsystems, features) for name-based matching
      const [systemsRes, subsystemsRes, featuresRes] = await Promise.all([
        pool.query('SELECT id, name, description FROM systems ORDER BY name'),
        pool.query('SELECT s.id, s.name, s.description, s.system_id, sys.name AS system_name FROM subsystems s LEFT JOIN systems sys ON sys.id = s.system_id ORDER BY s.name'),
        pool.query('SELECT f.id, f.name, f.description, f.subsystem_id, sub.name AS subsystem_name, sub.system_id FROM features f LEFT JOIN subsystems sub ON sub.id = f.subsystem_id ORDER BY f.name'),
      ]);

      const allSystems = systemsRes.rows;
      const allSubsystems = subsystemsRes.rows;
      const allFeatures = featuresRes.rows;

      // 3. For each candidate, run semantic search (parallelized) and evaluate confidence
      const scriptPath = '/home/codex/dev/nexus/bin/unified_semantic_search.py';
      const pythonBin = '/home/codex/dev/nexus/python/rover/.venv/bin/python3';

      interface MatchResult {
        candidateId: string;
        candidateTitle: string;
        candidateIntent: string | null;
        harvestSource: string | null;
        curatedMatches: Array<{
          entityId: string;
          entityName: string;
          section: string;
          entityType: string;
          description: string;
          similarity: number;
        }>;
        hierarchyMatches: Array<{
          type: 'system' | 'subsystem' | 'feature';
          id: string;
          name: string;
          description: string;
          parentInfo?: string;
        }>;
        topSimilarity: number;
        searchFailed?: boolean;
      }

      // Helper: build search query and run text matching for one candidate
      const searchPromises = candidates.map(async (cand): Promise<MatchResult> => {
        const searchQuery = [cand.title, cand.intent_description]
          .filter(Boolean)
          .join(' ')
          .slice(0, 500);

        if (!searchQuery) {
          return {
            candidateId: cand.id,
            candidateTitle: cand.title,
            candidateIntent: cand.intent_description,
            harvestSource: cand.harvest_source,
            curatedMatches: [],
            hierarchyMatches: [],
            topSimilarity: 0,
          };
        }

        let curatedMatches: MatchResult['curatedMatches'] = [];
        let searchFailed = false;
        try {
          const { stdout } = await execFileAsync(
            pythonBin,
            [scriptPath, searchQuery, '--limit', '15', '--layers', 'harvest,kg', '--json'],
            { timeout: 60000, maxBuffer: 10 * 1024 * 1024 }
          );

          const parsed = JSON.parse(stdout);
          const results: any[] = parsed.results || [];

          curatedMatches = results
            .filter((r: any) => r.provenance === 'curated')
            .map((r: any) => ({
              entityId: r.id,
              entityName: r.title,
              section: r.section || '',
              entityType: r.entity_type || '',
              description: (r.description || '').slice(0, 300),
              similarity: r.similarity,
            }));
        } catch (searchErr: any) {
          console.error(`[discover] Semantic search failed for candidate ${cand.id}:`, searchErr.message);
          searchFailed = true;
        }

        // Simple text matching against hierarchy (token-based, ILIKE equivalent)
        const hierarchyMatches: MatchResult['hierarchyMatches'] = [];
        const searchTokens = searchQuery.toLowerCase().split(/\s+/).filter(t => t.length > 2);

        for (const sys of allSystems) {
          const sysName = (sys.name || '').toLowerCase();
          const sysDesc = (sys.description || '').toLowerCase();
          const tokenHits = searchTokens.filter(t => sysName.includes(t) || sysDesc.includes(t)).length;
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'system',
              id: sys.id,
              name: sys.name,
              description: (sys.description || '').slice(0, 200),
              parentInfo: undefined,
            });
          }
        }

        for (const sub of allSubsystems) {
          const subName = (sub.name || '').toLowerCase();
          const subDesc = (sub.description || '').toLowerCase();
          const tokenHits = searchTokens.filter(t => subName.includes(t) || subDesc.includes(t)).length;
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'subsystem',
              id: sub.id,
              name: sub.name,
              description: (sub.description || '').slice(0, 200),
              parentInfo: `System: ${sub.system_name || 'unknown'}`,
            });
          }
        }

        for (const feat of allFeatures) {
          const featName = (feat.name || '').toLowerCase();
          const featDesc = (feat.description || '').toLowerCase();
          const tokenHits = searchTokens.filter(t => featName.includes(t) || featDesc.includes(t)).length;
          if (tokenHits > 0) {
            hierarchyMatches.push({
              type: 'feature',
              id: feat.id,
              name: feat.name,
              description: (feat.description || '').slice(0, 200),
              parentInfo: `Subsystem: ${feat.subsystem_name || 'unknown'}`,
            });
          }
        }

        const topCuratedSimilarity = curatedMatches.length > 0
          ? Math.max(...curatedMatches.map(m => m.similarity))
          : 0;

        const result: MatchResult = {
          candidateId: cand.id,
          candidateTitle: cand.title,
          candidateIntent: cand.intent_description,
          harvestSource: cand.harvest_source,
          curatedMatches: curatedMatches.slice(0, 5),
          hierarchyMatches: hierarchyMatches.slice(0, 5),
          topSimilarity: topCuratedSimilarity,
        };
        if (searchFailed) result.searchFailed = true;
        return result;
      });

      // Run searches with concurrency limiting (max 8 parallel Python subprocesses
      // to avoid overwhelming Ollama and system resources)
      const MAX_CONCURRENT = 8;
      const allResults: MatchResult[] = [];
      for (let i = 0; i < searchPromises.length; i += MAX_CONCURRENT) {
        const batch = searchPromises.slice(i, i + MAX_CONCURRENT);
        const batchResults = await Promise.all(batch);
        allResults.push(...batchResults);
      }

      // Classify by threshold
      const matched: MatchResult[] = [];
      const undocumented: MatchResult[] = [];
      for (const result of allResults) {
        if (result.topSimilarity >= matchThreshold) {
          matched.push(result);
        } else {
          undocumented.push(result);
        }
      }

      res.json({
        candidateCount: candidates.length,
        matchThreshold,
        matches: matched,
        undocumented,
      });
    } catch (err: any) {
      console.error('[discover] Error:', err);
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  AGENT RECORDS — database-first audit trail
  // ════════════════════════════════════════════════════════════════

  // GET /api/agent-records — list records with optional filters and pagination
  // GET /api/agent-records/:id — full single record INCLUDING content.
  // Fixes finding 6d731551: REST had no content-bearing read (list omits
  // content; ?id= was fuzzy search), so REST-only consumers misread
  // persisted records as empty.
  router.get('/agent-records/:id', async (req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT id, record_type, role, model, title, source_path, tags, content,
                system_id, subsystem_id, feature_id, plan_ref, candidate_id,
                requirement_id, created_at, recorded_on_dt, level, visibility_scope
         FROM nebula.agent_records WHERE id = $1`,
        [req.params.id]
      );
      if (!rows.length) return res.status(404).json({ error: 'Agent record not found' });
      res.json(rows[0]);
    } catch (err: any) {
      res.status(400).json({ error: err.message });
    }
  });

  router.get('/agent-records', async (req: Request, res: Response) => {
    try {
      const { type, role, systemId, subsystemId, featureId, planRef, tag, search, createdAfter, createdBefore, level, visibilityScope } = req.query;
      // Pagination: support both REST convention (page/pageSize) and the
      // MCP client convention (limit/offset, used by nebula-mcp tools).

      const page = Math.max(1, parseInt(String(req.query.page || '1'), 10));
      const limitParam = parseInt(String(req.query.limit ?? ''), 10);
      const offsetParam = parseInt(String(req.query.offset ?? ''), 10);
      const pageSize = Number.isFinite(limitParam)
        ? Math.min(500, Math.max(1, limitParam))
        : Math.min(100, Math.max(1, parseInt(String(req.query.pageSize || '100'), 10)));
      const offset = Number.isFinite(offsetParam)
        ? Math.max(0, offsetParam)
        : (page - 1) * pageSize;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;

      // Exact id filter (finding 6d731551): ?id=<uuid> previously behaved
      // as fuzzy search and returned unrelated rows. Exact match wins;
      // includeContent defaults on for single-record lookups.
      if (req.query.id) {
        clauses.push(`id = $${i++}`);
        vals.push(req.query.id);
        if (req.query.includeContent === undefined) {
          (req.query as any).includeContent = 'true';
        }
      }

      if (type) { clauses.push(`record_type = $${i++}`); vals.push(type); }
      if (role) { clauses.push(`role = $${i++}`); vals.push(role); }
      if (systemId) { clauses.push(`system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId) { clauses.push(`subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId) { clauses.push(`feature_id = $${i++}`); vals.push(featureId); }
      if (planRef) { clauses.push(`plan_ref = $${i++}`); vals.push(planRef); }
      // Multi-tag support: ?tag=val, ?tag=a,b (comma-separated), or ?tag=a&tag=b (AND conjunction)
      if (tag) {
        const raw = Array.isArray(tag) ? tag as string[] : [tag as string];
        // Flatten and split any comma-separated values
        const tagArr: string[] = [];
        for (const item of raw) {
          for (const part of item.split(',')) {
            const trimmed = part.trim();
            if (trimmed) tagArr.push(trimmed);
          }
        }
        if (tagArr.length === 1) {
          clauses.push(`$${i} = ANY(tags)`);
          vals.push(tagArr[0]);
          i++;
        } else if (tagArr.length > 1) {
          clauses.push(`tags @> $${i}::text[]`);
          vals.push(tagArr);
          i++;
        }
      }
      if (search) {
        clauses.push(`(title ILIKE $${i} OR content ILIKE $${i})`);
        vals.push(`%${search}%`);
        i++;
      }
      if (createdAfter) {
        clauses.push(`created_at >= $${i++}`);
        vals.push(createdAfter);
      }
      if (createdBefore) {
        clauses.push(`created_at <= $${i++}`);
        vals.push(createdBefore);
      }
      if (level) { clauses.push(`level = $${i++}`); vals.push(parseInt(level as string)); }
      if (visibilityScope) { clauses.push(`visibility_scope = $${i++}`); vals.push(visibilityScope); }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      // content is excluded from the default list projection to keep list
      // payloads small (MCP inbox checks, etc.). ?includeContent=true opts in
      // for UIs that render record bodies in the list (agent-records/reports).
      const listColumns = `id, record_type, role, model, title, source_path, tags,
           system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt,
           level, visibility_scope`;
      const columns = req.query.includeContent === 'true'
        ? `${listColumns}, content`
        : listColumns;

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT ${columns}
           FROM nebula.agent_records ${where}
           ORDER BY created_at DESC, id DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.agent_records ${where}`,
          vals
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/agent-records/:id — full record with content
  router.get('/agent-records/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.agent_records WHERE id = $1', [id]
      );
      if (!row) return res.status(404).json({ error: 'Agent record not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/agent-records/search — multi-tag AND/OR agent record search
  router.post('/agent-records/search', async (req: Request, res: Response) => {
    try {
      const { tags, recordType, role, level, visibilityScope, match = 'all', limit: qLimit, offset: qOffset } = req.body;
      const maxLimit = Math.min(parseInt(qLimit as string) || 100, 500);
      const offset = parseInt(qOffset as string) || 0;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;

      if (recordType) { clauses.push(`record_type = $${i++}`); vals.push(recordType); }
      if (role) { clauses.push(`role = $${i++}`); vals.push(role); }
      if (level !== undefined && level !== null) {
        const levelNum = parseInt(level);
        if (levelNum >= 1 && levelNum <= 4) {
          clauses.push(`level = $${i++}`); vals.push(levelNum);
        }
      }
      if (visibilityScope) { clauses.push(`visibility_scope = $${i++}`); vals.push(visibilityScope); }

      // Multi-tag search with match mode
      if (tags && Array.isArray(tags) && tags.length > 0) {
        const cleanTags = tags.filter((t: any) => typeof t === 'string' && t.trim()).map((t: string) => t.trim());
        if (cleanTags.length > 0) {
          if (match === 'any') {
            // OR semantics: any of the tags match
            clauses.push(`tags && $${i}::text[]`);
            vals.push(cleanTags);
            i++;
          } else {
            // AND semantics (default): all tags must match (same as GET handler)
            if (cleanTags.length === 1) {
              clauses.push(`$${i} = ANY(tags)`);
              vals.push(cleanTags[0]);
              i++;
            } else {
              clauses.push(`tags @> $${i}::text[]`);
              vals.push(cleanTags);
              i++;
            }
          }
        }
      }

      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const { rows } = await pool.query(
        `SELECT id, record_type, role, model, title, source_path, tags, system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt, level, visibility_scope
         FROM nebula.agent_records ${where}
         ORDER BY created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
        [...vals, maxLimit, offset]
      );

      // Also fetch total count for the same query (without pagination)
      const countWhere = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';
      const { rows: [{ count }] } = await pool.query(
        `SELECT COUNT(*)::int AS count FROM nebula.agent_records ${countWhere}`,
        vals
      );

      res.json({ records: rows, count: parseInt(count), limit: maxLimit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Assembly decisions-forum mirror (T27) ─────────────────────────
  // On-create hook: when a `decision` record is written, deterministically
  // mirror it into the Assembly `decisions` forum (admin-facing projection).
  // Fire-and-forget: a mirror failure never fails the record write.
  const ASSEMBLY_URL = process.env.ASSEMBLY_URL || 'http://localhost:3107';
  const DECISIONS_FORUM_ID = '703bc0f9-faf4-4c94-a52d-8f0d4024a89b';

  let assemblyUserMapCache: Record<string, string> | null = null;
  async function getAssemblyUserMap(): Promise<Record<string, string>> {
    if (assemblyUserMapCache) return assemblyUserMapCache;
    const resp = await fetch(`${ASSEMBLY_URL}/api/users`);
    if (!resp.ok) throw new Error(`assembly /api/users -> HTTP ${resp.status}`);
    const users = (await resp.json()) as any[];
    const map: Record<string, string> = {};
    for (const u of users) {
      if (u && u.name) map[String(u.name).toLowerCase()] = u.id;
    }
    assemblyUserMapCache = map;
    return map;
  }

  async function mirrorDecisionToForum(record: any): Promise<void> {
    try {
      const users = await getAssemblyUserMap();
      const userId = users[String(record.role || '').toLowerCase()];
      if (!userId) {
        console.warn(`[decisions-mirror] no assembly user for role '${record.role}' — skipping ${record.id}`);
        return;
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
      });
      if (!resp.ok) {
        console.warn(`[decisions-mirror] forum post HTTP ${resp.status} for ${record.id}`);
      }
    } catch (err: any) {
      console.warn(`[decisions-mirror] error for ${record.id}: ${err?.message || err}`);
    }
  }

  // POST /api/agent-records — create a new agent record (canonical write path)
  router.post('/agent-records', async (req: Request, res: Response) => {
    try {
      const { recordType, role, title, content, sourcePath, metadata, tags, systemId, subsystemId, featureId, planRef, level, visibilityScope, model } = req.body;

      const validTypes = ['report', 'analysis', 'assessment', 'inspection', 'prompt', 'response', 'engineering_log', 'architecture_note', 'decision'];
      if (!recordType || !validTypes.includes(recordType)) {
        return res.status(400).json({ error: `recordType must be one of: ${validTypes.join(', ')}` });
      }

      if (level !== undefined && (level < 1 || level > 4)) {
        return res.status(400).json({ error: 'level must be between 1 and 4' });
      }

      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.agent_records (record_type, role, title, content, source_path, metadata, tags, system_id, subsystem_id, feature_id, plan_ref, level, visibility_scope, model)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) RETURNING *`,
        [
          recordType, role || '', title || '', content || '',
          sourcePath || null, metadata || {}, tags || [],
          systemId || null, subsystemId || null, featureId || null, planRef || null,
          level ?? 1, visibilityScope || 'all', model || null,
        ]
      );
      res.status(201).json(row);

      // T27: deterministically mirror decision records into the decisions forum.
      if (recordType === 'decision') {
        mirrorDecisionToForum(row).catch((e: any) => console.warn('[decisions-mirror]', e?.message || e));
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/agent-records/:id — update record fields
  router.patch('/agent-records/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { title, content, metadata, tags, systemId, subsystemId, featureId, planRef, level, visibilityScope, model } = req.body;
      if (level !== undefined && (level < 1 || level > 4)) {
        return res.status(400).json({ error: 'level must be between 1 and 4' });
      }
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (title !== undefined) { sets.push(`title = $${i++}`); vals.push(title); }
      if (content !== undefined) { sets.push(`content = $${i++}`); vals.push(content); }
      if (metadata !== undefined) { sets.push(`metadata = $${i++}`); vals.push(metadata); }
      if (tags !== undefined) { sets.push(`tags = $${i++}`); vals.push(tags); }
      if (systemId !== undefined) { sets.push(`system_id = $${i++}`); vals.push(systemId); }
      if (subsystemId !== undefined) { sets.push(`subsystem_id = $${i++}`); vals.push(subsystemId); }
      if (featureId !== undefined) { sets.push(`feature_id = $${i++}`); vals.push(featureId); }
      if (planRef !== undefined) { sets.push(`plan_ref = $${i++}`); vals.push(planRef); }
      if (level !== undefined) { sets.push(`level = $${i++}`); vals.push(level); }
      if (visibilityScope !== undefined) { sets.push(`visibility_scope = $${i++}`); vals.push(visibilityScope); }
      if (model !== undefined) { sets.push(`model = $${i++}`); vals.push(model); }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [row] } = await pool.query(
        `UPDATE nebula.agent_records SET ${sets.join(', ')} WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!row) return res.status(404).json({ error: 'Agent record not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/agent-records/:id
  router.delete('/agent-records/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query('UPDATE nebula.agent_records SET valid_until = now() WHERE id = $1 AND valid_until > now()', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Agent record not found' });
      res.json({ expired: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  INBOX POINTERS — per-role watermark for unread messages
  // ════════════════════════════════════════════════════════════════

  // GET /api/inbox-pointer/:role — get the inbox pointer for a role
  router.get('/inbox-pointer/:role', async (req: Request, res: Response) => {
    try {
      const role = req.params.role as string;
      const { getInboxPointer } = await import('./services/block-segmentation-redis.service');
      const pointer = await getInboxPointer(role);
      res.json({ role, pointer });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/inbox-pointer/:role — set the inbox pointer for a role
  router.put('/inbox-pointer/:role', async (req: Request, res: Response) => {
    try {
      const role = req.params.role as string;
      const { timestamp } = req.body;
      if (!timestamp) return res.status(400).json({ error: 'timestamp is required' });
      const { setInboxPointer } = await import('./services/block-segmentation-redis.service');
      await setInboxPointer(role, timestamp);
      res.json({ ok: true, role, pointer: timestamp });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/inbox-pointers — list all inbox pointers (debugging)
  router.get('/inbox-pointers', async (_req: Request, res: Response) => {
    try {
      const { getAllInboxPointers } = await import('./services/block-segmentation-redis.service');
      const pointers = await getAllInboxPointers();
      res.json({ pointers });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  PROJECTIONS — on-demand markdown folder generation
  // ════════════════════════════════════════════════════════════════

  // GET /api/projections — list all projection configs
  router.get('/projections', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          'SELECT id, name, type, description, target_path, model, schedule, created_at, recorded_on_dt FROM nebula.projections ORDER BY name LIMIT $1 OFFSET $2',
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.projections'),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/projections — create a projection config
  router.post('/projections', async (req: Request, res: Response) => {
    try {
      const { name, type, description, sourceQuery, template, targetPath, model, schedule, metadata } = req.body;
      if (!name || !type) return res.status(400).json({ error: 'name and type are required' });
      if (!['deterministic', 'inference'].includes(type)) return res.status(400).json({ error: 'type must be deterministic or inference' });
      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.projections (name, type, description, source_query, template, target_path, model, schedule, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *`,
        [name, type, description || '', sourceQuery || '', template || '', targetPath || '', model || '', schedule || '', metadata || {}]
      );
      res.status(201).json(row);
    } catch (err: any) {
      if (err.code === '23505') return res.status(409).json({ error: `Projection '${req.body.name}' already exists` });
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/projections/:id/render — execute a projection and write output files
  router.post('/projections/:id/render', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [proj] } = await pool.query('SELECT * FROM nebula.projections WHERE id = $1', [id]);
      if (!proj) return res.status(404).json({ error: 'Projection not found' });

      if (proj.type === 'deterministic') {
        // Execute the source SQL, then render each row through the template
        const { rows: data } = await pool.query(proj.source_query);
        const rendered: { path: string; content: string }[] = [];

        for (const row of data) {
          let content = proj.template;
          // Replace all {{key}} placeholders with values from the row
          for (const [key, value] of Object.entries(row)) {
            const val = value === null ? '' : String(value);
            // SECURITY: escape regex metacharacters in the key to prevent
            // regex injection via user-controlled source_query column names.
            const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            // SECURITY: escape $ in the replacement value to prevent
            // replacement-string injection ($&, $`, $', $n patterns in
            // String.replace() are interpreted specially).
            const safeVal = val.replace(/\$/g, '$$$$');
            content = content.replace(new RegExp(`\\{\\{${escapedKey}\\}\\}`, 'g'), safeVal);
          }
          // Substitute every {{key}} in the target path (id, name, slug, …) —
          // same escaping discipline as the content template.
          let targetPath = proj.target_path;
          for (const [key, value] of Object.entries(row)) {
            const val = value === null ? '' : String(value);
            const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const safeVal = val.replace(/\$/g, '$$$$');
            targetPath = targetPath.replace(new RegExp(`\\{\\{${escapedKey}\\}\}`, 'g'), safeVal);
          }

          const absPath = path.resolve(AUDIT_ROOT, targetPath);
          if (!absPath.startsWith(AUDIT_ROOT)) {
            return res.status(403).json({ error: `Target path traversal denied: ${targetPath}` });
          }
          const dir = path.dirname(absPath);
          fs.mkdirSync(dir, { recursive: true });
          fs.writeFileSync(absPath, content, 'utf-8');
          rendered.push({ path: targetPath, content: content.slice(0, 200) + '...' });
        }

        res.json({ ok: true, type: 'deterministic', rendered: rendered.length, files: rendered });
      } else {
        // Inference mode — placeholder; will invoke LLM in future version
        res.json({ ok: true, type: 'inference', note: 'Inference projection not yet implemented' });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/projections/:id
  router.delete('/projections/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query('UPDATE nebula.projections SET valid_until = now() WHERE id = $1 AND valid_until > now()', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Projection not found' });
      res.json({ expired: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  //  CROSS-REFERENCES
  // ═══════════════════════════════════════════════════════════════════

  // POST /api/cross-references
  router.post('/cross-references', async (req: Request, res: Response) => {
    try {
      const { sourceType, sourceId, targetType, targetId, relType, metadata } = req.body;
      if (!sourceType || !sourceId || !targetType || !targetId || !relType) {
        return res.status(400).json({ error: 'sourceType, sourceId, targetType, targetId, and relType are required' });
      }

      // Validate against cross-reference taxonomy
      const { isValidCrossReferenceType, validateCrossRefConstraint } = await import('./crossref-taxonomy');
      if (!isValidCrossReferenceType(relType)) {
        const allowed = (await import('./crossref-taxonomy')).ALL_CROSSREF_TYPES.join(', ');
        return res.status(400).json({
          error: `Invalid rel_type "${relType}". Allowed values: ${allowed}`,
        });
      }

      const constraint = validateCrossRefConstraint(relType, sourceType, targetType);
      if (!constraint.valid) {
        return res.status(400).json({ error: constraint.error });
      }

      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.cross_references_history (source_type, source_id, target_type, target_id, rel_type, metadata)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
           WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
         DO NOTHING
         RETURNING *`,
        [sourceType, sourceId, targetType, targetId, relType, JSON.stringify(metadata || {})]
      );
      if (!row) return res.status(409).json({ error: 'Cross-reference already exists' });
      res.status(201).json(toEpochMs(row, 'created_at'));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/cross-references
  router.get('/cross-references', async (req: Request, res: Response) => {
    try {
      const { sourceType, sourceId, targetType, targetId, relType } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (sourceType) { clauses.push(`source_type = $${i++}`); vals.push(sourceType); }
      if (sourceId) { clauses.push(`source_id = $${i++}`); vals.push(sourceId); }
      if (targetType) { clauses.push(`target_type = $${i++}`); vals.push(targetType); }
      if (targetId) { clauses.push(`target_id = $${i++}`); vals.push(targetId); }
      if (relType) { clauses.push(`rel_type = $${i++}`); vals.push(relType); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM nebula.cross_references ${where} ORDER BY created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.cross_references ${where}`,
          vals
        ),
      ]);

      // Hydrate camelCase relation fields at parity with what MCP consumers
      // expect — the raw rows are snake_case, which left REST consumers
      // reading sourceType/relType/targetType as null. snake_case keys are
      // kept for backward compatibility with existing callers.
      res.json({
        items: dataResult.rows.map((r: any) => ({
          ...toEpochMs(r, 'created_at'),
          sourceType: r.source_type,
          sourceId: r.source_id,
          targetType: r.target_type,
          targetId: r.target_id,
          relType: r.rel_type,
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/cross-references/:id
  router.get('/cross-references/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query('SELECT * FROM nebula.cross_references WHERE id = $1', [id]);
      if (!row) return res.status(404).json({ error: 'Cross-reference not found' });
      res.json({
        ...toEpochMs(row, 'created_at'),
        sourceType: row.source_type,
        sourceId: row.source_id,
        targetType: row.target_type,
        targetId: row.target_id,
        relType: row.rel_type,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/cross-references/:id — soft-delete (expire)
  router.delete('/cross-references/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query(
        'UPDATE nebula.cross_references SET valid_until = now() WHERE id = $1 AND valid_until > now()',
        [id]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Cross-reference not found' });
      res.json({ expired: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  // ═══════════════════════════════════════════════════════════════════
  //  EVIDENCE LINKS — typed harvest→knowledge bridge
  // ═══════════════════════════════════════════════════════════════════

  // POST /api/evidence-links
  router.post('/evidence-links', async (req: Request, res: Response) => {
    try {
      const {
        knowledgeEntityId, nebulaHarvestId, nebulaCandidateId,
        linkType, confidence, provenance, rationale, sourceSpan, metadata,
      } = req.body;

      if (!knowledgeEntityId || !linkType) {
        return res.status(400).json({
          error: 'knowledgeEntityId and linkType are required',
        });
      }

      if (!nebulaHarvestId && !nebulaCandidateId) {
        return res.status(400).json({
          error: 'At least one of nebulaHarvestId or nebulaCandidateId is required',
        });
      }

      // Validate link type against taxonomy
      const { isValidEvidenceLinkType } = await import('./evidence-link-types');
      if (!isValidEvidenceLinkType(linkType)) {
        const allowed = (await import('./evidence-link-types')).ALL_EVIDENCE_LINK_TYPES.join(', ');
        return res.status(400).json({
          error: `Invalid linkType "${linkType}". Allowed values: ${allowed}`,
        });
      }

      // Validate provenance if provided
      if (provenance) {
        const { isValidProvenance } = await import('./evidence-link-types');
        if (!isValidProvenance(provenance)) {
          const allowed = (await import('./evidence-link-types')).EVIDENCE_PROVENANCE_VALUES.join(', ');
          return res.status(400).json({
            error: `Invalid provenance "${provenance}". Allowed values: ${allowed}`,
          });
        }
      }

      const { rows: [row] } = await pool.query(
        `INSERT INTO knowledge.evidence_links
           (knowledge_entity_id, nebula_harvest_id, nebula_candidate_id,
            link_type, confidence, provenance, rationale, source_span, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
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
      );
      res.status(201).json(toEpochMs(row, 'created_at'));
    } catch (err: any) {
      if (err.code === '23505') {
        return res.status(409).json({
          error: 'Duplicate evidence link — this entity+source+type combination already exists',
        });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/evidence-links
  router.get('/evidence-links', async (req: Request, res: Response) => {
    try {
      const {
        knowledgeEntityId, nebulaHarvestId, nebulaCandidateId,
        linkType, provenance, minConfidence, maxConfidence,
      } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;

      if (knowledgeEntityId) { clauses.push(`knowledge_entity_id = $${i++}`); vals.push(knowledgeEntityId); }
      if (nebulaHarvestId) { clauses.push(`nebula_harvest_id = $${i++}`); vals.push(nebulaHarvestId); }
      if (nebulaCandidateId) { clauses.push(`nebula_candidate_id = $${i++}`); vals.push(nebulaCandidateId); }
      if (linkType) { clauses.push(`link_type = $${i++}`); vals.push(linkType); }
      if (provenance) { clauses.push(`provenance = $${i++}`); vals.push(provenance); }
      if (minConfidence) { clauses.push(`confidence >= $${i++}`); vals.push(parseFloat(minConfidence as string)); }
      if (maxConfidence) { clauses.push(`confidence <= $${i++}`); vals.push(parseFloat(maxConfidence as string)); }

      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM knowledge.evidence_links ${where} ORDER BY created_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM knowledge.evidence_links ${where}`,
          vals
        ),
      ]);

      res.json({
        items: dataResult.rows.map((r: any) => toEpochMs(r, 'created_at')),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/evidence-links/:id
  router.get('/evidence-links/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM knowledge.evidence_links WHERE id = $1', [id]
      );
      if (!row) return res.status(404).json({ error: 'Evidence link not found' });
      res.json(toEpochMs(row, 'created_at'));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/evidence-links/:id
  router.delete('/evidence-links/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query(
        'DELETE FROM knowledge.evidence_links WHERE id = $1', [id]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Evidence link not found' });
      res.status(204).send();
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/evidence-links?knowledgeEntityId=... — bulk delete all links for an entity
  router.delete('/evidence-links', async (req: Request, res: Response) => {
    try {
      const { knowledgeEntityId } = req.query;
      if (!knowledgeEntityId) {
        return res.status(400).json({ error: 'knowledgeEntityId query parameter is required for bulk delete' });
      }
      const { rowCount } = await pool.query(
        'DELETE FROM knowledge.evidence_links WHERE knowledge_entity_id = $1',
        [knowledgeEntityId]
      );
      res.json({ deleted: rowCount });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });


  // ════════════════════════════════════════════════════════════════
  //  BLOCK SEGMENTATION — interactive block-level transcript editing
  // ════════════════════════════════════════════════════════════════

  // GET /api/conversations/by-snapshot/:snapshotId — single conversation
  // snapshot by id. Distinct from `:id/snapshots` (which lists all snapshots
  // for a *conversation_id*); this returns the single snapshot whose `id`
  // equals the supplied UUID — the exact semantics assembly-srv's former
  // `GET /api/conversations/:id` provided (where `:id` was the snapshot id).
  // Returns the snapshot row enriched with `source_filename` from the harvests
  // join (same column set as `GET /api/conversations`) so list→detail flows
  // see identical shapes.
  router.get('/conversations/by-snapshot/:snapshotId', async (req: Request, res: Response) => {
    try {
      const snapshotId = req.params.snapshotId as string;
      if (!isUuid(snapshotId)) {
        return res.status(400).json({ error: 'snapshotId must be a UUID' });
      }
      const { rows } = await pool.query(
        `SELECT cs.id, cs.conversation_id, cs.snapshot_index, cs.source_hash,
                cs.capture_mode, cs.block_count, cs.created_by, cs.created_at,
                h.source_filename
         FROM nebula.conversation_snapshots cs
         LEFT JOIN nebula.harvests h ON h.id = cs.conversation_id
         WHERE cs.id = $1`,
        [snapshotId]
      );
      if (rows.length === 0) {
        return res.status(404).json({ error: 'Snapshot not found' });
      }
      res.json(camelCaseRow(rows[0]));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conversations — paginated list of conversation snapshots
  router.get('/conversations', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT cs.id, cs.conversation_id, cs.snapshot_index, cs.source_hash,
                  cs.capture_mode, cs.block_count, cs.created_by, cs.created_at,
                  h.source_filename
           FROM nebula.conversation_snapshots cs
           LEFT JOIN nebula.harvests h ON h.id = cs.conversation_id
           ORDER BY cs.created_at DESC
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.conversation_snapshots'),
      ]);

      res.json({
        items: dataResult.rows.map((r: any) => camelCaseRow(r)),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conversations/:id/snapshots — list all snapshots for a conversation
  router.get('/conversations/:id/snapshots', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const result = await bs.listSnapshots(pool, id as string);
      res.json(result);

      // Warm session cache on read
      try {
        await bsRedis.cacheSession(id as string, {
          conversationId: id as string,
          activeSnapshotId: result.snapshots[0]?.id || null,
          mode: 'view',
          userId: 'unknown',
        });
      } catch (_) { /* Redis unavailable — non-fatal */ }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/snapshots/:id/blocks — list blocks with optional diff from a previous snapshot
  router.get('/snapshots/:id/blocks', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const dq = req.query.diffFrom;
      const diffFrom = typeof dq === 'string' ? dq : undefined;
      const result = await bs.listBlocks(pool, id as string, diffFrom);
      res.json(result);

      // Cache blocks for next read (non-diff queries only)
      if (!diffFrom) {
        try { await bsRedis.cacheBlocks(id as string, result.blocks); }
        catch (_) { /* non-fatal */ }

      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conversations/:id/blocks — get blocks for the latest snapshot of a conversation
  router.get('/conversations/:id/blocks', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      // Find the latest snapshot for this conversation
      const snapResult = await pool.query(
        `SELECT id FROM nebula.conversation_snapshots
         WHERE conversation_id = $1 ORDER BY snapshot_index DESC LIMIT 1`,
        [id]
      );
      if (snapResult.rows.length === 0) {
        return res.status(404).json({ error: 'No snapshots found for this conversation' });
      }
      const snapshotId = snapResult.rows[0].id;
      const result = await bs.listBlocks(pool, snapshotId);
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conversations/by-snapshot/:snapshotId/blocks — list blocks for a
  // specific snapshot_id.
  //
  // This endpoint is the stable, symmetric counterpart to
  // `/api/conversations/:id/blocks`. The `:id` route above resolves a
  // *conversation_id* into its latest snapshot and then reads blocks; this
  // route takes a *snapshot_id* directly. The distinction exists because
  // callers fall into two cohorts:
  //   - UI flows that begin from a list of conversations (conversation_id
  //     is the natural handle), and
  //   - callers that already hold a snapshot_id (e.g. from a snapshot list,
  //     a cross-reference, or a previous /api/snapshots fetch).
  //
  // Returning the same envelope shape as `/api/conversations/:id/blocks`
  // and `/api/snapshots/:id/blocks` (`{ snapshotId, blocks }`) lets
  // assembly-srv act as a transparent proxy without a response-shape
  // transform — see assembly-srv `routes/conversations.js`.
  router.get('/conversations/by-snapshot/:snapshotId/blocks', async (req: Request, res: Response) => {
    try {
      const snapshotId = req.params.snapshotId as string;
      if (!isUuid(snapshotId)) {
        return res.status(400).json({ error: 'snapshotId must be a UUID' });
      }
      // Existence check + conversation_id enrichment, mirroring the snapshot
      // list shape so the caller can tie blocks back to a conversation.
      const snapResult = await pool.query(
        `SELECT id, conversation_id, snapshot_index
         FROM nebula.conversation_snapshots
         WHERE id = $1`,
        [snapshotId]
      );
      if (snapResult.rows.length === 0) {
        return res.status(404).json({ error: 'Snapshot not found' });
      }
      const result = await bs.listBlocks(pool, snapshotId);
      // bs.listBlocks returns { blocks, segments, overrides, diff? } — surface
      // additional snapshot row metadata alongside, in a non-breaking way.
      res.json({
        ...result,
        conversationId: snapResult.rows[0].conversation_id,
        snapshotIndex: snapResult.rows[0].snapshot_index,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/snapshots — create a new conversation snapshot with blocks
  router.post('/snapshots', async (req: Request, res: Response) => {
    try {
      const { conversationId, snapshotIndex, sourceHash, captureMode, blockCount, createdBy, blocks } = req.body;
      if (!conversationId || snapshotIndex === undefined || !sourceHash) {
        return res.status(400).json({ error: 'conversationId, snapshotIndex, and sourceHash are required' });
      }
      const result = await bs.createSnapshot(pool, {
        conversationId, snapshotIndex, sourceHash, captureMode, blockCount, createdBy, blocks,
      });
      // Cache new blocks and invalidate stale projection
      try {
        await bsRedis.cacheBlocks(result.snapshot.id, blocks || []);
        await bsRedis.invalidateProjection(result.snapshot.id);
      } catch (_) { /* non-fatal */ }
      res.status(201).json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/segments — commit a user-defined segment
  router.post('/segments', async (req: Request, res: Response) => {
    try {
      const { conversationId, snapshotId, startBlockId, endBlockId, startBlockIndex, endBlockIndex, segmentType, source, title, notesMd, createdBy } = req.body;
      if (!conversationId || !snapshotId || !startBlockId || !endBlockId || startBlockIndex === undefined || endBlockIndex === undefined) {
        return res.status(400).json({ error: 'conversationId, snapshotId, startBlockId, endBlockId, startBlockIndex, and endBlockIndex are required' });
      }
      // Invalidate caches after segment commit
      try {
        await bsRedis.invalidateCandidates(req.body.snapshotId);
        await bsRedis.invalidateProjection(req.body.snapshotId);
        await bsRedis.invalidateGraph(req.body.snapshotId);
      } catch (_) { /* non-fatal */ }
      const segment = await bs.createSegment(pool, {
        conversationId, snapshotId, startBlockId, endBlockId, startBlockIndex, endBlockIndex, segmentType, source, title, notesMd, createdBy,
      });
      res.status(201).json(segment);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/segments/:id — update segment (type, state, title, notes)
  router.patch('/segments/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { segmentType, state, title, notesMd } = req.body;
      const segment = await bs.updateSegment(pool, id as string, { segmentType, state, title, notesMd });
      if (!segment) return res.status(404).json({ error: 'Segment not found' });
      res.json(segment);
      try { await bsRedis.invalidateProjection(segment.snapshot_id); }
      catch (_) { /* non-fatal */ }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/segments/:id — supersede (bitemporal expire) a segment
  router.delete('/segments/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const result = await bs.supersedeSegment(pool, id as string);
      res.json(result);

      // Invalidate caches after segment deletion
      try {
        const { rows } = await pool.query(
          'SELECT snapshot_id FROM nebula.segments_history WHERE id = $1 AND recorded_until_dt = \'9999-12-31 23:59:59+00\'',
          [req.params.id]
        );
        if (rows.length > 0) {
          await bsRedis.invalidateProjection(rows[0].snapshot_id);
          await bsRedis.invalidateGraph(rows[0].snapshot_id);
        }
      } catch (_) { /* non-fatal */ }
    } catch (err: any) {
      const status = err.message === 'Segment not found' ? 404 : 500;
      res.status(status).json({ error: err.message });
    }
  });

  // POST /api/projection-overrides — add a suppression/deprioritization override
  router.post('/projection-overrides', async (req: Request, res: Response) => {
    try {
      const { conversationId, snapshotId, targetType, targetId, projectionTarget, overrideType, reasonCode, notesMd, source, createdBy } = req.body;
      if (!conversationId || !snapshotId || !targetId) {
        return res.status(400).json({ error: 'conversationId, snapshotId, and targetId are required' });
      }
      // Invalidate projection cache for this snapshot
      try {
        await bsRedis.invalidateProjection(req.body.snapshotId, req.body.projectionTarget || 'BP');
      } catch (_) { /* non-fatal */ }
      const override = await bs.createProjectionOverride(pool, {
        conversationId, snapshotId, targetType, targetId, projectionTarget, overrideType, reasonCode, notesMd, source, createdBy,
      });
      res.status(201).json(override);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/projection-overrides/:id — remove an override
  router.delete('/projection-overrides/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const result = await bs.removeProjectionOverride(pool, id as string);
      res.json(result);

      // Invalidate projection cache
      try {
        const { rows } = await pool.query(
          "SELECT snapshot_id, projection_target FROM nebula.projection_overrides_history WHERE id = $1 AND recorded_until_dt = '9999-12-31 23:59:59+00'",
          [req.params.id]
        );
        if (rows.length > 0) {
          await bsRedis.invalidateProjection(rows[0].snapshot_id, rows[0].projection_target);
        }
      } catch (_) { /* non-fatal */ }
    } catch (err: any) {
      const status = err.message === 'Override not found' ? 404 : 500;
      res.status(status).json({ error: err.message });
    }
  });

  // GET /api/snapshots/:id/projection — get the BP projection for a snapshot
  router.get('/snapshots/:id/projection', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const tq = req.query.target;
      const target = (typeof tq === 'string' ? tq : undefined) || 'BP';

      // Try Redis cache first
      try {
        const cached = await bsRedis.getCachedProjection(id as string, target);
        if (cached) { res.json(cached); return; }
      } catch (_) { /* cache miss — fall through to PG */ }
      const result = await bs.getProjection(pool, id as string, target);
      res.json(result);

      // Cache projection for next read
      try { await bsRedis.cacheProjection(id as string, target, result); }
      catch (_) { /* non-fatal */ }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/snapshots/:id/references — get harvest references for a snapshot
  router.get('/snapshots/:id/references', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const sq = req.query.state;
      const eq = req.query.edgeType;
      const state = typeof sq === 'string' ? sq : undefined;
      const edgeType = typeof eq === 'string' ? eq : undefined;
      const mq = req.query.minConfidence;
      const minConfidence = typeof mq === 'string' ? parseFloat(mq) : undefined;
      const result = await bs.listReferences(pool, id as string, { state, edgeType, minConfidence });
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  //  KNOWLEDGE GRAPH — read-only queries for graph visualization
  // ═══════════════════════════════════════════════════════════════════

  // GET /api/knowledge/entities — list knowledge graph entities with optional filters and pagination
  router.get('/knowledge/entities', async (req: Request, res: Response) => {
    try {
      const { section, entity_type, search } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const conditions: string[] = [];
      const filterParams: any[] = [];
      let i = 1;

      if (section) { conditions.push(`section = $${i++}`); filterParams.push(section); }
      if (entity_type) { conditions.push(`entity_type = $${i++}`); filterParams.push(entity_type); }
      if (search) { conditions.push(`(name ILIKE $${i} OR description ILIKE $${i})`); filterParams.push(`%${search}%`); i++; }

      const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT id, section, entity_id, name, entity_type, status,
                  substring(description, 1, 500) AS description_abbr,
                  created_at, updated_at
           FROM knowledge.graph_entities ${where}
           ORDER BY section, name
           LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM knowledge.graph_entities ${where}`,
          filterParams
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/entities/:section/:entityId — get single entity
  router.get('/knowledge/entities/:section/:entityId', async (req: Request, res: Response) => {
    try {
      const { section, entityId } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM knowledge.graph_entities WHERE section = $1 AND entity_id = $2',
        [section, entityId]
      );
      if (!row) return res.status(404).json({ error: 'Entity not found' });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/entities/:section/:entityId/relations — inbound + outbound with pagination
  router.get('/knowledge/entities/:section/:entityId/relations', async (req: Request, res: Response) => {
    try {
      const { section, entityId } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [outbound, inbound, outboundCount, inboundCount] = await Promise.all([
        pool.query(
          `SELECT e.id, e.relation_type, e.target_section, e.target_id, e.properties,
                  tgt.name AS target_name
           FROM knowledge.graph_edges e
           LEFT JOIN knowledge.graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
           WHERE e.source_section = $1 AND e.source_id = $2
           ORDER BY e.relation_type
           LIMIT $3 OFFSET $4`,
          [section, entityId, pageSize, offset]
        ),
        pool.query(
          `SELECT e.id, e.relation_type, e.source_section, e.source_id, e.properties,
                  src.name AS source_name
           FROM knowledge.graph_edges e
           LEFT JOIN knowledge.graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
           WHERE e.target_section = $1 AND e.target_id = $2
           ORDER BY e.relation_type
           LIMIT $3 OFFSET $4`,
          [section, entityId, pageSize, offset]
        ),
        pool.query(
          'SELECT COUNT(*)::int AS total FROM knowledge.graph_edges WHERE source_section = $1 AND source_id = $2',
          [section, entityId]
        ),
        pool.query(
          'SELECT COUNT(*)::int AS total FROM knowledge.graph_edges WHERE target_section = $1 AND target_id = $2',
          [section, entityId]
        ),
      ]);

      res.json({
        entity: { section, entityId },
        outbound: {
          items: outbound.rows.map(camelCaseRow),
          total: parseInt(outboundCount.rows[0].total, 10),
          page,
          pageSize,
        },
        inbound: {
          items: inbound.rows.map(camelCaseRow),
          total: parseInt(inboundCount.rows[0].total, 10),
          page,
          pageSize,
        },
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/edges — list graph edges with optional filters and pagination
  router.get('/knowledge/edges', async (req: Request, res: Response) => {
    try {
      const { source_section, source_id, target_section, target_id, relation_type } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const conditions: string[] = [];
      const filterParams: any[] = [];
      let i = 1;

      if (source_section) { conditions.push(`e.source_section = $${i++}`); filterParams.push(source_section); }
      if (source_id) { conditions.push(`e.source_id = $${i++}`); filterParams.push(source_id); }
      if (target_section) { conditions.push(`e.target_section = $${i++}`); filterParams.push(target_section); }
      if (target_id) { conditions.push(`e.target_id = $${i++}`); filterParams.push(target_id); }
      if (relation_type) { conditions.push(`e.relation_type = $${i++}`); filterParams.push(relation_type); }

      const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT e.id, e.source_section, e.source_id, e.relation_type,
                  e.target_section, e.target_id, e.properties, e.created_at,
                  src.name AS source_name, tgt.name AS target_name
           FROM knowledge.graph_edges e
           LEFT JOIN knowledge.graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
           LEFT JOIN knowledge.graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
           ${where}
           ORDER BY e.source_section, e.source_id, e.relation_type
           LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total
           FROM knowledge.graph_edges e
           LEFT JOIN knowledge.graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
           LEFT JOIN knowledge.graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
           ${where}`,
          filterParams
        ),
      ]);

      res.json({ items: dataResult.rows.map(camelCaseRow), total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  //  OP MAPPING REGISTRY — versioned intent→opcode mapping table
  //  Schema: nebula.op_registry
  // ═══════════════════════════════════════════════════════════════════

  // POST /api/op-registry — create a new registry entry
  router.post('/op-registry', async (req: Request, res: Response) => {
    try {
      const {
        id, intent_id, version, status, label,
        match_patterns, opcode_template, required_params, optional_params,
        preconditions, postconditions, idempotency_key, successor_id, notes,
      } = req.body;

      if (!id || !intent_id) {
        return res.status(400).json({ error: 'id and intent_id are required' });
      }

      const now = new Date().toISOString();
      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.op_registry
          (id, intent_id, version, status, label,
           match_patterns, opcode_template, required_params, optional_params,
           preconditions, postconditions, idempotency_key, successor_id, notes,
           created_at, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
         RETURNING *`,
        [
          id, intent_id, version || 'v1', status || 'active', label || '',
          match_patterns || [], JSON.stringify(opcode_template || []),
          required_params || [], optional_params || [],
          preconditions || [], postconditions || [],
          idempotency_key || '', successor_id || null, notes || '',
          now, now,
        ]
      );
      res.status(201).json(row);
    } catch (err: any) {
      // Catch ISA validation trigger errors
      if (err.message && err.message.includes('Invalid opcode')) {
        return res.status(422).json({ error: err.message });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/op-registry — list registry entries with optional filters and pagination
  router.get('/op-registry', async (req: Request, res: Response) => {
    try {
      const {
        intent_id, status, search,
      } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const conditions: string[] = ['deleted_at IS NULL'];
      const filterParams: any[] = [];
      let i = 1;

      if (intent_id) { conditions.push(`intent_id = $${i++}`); filterParams.push(intent_id); }
      if (status) { conditions.push(`status = $${i++}`); filterParams.push(status); }
      if (search) {
        conditions.push(`(label ILIKE $${i} OR intent_id ILIKE $${i} OR notes ILIKE $${i})`);
        filterParams.push(`%${search}%`);
        i++;
      }

      const where = conditions.join(' AND ');

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM nebula.op_registry
           WHERE ${where}
           ORDER BY intent_id, version DESC
           LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.op_registry WHERE ${where}`,
          filterParams
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/op-registry/:id — get a single registry entry
  router.get('/op-registry/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.op_registry WHERE id = $1 AND deleted_at IS NULL',
        [id]
      );
      if (!row) return res.status(404).json({ error: `Registry entry ${id} not found` });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/op-registry/:id/deprecate — deprecate a registry entry
  router.patch('/op-registry/:id/deprecate', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { successor_id } = req.body;
      const now = new Date().toISOString();
      const { rows: [row] } = await pool.query(
        `UPDATE nebula.op_registry
         SET status = 'deprecated', successor_id = COALESCE($2, successor_id),
             updated_at = $3
         WHERE id = $1 AND deleted_at IS NULL
         RETURNING *`,
        [id, successor_id || null, now]
      );
      if (!row) return res.status(404).json({ error: `Registry entry ${id} not found or already deleted` });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/op-registry/:id/supersede — mark as superseded (replaced by fork)
  router.patch('/op-registry/:id/supersede', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { successor_id } = req.body;
      if (!successor_id) return res.status(400).json({ error: 'successor_id is required to supersede an entry' });
      const now = new Date().toISOString();
      const { rows: [row] } = await pool.query(
        `UPDATE nebula.op_registry
         SET status = 'superseded', successor_id = $2, updated_at = $3
         WHERE id = $1 AND deleted_at IS NULL
         RETURNING *`,
        [id, successor_id, now]
      );
      if (!row) return res.status(404).json({ error: `Registry entry ${id} not found or already deleted` });
      res.json(row);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/op-registry/:id — soft-delete a registry entry
  router.delete('/op-registry/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const now = new Date().toISOString();
      const { rows: [row] } = await pool.query(
        'UPDATE nebula.op_registry SET deleted_at = $2, updated_at = $2, valid_until = $2 WHERE id = $1 AND deleted_at IS NULL RETURNING *',
        [id, now]
      );
      if (!row) return res.status(404).json({ error: `Registry entry ${id} not found` });
      res.json({ deleted: true, id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/op-registry/fork — create a new version of an existing intent mapping
  router.post('/op-registry/fork', async (req: Request, res: Response) => {
    try {
      const { source_id, new_version, label, notes, opcode_template, required_params } = req.body;
      if (!source_id || !new_version) {
        return res.status(400).json({ error: 'source_id and new_version are required' });
      }

      // Get the source entry
      const { rows: [source] } = await pool.query(
        'SELECT * FROM nebula.op_registry WHERE id = $1 AND deleted_at IS NULL',
        [source_id]
      );
      if (!source) return res.status(404).json({ error: `Source registry entry ${source_id} not found` });

      // Create the fork with a new ID
      const forkId = `${source.intent_id}:${new_version}`;
      const now = new Date().toISOString();

      const { rows: [fork] } = await pool.query(
        `INSERT INTO nebula.op_registry
          (id, intent_id, version, status, label,
           match_patterns, opcode_template, required_params, optional_params,
           preconditions, postconditions, idempotency_key, notes,
           created_at, updated_at)
         VALUES ($1, $2, $3, 'active', $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
         RETURNING *`,
        [
          forkId,
          source.intent_id,
          new_version,
          label || `${source.label} (${new_version})`,
          source.match_patterns,
          JSON.stringify(opcode_template || source.opcode_template),
          required_params || source.required_params,
          source.optional_params,
          source.preconditions,
          source.postconditions,
          source.idempotency_key,
          notes || '',
          now, now,
        ]
      );

      // Supersede the source
      await pool.query(
        `UPDATE nebula.op_registry SET status = 'superseded', successor_id = $2, updated_at = $3
         WHERE id = $1`,
        [source_id, forkId, now]
      );

      res.status(201).json({
        fork,
        superseded: source_id,
        message: `Forked ${source_id} → ${forkId}`,
      });
    } catch (err: any) {
      if (err.message && err.message.includes('Invalid opcode')) {
        return res.status(422).json({ error: err.message });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/op-registry/:id/lineage — show the version lineage of an intent
  router.get('/op-registry/:id/lineage', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      // Get the entry and follow successor chain
      const { rows: [entry] } = await pool.query(
        'SELECT * FROM nebula.op_registry WHERE id = $1 AND deleted_at IS NULL',
        [id]
      );
      if (!entry) return res.status(404).json({ error: `Registry entry ${id} not found` });

      // Get all versions of this intent
      const { rows: lineage } = await pool.query(
        `SELECT id, intent_id, version, status, successor_id, label, created_at
         FROM nebula.op_registry
         WHERE intent_id = $1 AND deleted_at IS NULL
         ORDER BY version DESC`,
        [entry.intent_id]
      );

      res.json({ intent_id: entry.intent_id, entries: lineage, count: lineage.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/summary — entity counts by section (with embedded), edge counts by relation type
  router.get('/knowledge/summary', async (_req: Request, res: Response) => {
    try {
      const [entityCount, edgeCount, xrefCount, sections, relationTypes, graphSummary] = await Promise.all([
        pool.query('SELECT COUNT(*)::int AS count FROM knowledge.graph_entities'),
        pool.query('SELECT COUNT(*)::int AS count FROM knowledge.graph_edges'),
        pool.query('SELECT COUNT(*)::int AS count FROM knowledge.graph_cross_references'),
        pool.query('SELECT section, COUNT(*)::int AS count FROM knowledge.graph_entities GROUP BY section ORDER BY count DESC'),
        pool.query('SELECT relation_type, COUNT(*)::int AS count FROM knowledge.graph_edges GROUP BY relation_type ORDER BY count DESC'),
        pool.query('SELECT * FROM knowledge.v_graph_summary ORDER BY section'),
      ]);
      res.json({
        entityCount: entityCount.rows[0]?.count ?? 0,
        edgeCount: edgeCount.rows[0]?.count ?? 0,
        crossReferenceCount: xrefCount.rows[0]?.count ?? 0,
        bySection: sections.rows,
        byRelationType: relationTypes.rows,
        embeddingSummary: graphSummary.rows,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/view — combined data payload for graph visualization
  // Returns all entities (including linked harvest_candidates) + all edges in one call, with optional limit. Harvest_candidates are unioned so spawn-requirement cross-references render as dashed edges in graph-view X-Refs mode.
  router.get('/knowledge/view', async (req: Request, res: Response) => {
    try {
      const limit = Math.min(parseInt(req.query.limit as string) || 500, 1000);

      // Union knowledge entities with linked harvest_candidates (for cross-ref visibility)
      const unionEntities = `
        SELECT id, section, entity_id, name, entity_type, status, description_abbr
        FROM (
          SELECT id, section, entity_id, name, entity_type, status,
                 substring(description, 1, 300) AS description_abbr
          FROM knowledge.graph_entities
          UNION ALL
          SELECT gen_random_uuid() AS id,
                 'harvest_candidate' AS section,
                 id::text AS entity_id,
                 COALESCE(title, 'Untitled') AS name,
                 'harvest_candidate' AS entity_type,
                 COALESCE(status, 'unlinked') AS status,
                 substring(intent_description, 1, 300) AS description_abbr
          FROM nebula.harvest_candidates
          WHERE system_id IS NOT NULL
        ) AS all_entities
        ORDER BY section, name
        LIMIT $1`;

      const [entities, edges] = await Promise.all([
        pool.query(unionEntities, [limit]),
        pool.query(
          `SELECT e.id, e.source_section, e.source_id, e.relation_type,
                  e.target_section, e.target_id,
                  src.name AS source_name, tgt.name AS target_name
           FROM knowledge.graph_edges e
           LEFT JOIN knowledge.graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
           LEFT JOIN knowledge.graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
           LIMIT $1`,
          [limit * 3]
        ),
      ]);
      res.json({
        entities: entities.rows,
        edges: edges.rows,
        entityCount: entities.rows.length,
        edgeCount: edges.rows.length,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/knowledge/cross-references — list cross-references for graph overlay with pagination. Also includes harvest_candidate spawn-requirement cross-references from nebula.cross_references.
  router.get('/knowledge/cross-references', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      // Union knowledge cross-references with harvest_candidate spawn-requirement xrefs
      const xrefSubquery = `(
        SELECT xr.id, xr.map_name, xr.source_section, xr.source_id,
               xr.target_section, xr.target_id, xr.weight
        FROM knowledge.graph_cross_references xr
        UNION ALL
        SELECT gen_random_uuid() AS id,
               'harvest_candidate' AS map_name,
               source_type AS source_section,
               source_id,
               target_type AS target_section,
               target_id,
               1 AS weight
        FROM nebula.cross_references
        WHERE source_type = 'harvest_candidate'
          AND rel_type = 'ag:spawns_plan'
      ) AS all_xrefs`;

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT id, map_name, source_section, source_id, target_section, target_id, weight
           FROM ${xrefSubquery}
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM ${xrefSubquery}`,
          []
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  //  AUDIT GRAPH — agent records as graph nodes
  //  (route handler is defined above, alongside other audit routes)
  // ═══════════════════════════════════════════════════════════════════

  // ═══════════════════════════════════════════════════════════════════
  //  CONDUIT — plan history & point-in-time queries (conduit + vision schemas)
  //  Reads from nebula.plans, nebula.receipts_unified, vision.tickets
  //  via fully qualified table names (pool search_path=nebula).
  // ═══════════════════════════════════════════════════════════════════

  // GET /api/conduit/plans — list all conduit plans, option to include soft-deleted
  // Query params: includeDeleted (bool), asOf (ISO timestamp), status (filter by derived status)
  router.get('/conduit/plans', async (req: Request, res: Response) => {
    try {
      const includeDeleted = req.query.includeDeleted === 'true';
      const asOf = req.query.asOf as string | undefined;
      const statusFilter = req.query.status as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);
      const offset = parseInt(req.query.offset as string) || 0;

      let sql: string;
      const params: any[] = [];
      let i = 1;

      if (asOf) {
        // Point-in-time: use plan_status view + receipts up to asOf
        // We query the plans table and join with receipts to derive historical state
        sql = `SELECT p.*,
          (
            SELECT r.type FROM nebula.receipts_unified r
            WHERE r.plan_id = p.id
              AND r.created_at <= $${i}
            ORDER BY r.created_at DESC LIMIT 1
          ) AS derived_status_at_time
          FROM nebula.plans p
          WHERE 1=1`;
        i++;
        params.push(asOf);

        if (!includeDeleted) {
          sql += ` AND p.deleted = 0`;
        }
        if (statusFilter) {
          sql += ` AND (SELECT r.type FROM nebula.receipts_unified r
            WHERE r.plan_id = p.id
              AND r.created_at <= $1    -- $1 is asOf
            ORDER BY r.created_at DESC LIMIT 1) = $${i}`;
          i++;
          params.push(statusFilter);
        }
        sql += ` ORDER BY p.created_at DESC LIMIT $${i} OFFSET $${i+1}`;
        params.push(limit, offset);
      } else {
        // Current state: use plan_status view
        sql = `SELECT * FROM nebula.plan_status ps WHERE 1=1`;

        if (!includeDeleted) {
          sql += ` AND ps.deleted = 0`;
        }
        if (statusFilter) {
          sql += ` AND ps.derived_status = $${i}`;
          i++;
          params.push(statusFilter);
        }
        sql += ` ORDER BY ps.created_at DESC LIMIT $${i} OFFSET $${i+1}`;
        params.push(limit, offset);
      }

      const { rows } = await pool.query(sql, params);
      res.json({ plans: rows, count: rows.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conduit/plans/as-of — point-in-time snapshot of plan states
  // Query params: timestamp (ISO 8601, required), includeDeleted
  router.get('/conduit/plans/as-of', async (req: Request, res: Response) => {
    try {
      const timestamp = req.query.timestamp as string;
      if (!timestamp) return res.status(400).json({ error: 'timestamp query parameter is required (ISO 8601)' });
      const includeDeleted = req.query.includeDeleted === 'true';

      const { rows } = await pool.query(
        `SELECT p.*,
          (
            SELECT r.type FROM nebula.receipts_unified r
            WHERE r.plan_id = p.id AND r.created_at <= $1
            ORDER BY r.created_at DESC LIMIT 1
          ) AS derived_status_at_time,
          (
            SELECT r.created_at FROM nebula.receipts_unified r
            WHERE r.plan_id = p.id AND r.created_at <= $1
            ORDER BY r.created_at DESC LIMIT 1
          ) AS last_receipt_at_time
          FROM nebula.plans p
          WHERE (p.created_at <= $1 OR p.updated_at <= $1)
          ${includeDeleted ? '' : 'AND p.deleted = 0'}
          ORDER BY p.created_at DESC`,
        [timestamp]
      );
      res.json({ timestamp, plans: rows, count: rows.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conduit/plans/:id/history — full lifecycle history for one plan
  // Returns plan metadata (even if deleted), all receipts, all tickets, linked sessions, token usage
  router.get('/conduit/plans/:id/history', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      // Plan row (even if deleted)
      const { rows: [plan] } = await pool.query(
        'SELECT * FROM nebula.plans WHERE id = $1',
        [id]
      );
      if (!plan) return res.status(404).json({ error: `Plan ${id} not found` });

      // All receipts in chronological order
      const { rows: receipts } = await pool.query(
        'SELECT * FROM nebula.receipts_unified WHERE plan_id = $1 ORDER BY created_at ASC',
        [id]
      );

      // All tickets in chronological order
      const { rows: tickets } = await pool.query(
        'SELECT * FROM vision.tickets WHERE plan_id = $1 ORDER BY created_at ASC',
        [id]
      );

      // Token usage summary
      const { rows: [tokenUsage] } = await pool.query(
        'SELECT COALESCE(SUM(tokens_used), 0) AS total_tokens, COUNT(*) AS receipt_count FROM nebula.receipts_unified WHERE plan_id = $1',
        [id]
      );

      // Sessions that worked on this plan
      const { rows: sessions } = await pool.query(
        'SELECT s.id, s.agent_role, s.start_iso, s.end_iso, s.model, s.exit_code, s.workflow_id FROM conduit.sessions s WHERE s.id IN (SELECT DISTINCT r.session_id FROM nebula.receipts_unified r WHERE r.plan_id = $1 AND r.session_id IS NOT NULL) ORDER BY s.start_iso ASC',
        [id]
      );

      res.json({
        plan,
        receipts,
        tickets,
        sessions,
        tokenUsage: { totalTokens: tokenUsage?.total_tokens ?? 0, receiptCount: tokenUsage?.receipt_count ?? 0 },
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conduit/plans/:id/receipts — receipts for a specific plan
  router.get('/conduit/plans/:id/receipts', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows } = await pool.query(
        'SELECT * FROM nebula.receipts_unified WHERE plan_id = $1 ORDER BY created_at ASC',
        [id]
      );
      res.json({ planId: id, receipts: rows, count: rows.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/conduit/deleted-plans — shortcut to find all soft-deleted plans
  router.get('/conduit/deleted-plans', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          'SELECT * FROM nebula.plans WHERE deleted = 1 ORDER BY updated_at DESC LIMIT $1 OFFSET $2',
          [pageSize, offset]
        ),
        pool.query("SELECT COUNT(*)::int AS total FROM nebula.plans WHERE deleted = 1"),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  EXECUTION AUTHORITY (ADR-006)
  // ════════════════════════════════════════════════════════════════

  // POST /api/execution/requests — create a new WorkRequest
  router.post('/execution/requests', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const {
        businessKey, title, intentType, objective, inputs,
        deterministic, maxRetries, timeoutPolicy, resourceHints,
        opTrace, status, sourcePlanId, sourceWrId,
      } = req.body;

      if (!businessKey) {
        res.status(400).json({ error: 'businessKey is required' });
        return;
      }

      const { rows: [row] } = await client.query(
        `INSERT INTO execution.requests (
          business_key, title, intent_type, objective, inputs,
          deterministic, max_retries, timeout_policy, resource_hints,
          op_trace, status, source_plan_id, source_wr_id
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) RETURNING *`,
        [
          businessKey, title||'', intentType||'task', objective||'',
          inputs||{}, deterministic??true, maxRetries||null,
          timeoutPolicy||null, resourceHints||[], opTrace||{},
          status||'DRAFT', sourcePlanId||null, sourceWrId||null,
        ]
      );
      await client.query('COMMIT');
      res.status(201).json(row);
    } catch (err: any) {
      await client.query('ROLLBACK');
      if (err.code === '23505') { // unique_violation
        res.status(409).json({ error: `Request with business_key '${req.body.businessKey}' already exists` });
      } else {
        res.status(500).json({ error: err.message });
      }
    } finally {
      client.release();
    }
  });

  // GET /api/execution/requests — list requests
  router.get('/execution/requests', async (req: Request, res: Response) => {
    try {
      const { status } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const filterParams: any[] = [];
      let i = 1;
      if (status) { clauses.push(`status = $${i++}`); filterParams.push(status); }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM execution.requests ${where} ORDER BY created_at DESC LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM execution.requests ${where}`,
          filterParams
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/execution/requests/:id — get a single request
  router.get('/execution/requests/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows } = await pool.query(
        'SELECT * FROM execution.requests WHERE id = $1', [id]
      );
      if (rows.length === 0) { res.status(404).json({ error: 'Request not found' }); return; }
      // Also fetch leases, attempts, receipts
      const { rows: leases } = await pool.query(
        'SELECT * FROM execution.leases WHERE request_id = $1 ORDER BY acquired_at DESC', [id]
      );
      const { rows: attempts } = await pool.query(
        'SELECT * FROM execution.attempts WHERE request_id = $1 ORDER BY created_at DESC', [id]
      );
      const { rows: receipts } = await pool.query(
        'SELECT * FROM execution.receipts WHERE request_id = $1 ORDER BY issued_at DESC', [id]
      );
      res.json({ ...rows[0], leases, attempts, receipts });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/execution/requests/:id/transition — transition WorkRequest status
  router.patch('/execution/requests/:id/transition', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { id } = req.params;
      const { targetStatus, reason } = req.body;

      const validTransitions: Record<string, string[]> = {
        DRAFT:     ['COMPILED', 'CANCELLED'],
        COMPILED:  ['VALIDATED', 'CANCELLED'],
        VALIDATED: ['ADMITTED', 'CANCELLED'],
        ADMITTED:  ['READY', 'CANCELLED'],
        READY:     ['COMPLETED', 'FAILED', 'CANCELLED'],
        COMPLETED: [],
        FAILED:    [],
        CANCELLED: [],
      };

      const { rows: [current] } = await client.query(
        'SELECT * FROM execution.requests WHERE id = $1', [id]
      );
      if (!current) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Request not found' }); return; }

      const allowed = validTransitions[current.status] || [];
      if (!allowed.includes(targetStatus)) {
        await client.query('ROLLBACK');
        res.status(400).json({
          error: `Invalid transition: ${current.status} → ${targetStatus}`,
          allowed,
        });
        return;
      }

      const { rows: [updated] } = await client.query(
        'UPDATE execution.requests SET status = $1, updated_at = NOW() WHERE id = $2 RETURNING *',
        [targetStatus, id]
      );

      await client.query('COMMIT');
      res.json({ previous: current.status, request: updated, reason: reason||null });
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/execution/leases/acquire — acquire a lease on a request
  router.post('/execution/leases/acquire', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { requestId, executorId, ttlSeconds } = req.body;

      if (!requestId || !executorId) {
        res.status(400).json({ error: 'requestId and executorId are required' });
        return;
      }

      // Check request exists and is in a leaseable state
      const { rows: [request] } = await client.query(
        'SELECT * FROM execution.requests WHERE id = $1', [requestId]
      );
      if (!request) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Request not found' }); return; }
      if (!['ADMITTED', 'READY'].includes(request.status)) {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Request must be ADMITTED or READY to lease (current: ${request.status})` });
        return;
      }

      // Check no active lease exists
      const { rows: existing } = await client.query(
        "SELECT id FROM execution.leases WHERE request_id = $1 AND status = 'ACTIVE'", [requestId]
      );
      if (existing.length > 0) {
        await client.query('ROLLBACK');
        res.status(409).json({ error: 'Active lease already exists for this request', existingLeaseId: existing[0].id });
        return;
      }

      const ttl = ttlSeconds || 300;
      const { rows: [lease] } = await client.query(
        `INSERT INTO execution.leases (request_id, executor_id, ttl_seconds, expires_at)
         VALUES ($1, $2, $3, NOW() + ($4 || ' seconds')::interval) RETURNING *`,
        [requestId, executorId, ttl, String(ttl)]
      );

      await client.query('COMMIT');
      res.status(201).json(lease);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/execution/leases/:id/renew — renew an active lease
  router.post('/execution/leases/:id/renew', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { id } = req.params;
      const { ttlSeconds } = req.body;
      const ttl = ttlSeconds || 300;

      const { rows: [lease] } = await client.query(
        'SELECT * FROM execution.leases WHERE id = $1', [id]
      );
      if (!lease) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Lease not found' }); return; }
      if (lease.status !== 'ACTIVE') {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Cannot renew lease in status '${lease.status}' (must be ACTIVE)` });
        return;
      }
      if (new Date(lease.expires_at) < new Date()) {
        // Auto-expire
        await client.query("UPDATE execution.leases SET status = 'EXPIRED' WHERE id = $1", [id]);
        await client.query('COMMIT');
        res.status(400).json({ error: 'Lease has already expired' });
        return;
      }

      const { rows: [updated] } = await client.query(
        `UPDATE execution.leases
         SET ttl_seconds = $1, expires_at = NOW() + ($3 || ' seconds')::interval
         WHERE id = $2 AND status = 'ACTIVE' RETURNING *`,
        [ttl, id, String(ttl)]
      );

      await client.query('COMMIT');
      res.json(updated);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/execution/leases/:id/release — release an active lease
  router.post('/execution/leases/:id/release', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { id } = req.params;

      const { rows: [lease] } = await client.query(
        'SELECT * FROM execution.leases WHERE id = $1', [id]
      );
      if (!lease) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Lease not found' }); return; }
      if (lease.status !== 'ACTIVE') {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Cannot release lease in status '${lease.status}' (must be ACTIVE)` });
        return;
      }

      const { rows: [updated] } = await client.query(
        "UPDATE execution.leases SET status = 'RELEASED', released_at = NOW() WHERE id = $1 RETURNING *",
        [id]
      );

      await client.query('COMMIT');
      res.json(updated);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // ─────────────────────────────────────────────────────────────────────
  //  ROLE LEASES (RoleLeases / plan 1286) — session-level leases in tackle
  //  schema: a bounded window + budget under which a role on a channel may
  //  consume work. Mirrors execution.leases (per-request) at role scope.
  // ─────────────────────────────────────────────────────────────────────

  // POST /api/role-leases/issue — issue an ACTIVE role lease
  router.post('/role-leases/issue', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { role, channel, model, ttlSeconds, budgetUnits, windowEnd } = req.body;

      if (!role) {
        await client.query('ROLLBACK');
        res.status(400).json({ error: 'role is required' });
        return;
      }

      // D-2026-08-16-007 (R2): one ACTIVE lease per (role, channel).
      // Different channels do not conflict.
      const leaseChannel = channel || 'interactive';
      const { rows: existing } = await client.query(
        "SELECT id FROM tackle.role_leases WHERE role = $1 AND channel = $2 AND status = 'ACTIVE'",
        [role, leaseChannel]
      );
      if (existing.length > 0) {
        await client.query('ROLLBACK');
        res.status(409).json({ error: 'Active role lease already exists for this channel', existingLeaseId: existing[0].id });
        return;
      }

      // window_end explicit OR ttl from now (mandatory time limit per design)
      const ttl = ttlSeconds ?? 3600;
      const windowEndTs = windowEnd
        ? new Date(windowEnd)
        : new Date(Date.now() + ttl * 1000);
      if (windowEndTs.getTime() <= Date.now()) {
        await client.query('ROLLBACK');
        res.status(400).json({ error: 'windowEnd/ttlSeconds must be in the future' });
        return;
      }

      const { rows: [lease] } = await client.query(
        `INSERT INTO tackle.role_leases
           (role, channel, model, window_end, budget_units, expires_at)
         VALUES ($1, $2, $3, $4, $5, $4) RETURNING *`,
        [role, leaseChannel, model || null, windowEndTs, budgetUnits ?? null]
      );
      await client.query('COMMIT');
      await emitRoleLifecycleEvent(pool, 'role.granted', 'nebula-srv.role-leases', 'role_lease', String(lease.id), {
        role,
        channel: leaseChannel,
        model: lease.model || null,
        windowEnd: lease.window_end,
      });
      res.status(201).json(lease);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/role-leases/:id/renew — renew an ACTIVE lease (window + budget)
  router.post('/role-leases/:id/renew', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { id } = req.params;
      const { ttlSeconds, budgetUnits } = req.body;

      const { rows: [lease] } = await client.query(
        'SELECT * FROM tackle.role_leases WHERE id = $1', [id]
      );
      if (!lease) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Role lease not found' }); return; }
      if (lease.status !== 'ACTIVE') {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Cannot renew role lease in status '${lease.status}' (must be ACTIVE)` });
        return;
      }
      if (new Date(lease.expires_at) < new Date()) {
        await client.query("UPDATE tackle.role_leases SET status = 'EXPIRED' WHERE id = $1", [id]);
        await client.query('COMMIT');
        res.status(400).json({ error: 'Role lease has already expired' });
        return;
      }

      const ttl = ttlSeconds ?? 3600;
      const { rows: [updated] } = await client.query(
        `UPDATE tackle.role_leases
         SET window_end = GREATEST(window_end, NOW() + ($1 || ' seconds')::interval),
             expires_at = NOW() + ($1 || ' seconds')::interval,
             budget_units = COALESCE($2, budget_units),
             updated_at = NOW()
         WHERE id = $3 AND status = 'ACTIVE' RETURNING *`,
        [ttl, budgetUnits ?? null, id]
      );
      await client.query('COMMIT');
      res.json(updated);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/role-leases/:id/revoke — release an ACTIVE role lease
  router.post('/role-leases/:id/revoke', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { id } = req.params;
      const { rows: [lease] } = await client.query(
        'SELECT * FROM tackle.role_leases WHERE id = $1', [id]
      );
      if (!lease) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Role lease not found' }); return; }
      if (lease.status !== 'ACTIVE') {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Cannot revoke role lease in status '${lease.status}' (must be ACTIVE)` });
        return;
      }
      const { rows: [updated] } = await client.query(
        "UPDATE tackle.role_leases SET status = 'RELEASED', released_at = NOW(), release_reason = 'revoked', updated_at = NOW() WHERE id = $1 RETURNING *",
        [id]
      );
      await client.query('COMMIT');
      await emitRoleLifecycleEvent(pool, 'role.revoked', 'nebula-srv.role-leases', 'role_lease', String(updated.id), {
        role: updated.role,
        channel: updated.channel || null,
        releaseReason: 'revoked',
      });
      // D-2026-08-16-008 (R3): emit type:lease-revoked on explicit revoke.
      // Deduped on the ACTIVE → RELEASED transition — the pre-check above
      // 400s any non-ACTIVE lease before reaching this point.
      const revokeId = randomUUID();
      const now = new Date().toISOString();
      pool.query(
        `INSERT INTO nebula.agent_records_history (id, record_type, role, title, content, tags, created_at, recorded_on_dt, model)
         VALUES ($1::uuid, 'report', $2, $3, $4, $5, $6, $6, $7)`,
        [
          revokeId,
          'architect',
          `Role lease revoked: ${updated.role} (${updated.channel || 'unknown'})`,
          `## Role lease revoked\n\n- **Role:** ${updated.role}\n- **Channel:** ${updated.channel || 'unknown'}\n- **Model:** ${updated.model || 'unknown'}\n- **Lease ID:** ${updated.id}\n- **Released at:** ${now}\n\nExplicit revoke (D-008 R3). The authorization envelope is withdrawn; history preserved.`,
          roleLeaseRecordTags(['type:lease-revoked', 'to:architect', 'to:engineer', `role:${updated.role}`], updated.role, updated.model),
          now,
          updated.model || null
        ]
      ).catch(() => { /* best-effort — don't fail the response */ });
      // D-009 R4: targeted procedure-index cache invalidation (no broad flush)
      bsRedis.invalidateRoleMemory(updated.role).catch(() => {});
      res.json(updated);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // GET /api/role-leases — list role leases (filters: role, status)
  router.get('/role-leases', async (req: Request, res: Response) => {
    try {
      const { role, status, channel, limit } = req.query as Record<string, string | undefined>;
      const conds: string[] = [];
      const vals: any[] = [];
      if (role) { vals.push(role); conds.push(`role = $${vals.length}`); }
      if (status) { vals.push(status); conds.push(`status = $${vals.length}`); }
      if (channel) { vals.push(channel); conds.push(`channel = $${vals.length}`); }
      const where = conds.length ? `WHERE ${conds.join(' AND ')}` : '';
      const { rows } = await pool.query(
        `SELECT * FROM tackle.role_leases ${where} ORDER BY created_at DESC LIMIT $${vals.length + 1}`,
        [...vals, Number(limit) || 50]
      );
      res.json({ items: rows });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/cascade/subscriber-status — live liveness check for the
  // cascade interactive-turn subscriber (the daemon that turns duality
  // comments into agent turns). The subscriber tags its PG connection with
  // application_name='cascade-interactive-turn'; when the daemon dies its
  // socket closes and the backend disappears from pg_stat_activity. The
  // duality-ui TopBar polls this so users know BEFORE sending whether a
  // response is even possible.
  router.get('/cascade/subscriber-status', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT application_name, state, backend_start, pid
         FROM pg_stat_activity
         WHERE application_name = 'cascade-interactive-turn'
           AND datname = current_database()
         LIMIT 1`
      );
      const row = rows[0] || null;
      // NOTE: backend_start is the server backend's start time; for a
      // LISTEN connection the backend is spawned at connect, so it is a
      // close approximation of when the subscriber connected.
      res.json({
        up: !!row,
        state: row?.state ?? null,
        backendSince: row?.backend_start
          ? new Date(row.backend_start).toISOString()
          : null,
        backendPid: row?.pid ?? null,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/role-leases/stale — ACTIVE leases past window/budget (for sweep)
  router.get('/role-leases/stale', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT * FROM tackle.role_leases
         WHERE status = 'ACTIVE'
           AND (expires_at < NOW()
             OR (budget_units IS NOT NULL AND consumed_units >= budget_units))
         ORDER BY expires_at ASC`
      );
      res.json({ items: rows });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/role-leases/sweep — transition stale ACTIVE leases → EXPIRED
  // (D-2026-08-16-007 R5). Idempotent; non-destructive (never RELEASED — that
  // stays the explicit-revoke/auto-exhaust path). Each swept lease emits a
  // type:drift-finding record so the operator sees it. Wired at nebula-srv
  // startup + on a periodic interval in index.ts.
  router.post('/role-leases/sweep', async (_req: Request, res: Response) => {
    try {
      const { rows: stale } = await pool.query(
        `SELECT * FROM tackle.role_leases
         WHERE status = 'ACTIVE'
           AND expires_at < NOW()
         ORDER BY expires_at ASC`
      );
      const swept: any[] = [];
      for (const lease of stale) {
        const { rows: [updated] } = await pool.query(
          `UPDATE tackle.role_leases
           SET status = 'EXPIRED', release_reason = 'expired', updated_at = NOW()
           WHERE id = $1 AND status = 'ACTIVE'
           RETURNING *`,
          [lease.id]
        );
        if (updated) {
          swept.push(updated);
          await emitRoleLifecycleEvent(pool, 'role.expired', 'nebula-srv.role-leases', 'role_lease', String(updated.id), {
            role: updated.role,
            channel: updated.channel || null,
            releaseReason: 'expired',
          });
          const sweepId = randomUUID();
          const now = new Date().toISOString();
          pool.query(
            `INSERT INTO nebula.agent_records_history (id, record_type, role, title, content, tags, created_at, recorded_on_dt, model)
             VALUES ($1::uuid, 'report', $2, $3, $4, $5, $6, $6, $7)`,
            [
              sweepId,
              'engineer',
              `Stale role lease swept: ${lease.role} (${lease.channel})`,
              `## Stale role lease swept (D-007 R5)\n\n- **Role:** ${lease.role}\n- **Channel:** ${lease.channel}\n- **Lease ID:** ${lease.id}\n- **Window end:** ${lease.window_end}\n- **Model:** ${lease.model || 'unknown'}\n\nTransitioned ACTIVE → EXPIRED (window exceeded). No work was revoked mid-session; EXPIRED marks the lease as past its window for the worker-pool gate.`,
              roleLeaseRecordTags(['type:drift-finding', 'to:engineer', 'to:architect', 'domain:role-leases'], lease.role, lease.model),
              now,
              lease.model || null
            ]
          ).catch(() => { /* best-effort — don't fail the sweep */ });
          // D-009 R4: targeted procedure-index cache invalidation
          bsRedis.invalidateRoleMemory(lease.role).catch(() => {});
        }
      }
      res.json({ swept: swept.length, items: swept });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/role-leases/:role/status — derived lease state for (role, channel)
  // (D-2026-08-16-008 R1). NEVER_LEASED / ACTIVE / REVOKED / EXPIRED.
  router.get('/role-leases/:role/status', async (req: Request, res: Response) => {
    try {
      const { role } = req.params;
      const { channel } = req.query as Record<string, string | undefined>;
      const keyCond = channel ? 'role = $1 AND channel = $2' : 'role = $1';
      const keyVals = channel ? [role, channel] : [role];
      const { rows } = await pool.query(
        `SELECT id, status, released_at, release_reason, window_end, expires_at, channel, model
         FROM tackle.role_leases
         WHERE ${keyCond}
         ORDER BY created_at DESC LIMIT 1`,
        keyVals
      );
      if (rows.length === 0) {
        res.json({ state: 'NEVER_LEASED', lastLease: null });
        return;
      }
      const last = rows[0];
      let state: string;
      if (last.status === 'ACTIVE') {
        const pastWindow = new Date(last.expires_at || last.window_end) < new Date();
        state = pastWindow ? 'EXPIRED' : 'ACTIVE';
      } else if (last.status === 'RELEASED' && last.released_at) {
        state = 'REVOKED';
      } else {
        state = 'EXPIRED';
      }
      res.json({
        state,
        lastLease: {
          status: last.status,
          released_at: last.released_at,
          release_reason: last.release_reason,
        },
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/role-leases/consume — increment consumed_units (all channels)
  //
  // Unified accounting: execution_worker, harness-srv, and interactive
  // Freebuff all hit this one endpoint for lease consumption. When the
  // budget is exhausted, the endpoint auto-revokes the lease and emits
  // a type:lease-exhausted agent record so the operator is notified.
  router.post('/role-leases/consume', async (req: Request, res: Response) => {
    try {
      const { role, channel } = req.body;
      if (!role) return res.status(400).json({ error: 'role is required' });
      // D-2026-08-16-007 (R3): scope consumption to (role, channel) when the
      // caller names a channel. When omitted, fall back to matching any ACTIVE
      // lease for the role (backward-compatible with the {role}-only callers —
      // execution_worker, harness-srv, and the wr-conf-002 conformance suite).
      const where = channel
        ? `WHERE role = $1 AND channel = $2 AND status = 'ACTIVE'`
        : `WHERE role = $1 AND status = 'ACTIVE'`;
      const params = channel ? [role, channel] : [role];
      const { rows } = await pool.query(
        `UPDATE tackle.role_leases
         SET consumed_units = consumed_units + 1, updated_at = NOW()
         ${where}
         RETURNING id, consumed_units, budget_units, window_end, channel, model`,
        params
      );
      if (rows.length === 0) {
        return res.status(404).json({ error: `No ACTIVE lease for role '${role}'` });
      }
      const lease = rows[0];
      const exhausted = lease.budget_units !== null && lease.consumed_units >= lease.budget_units;

      if (exhausted) {
        // Auto-revoke — budget consumed, no more work under this lease.
        // G2 (binding, D-2026-08-14-001): dedup the exhaustion notification.
        // Only the call that performs the ACTIVE → RELEASED transition emits
        // the type:lease-exhausted agent record. Concurrent/duplicate consume
        // calls that find the lease already RELEASED fall through silently,
        // collapsing the wr-conf-002 spam (4+ records in ~40s) to a single
        // record per (role, lease) exhaustion episode.
        const revoked = await pool.query(
          `UPDATE tackle.role_leases SET status = 'RELEASED', released_at = NOW(), release_reason = 'exhausted', updated_at = NOW()
           WHERE id = $1 AND status = 'ACTIVE' RETURNING id`,
          [lease.id]
        );
        if (revoked.rows.length > 0) {
          await emitRoleLifecycleEvent(pool, 'role.expired', 'nebula-srv.role-leases', 'role_lease', String(lease.id), {
            role,
            channel: lease.channel || null,
            releaseReason: 'exhausted',
            consumed: lease.consumed_units,
            budget: lease.budget_units,
          });
          // Emit exhaustion record (fire-and-forget — don't block the response)
          const exhaustId = randomUUID();
          const now = new Date().toISOString();
          pool.query(
            `INSERT INTO nebula.agent_records_history (id, record_type, role, title, content, tags, created_at, recorded_on_dt, model)
             VALUES ($1::uuid, 'report', $2, $3, $4, $5, $6, $6, $7)`,
            [
              exhaustId,
              'architect',
              `Role-lease exhausted: ${role} (${lease.consumed_units}/${lease.budget_units})`,
              `## Role lease exhausted

- **Role:** ${role}
- **Channel:** ${lease.channel || 'unknown'}
- **Model:** ${lease.model || 'unknown'}
- **Consumed:** ${lease.consumed_units}/${lease.budget_units}
- **Window end:** ${lease.window_end}
- **Lease ID:** ${lease.id}

The lease has been auto-revoked. Issue a new lease to resume work.`,
              roleLeaseRecordTags(['type:lease-exhausted', 'to:architect', 'to:engineer', `role:${role}`], role, lease.model),
              now,
              lease.model || null
            ]
          ).catch(() => { /* best-effort — don't fail the response */ });
          // D-009 R4: targeted procedure-index cache invalidation
          bsRedis.invalidateRoleMemory(lease.role).catch(() => {});
        }
      }

      res.json({
        ok: true,
        consumed: lease.consumed_units,
        budget: lease.budget_units,
        exhausted,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/execution/attempts — submit an attempt (create + set outcome)
  router.post('/execution/attempts', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { leaseId, status: attemptStatus, result, error: attemptError, exitCode } = req.body;

      if (!leaseId) {
        res.status(400).json({ error: 'leaseId is required' });
        return;
      }

      const { rows: [lease] } = await client.query(
        'SELECT * FROM execution.leases WHERE id = $1', [leaseId]
      );
      if (!lease) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Lease not found' }); return; }
      if (lease.status !== 'ACTIVE') {
        await client.query('ROLLBACK');
        res.status(400).json({ error: `Lease is not ACTIVE (current: ${lease.status})` });
        return;
      }
      if (new Date(lease.expires_at) < new Date()) {
        await client.query("UPDATE execution.leases SET status = 'EXPIRED' WHERE id = $1", [leaseId]);
        await client.query('COMMIT');
        res.status(400).json({ error: 'Lease has expired' });
        return;
      }

      const finalStatus = attemptStatus || 'SUCCEEDED';
      const now = new Date().toISOString();

      const { rows: [attempt] } = await client.query(
        `INSERT INTO execution.attempts (
          lease_id, request_id, executor_id, status,
          started_at, completed_at, result, error, exit_code
        ) VALUES ($1, $2, $3, $4, $5, $5, $6, $7, $8) RETURNING *`,
        [
          leaseId, lease.request_id, lease.executor_id, finalStatus,
          now, result||{}, attemptError||null, exitCode||null,
        ]
      );

      await client.query('COMMIT');
      res.status(201).json(attempt);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // POST /api/execution/receipts — issue a receipt from an attempt
  router.post('/execution/receipts', async (req: Request, res: Response) => {
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      const { attemptId, type, agentRole, summary, metadata } = req.body;

      if (!attemptId) {
        res.status(400).json({ error: 'attemptId is required' });
        return;
      }

      const { rows: [attempt] } = await client.query(
        'SELECT * FROM execution.attempts WHERE id = $1', [attemptId]
      );
      if (!attempt) { await client.query('ROLLBACK'); res.status(404).json({ error: 'Attempt not found' }); return; }

      const receiptType = type || (attempt.status === 'SUCCEEDED' ? 'EXECUTION_COMPLETE' : 'EXECUTION_FAILED');

      const { rows: [receipt] } = await client.query(
        `INSERT INTO execution.receipts (
          attempt_id, request_id, type, agent_role, summary, metadata
        ) VALUES ($1, $2, $3, $4, $5, $6) RETURNING *`,
        [attemptId, attempt.request_id, receiptType, agentRole||attempt.executor_id, summary||'', metadata||{}]
      );

      await client.query('COMMIT');
      res.status(201).json(receipt);
    } catch (err: any) {
      await client.query('ROLLBACK');
      res.status(500).json({ error: err.message });
    } finally {
      client.release();
    }
  });

  // GET /api/execution/receipts — list receipts
  router.get('/execution/receipts', async (req: Request, res: Response) => {
    try {
      const { requestId, type } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const clauses: string[] = [];
      const filterParams: any[] = [];
      let i = 1;
      if (requestId) { clauses.push(`request_id = $${i++}`); filterParams.push(requestId); }
      if (type) { clauses.push(`type = $${i++}`); filterParams.push(type); }
      const where = clauses.length > 0 ? 'WHERE ' + clauses.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM execution.receipts ${where} ORDER BY issued_at DESC, id DESC LIMIT $${i++} OFFSET $${i}`,
          [...filterParams, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM execution.receipts ${where}`,
          filterParams
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/execution/state — summary of execution domain state
  router.get('/execution/state', async (req: Request, res: Response) => {
    try {
      const { rows: reqs } = await pool.query(
        `SELECT status, count(*) as count FROM execution.requests GROUP BY status ORDER BY status`
      );
      const { rows: leases } = await pool.query(
        `SELECT status, count(*) as count FROM execution.leases GROUP BY status ORDER BY status`
      );
      const { rows: attempts } = await pool.query(
        `SELECT status, count(*) as count FROM execution.attempts GROUP BY status ORDER BY status`
      );
      const { rows: [receiptTotal] } = await pool.query(
        `SELECT count(*) as total FROM execution.receipts`
      );
      const { rows: receiptTypes } = await pool.query(
        `SELECT type, count(*) as count FROM execution.receipts GROUP BY type ORDER BY count DESC`
      );
      res.json({
        requests: reqs,
        leases,
        attempts,
        receipts: { total: Number(receiptTotal.total), byType: receiptTypes },
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  OPEN QUESTIONS
  // ════════════════════════════════════════════════════════════════

  // GET /api/open-questions?requirementId=&candidateId=&status=&entityType=&entityId=
  router.get('/open-questions', async (req: Request, res: Response) => {
    try {
      const { requirementId, candidateId, status, entityType, entityId } = req.query;
      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (requirementId) { clauses.push(`requirement_id = $${i++}`); vals.push(requirementId); }
      if (candidateId) { clauses.push(`candidate_id = $${i++}`); vals.push(candidateId); }
      // Entity-scoped filtering now uses the canonical direct foreign-key
      // columns. The retired junction table only ever contained candidate and
      // requirement links in live data; reject unsupported legacy entity types
      // rather than silently returning an unscoped result.
      if (entityType && entityId) {
        if (entityType !== 'candidate' && entityType !== 'requirement') {
          return res.status(400).json({ error: 'entityType must be candidate or requirement' });
        }
        if (typeof entityId !== 'string' || !isUuid(entityId)) {
          return res.status(400).json({ error: 'entityId must be a UUID' });
        }
        const directColumn = entityType === 'candidate' ? 'candidate_id' : 'requirement_id';
        clauses.push(`oq.${directColumn} = $${i++}`);
        vals.push(entityId);
      }
      if (status) { clauses.push(`status = $${i++}`); vals.push(status); }
      else { clauses.push(`status = 'OPEN'`); }
      const where = 'WHERE ' + clauses.join(' AND ');
      const { rows } = await pool.query(
        `SELECT oq.id, oq.requirement_id, oq.candidate_id, oq.title, oq.description, oq.category,
                oq.status, oq.blocking,
                oq.answered_by, oq.answered_at, oq.created_by, oq.created_at,
                COALESCE(ac.answer_count, 0) AS answer_count,
                COALESCE(ac.role_count, 0) AS role_count
         FROM nebula.open_questions oq
         LEFT JOIN nebula.v_question_answer_counts ac ON ac.question_id = oq.id
         ${where}
         ORDER BY oq.created_at DESC`, vals
      );
      res.json({ questions: rows, count: rows.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/open-questions/:id/answers — list only currently-valid answers
  // Queries the open_question_answers VIEW (which enforces bitemporal
  // filtering). Excludes temporal housekeeping columns from the response.
  router.get('/open-questions/:id/answers', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows } = await pool.query(
        `SELECT id, question_id, role, answer, confidence, reasoning,
                version, answered_at
         FROM nebula.open_question_answers
         WHERE question_id = $1
         ORDER BY version DESC, answered_at DESC`,
        [id]
      );
      res.json({ answers: rows, count: rows.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/open-questions/:id/answers — record answer via stored procedure
  // The procedure handles: expire old answer, version increment, INSERT,
  // answered_by pointer update, and pg_notify('open_question_answered').
  router.post('/open-questions/:id/answers', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { answer, role, confidence, reasoning } = req.body;
      if (!answer || !role) {
        res.status(400).json({ error: 'answer and role are required' });
        return;
      }
      // Verify question exists and is open
      const qCheck = await pool.query(
        `SELECT id, status FROM nebula.open_questions WHERE id = $1`, [id]
      );
      if (qCheck.rows.length === 0) {
        res.status(404).json({ error: 'Question not found' });
        return;
      }

      const { rows } = await pool.query(
        `SELECT out_id AS id, out_question_id AS question_id, out_role AS role,
                out_answer AS answer, out_confidence AS confidence, out_reasoning AS reasoning,
                out_version AS version, out_answered_at AS answered_at
         FROM nebula.record_answer($1, $2, $3, $4, $5)`,
        [id, role, answer, confidence || 'MEDIUM', reasoning || null]
      );
      res.status(201).json(rows[0]);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/open-questions — create a new open question
  router.post('/open-questions', async (req: Request, res: Response) => {
    try {
      const { title, description, category, requirementId, candidateId, blocking, entityType, entityId, createdBy } = req.body;

      const VALID_CATEGORIES = ['AMBIGUITY', 'MISSING_INFO', 'CONFLICT', 'SCOPE', 'DEPENDENCY', 'DUPLICATE_CANDIDATE', 'WORK_COMPLETED'];
      if (!title || !VALID_CATEGORIES.includes(category)) {
        return res.status(400).json({ error: 'title and valid category are required' });
      }
      if ((entityType && !entityId) || (!entityType && entityId)) {
        return res.status(400).json({ error: 'Both entityType and entityId are required' });
      }
      if (entityType && !['candidate', 'requirement'].includes(entityType)) {
        return res.status(400).json({ error: 'entityType must be candidate or requirement' });
      }
      if (entityId && !isUuid(entityId)) {
        return res.status(400).json({ error: 'entityId must be a UUID' });
      }
      if (requirementId && !isUuid(requirementId)) {
        return res.status(400).json({ error: 'requirementId must be a UUID' });
      }
      if (candidateId && !isUuid(candidateId)) {
        return res.status(400).json({ error: 'candidateId must be a UUID' });
      }

      // Normalize legacy IDs
      let linkEntityType = entityType || null;
      let linkEntityId = entityId || null;
      if (!linkEntityType && requirementId) { linkEntityType = 'requirement'; linkEntityId = requirementId; }
      if (!linkEntityType && candidateId) { linkEntityType = 'candidate'; linkEntityId = candidateId; }

      const client = await pool.connect();
      try {
        await client.query('BEGIN');

        const result = await client.query(
          `INSERT INTO nebula.open_questions
           (id, requirement_id, candidate_id, title, description, category, status, blocking, created_by, created_at)
           VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, 'OPEN', $6, $7, NOW())
           RETURNING id`,
          [
            linkEntityType === 'requirement' ? linkEntityId : (requirementId || null),
            linkEntityType === 'candidate' ? linkEntityId : (candidateId || null),
            title,
            description || null,
            category,
            blocking || false,
            createdBy || null,
          ]
        );

        await client.query('COMMIT');
        res.status(201).json({ id: result.rows[0].id });
      } catch (err: any) {
        await client.query('ROLLBACK').catch(() => {});
        throw err;
      } finally {
        client.release();
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/open-questions/:id/answer — legacy single-answer endpoint (backwards compat)
  // Now also inserts into open_question_answers table.
  router.put('/open-questions/:id/answer', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { answer, answeredBy } = req.body;
      if (!answer || !answeredBy) {
        res.status(400).json({ error: 'answer and answeredBy are required' });
        return;
      }
      // Insert into answers table — append-only, versioned (AGENTS.md I4)
      const versionResult = await pool.query(
        `SELECT COALESCE(MAX(version), 0) + 1 AS next_version
         FROM nebula.open_question_answers
         WHERE question_id = $1 AND role = $2`,
        [id, answeredBy]
      );
      const nextVersion = versionResult.rows[0].next_version;

      await pool.query(
        `INSERT INTO nebula.open_question_answers (question_id, role, answer, confidence, reasoning, version)
         VALUES ($1, $2, $3, 'MEDIUM', NULL, $4)`,
        [id, answeredBy, answer, nextVersion]
      );
      // The AFTER INSERT trigger updates open_questions.answered_by automatically.
      const { rows } = await pool.query(
        `UPDATE nebula.open_questions
         SET updated_at = now()
         WHERE id = $1 AND status = 'OPEN'
         RETURNING id, title, status`,
        [id]
      );
      if (rows.length === 0) {
        res.status(404).json({ error: 'Question not found or already closed' });
        return;
      }
      res.json(rows[0]);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/open-questions/:id/resolve
  router.put('/open-questions/:id/resolve', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { resolvedBy } = req.body;
      if (!resolvedBy) {
        res.status(400).json({ error: 'resolvedBy is required' });
        return;
      }
      const { rows } = await pool.query(
        `UPDATE nebula.open_questions
         SET status = 'RESOLVED',
             answered_by = $1,
             answered_at = now(),
             updated_at = now()
         WHERE id = $2 AND status = 'OPEN'
           AND EXISTS (SELECT 1 FROM nebula.open_question_answers WHERE question_id = $2)
         RETURNING id, title, status, answered_by, answered_at`,
        [resolvedBy, id]
      );
      if (rows.length === 0) {
        res.status(404).json({ error: 'Question not found, already closed, or has no answer' });
        return;
      }
      res.json(rows[0]);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  ROLES
  // ════════════════════════════════════════════════════════════════

  // GET /api/roles — list all roles (governance roles with capabilities)
  router.get('/roles', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query('SELECT * FROM nebula.roles ORDER BY name ASC LIMIT $1 OFFSET $2', [pageSize, offset]),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.roles'),
      ]);

      res.json({
        items: dataResult.rows.map((r: any) => ({
          id: r.id,
          name: r.name,
          displayName: r.display_name,
          description: r.description,
          ownsDomains: r.owns_domains,
          canGreenlight: r.can_greenlight,
          canCreateQuestions: r.can_create_questions,
          canCreateAgendas: r.can_create_agendas,
          canResolveQuestions: r.can_resolve_questions,
          canVerifyWorkRequests: r.can_verify_work_requests,
          maxOpenQuestions: r.max_open_questions,
          requiresApprovalFrom: r.requires_approval_from,
          cronEnabled: r.cron_enabled,
          cronExpression: r.cron_expression,
          cronDescription: r.cron_description,
          escalatesTo: r.escalates_to,
          escalationTriggers: r.escalation_triggers,
          levelFilterPrimary: r.level_filter_primary,
          levelFilterAllowed: r.level_filter_allowed,
          visibilityScope: r.visibility_scope,
          createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
          updatedAt: r.updated_at ? new Date(r.updated_at).toISOString() : null,
        })),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/roles/drift — runtime-vs-governance-vs-execution drift check
  // (D-2026-08-16-009 R5). Three planes:
  //   governance = nebula.roles (current bitemporal view) + roles_history
  //   runtime    = tackle.roles (runtime personas)
  //   execution  = tackle.role_leases (ACTIVE leases)
  // Read-only: surfaces drift findings, never silently reconciles. Registered
  // BEFORE /roles/:id so 'drift' is not captured as a role id.
  router.get('/roles/drift', async (_req: Request, res: Response) => {
    try {
      const [govCur, govHist, runtime, exec] = await Promise.all([
        pool.query('SELECT name FROM nebula.roles'),
        pool.query('SELECT DISTINCT name FROM nebula.roles_history'),
        pool.query('SELECT name FROM tackle.roles'),
        pool.query("SELECT role, channel FROM tackle.role_leases WHERE status = 'ACTIVE'"),
      ]);
      const governanceCurrent = new Set<string>(govCur.rows.map((r: any) => r.name));
      const governanceHistory = new Set<string>(govHist.rows.map((r: any) => r.name));
      const runtimePersonas = new Set<string>(runtime.rows.map((r: any) => r.name));

      const findings: Array<{ severity: 'high' | 'info'; type: string; role: string; detail: string }> = [];

      // execution vs governance (and runtime)
      for (const lease of exec.rows) {
        const role = lease.role;
        if (governanceCurrent.has(role)) continue;
        if (governanceHistory.has(role)) {
          findings.push({
            severity: 'high',
            type: 'execution_expired_role',
            role,
            detail: `ACTIVE lease references role '${role}' whose governance window is not current (expired in nebula.roles_history)`,
          });
        } else if (runtimePersonas.has(role)) {
          findings.push({
            severity: 'info',
            type: 'execution_runtime_persona',
            role,
            detail: `ACTIVE lease references runtime persona '${role}' (no governance definition; capability proof = runtime config_bundle)`,
          });
        } else {
          findings.push({
            severity: 'high',
            type: 'execution_missing_role',
            role,
            detail: `ACTIVE lease references role '${role}' with no canonical key (not governance, not runtime persona)`,
          });
        }
      }

      // governance vs runtime (informational)
      for (const role of governanceCurrent) {
        if (!runtimePersonas.has(role)) {
          findings.push({
            severity: 'info',
            type: 'governance_unmaterialized',
            role,
            detail: `governance role '${role}' has no runtime persona in tackle.roles`,
          });
        }
      }

      // runtime vs governance (informational)
      for (const role of runtimePersonas) {
        if (!governanceHistory.has(role)) {
          findings.push({
            severity: 'info',
            type: 'runtime_persona_unlisted',
            role,
            detail: `runtime persona '${role}' has no governance definition in nebula.roles_history`,
          });
        }
      }

      findings.sort((a, b) => {
        if (a.severity !== b.severity) return a.severity === 'high' ? -1 : 1;
        return a.role.localeCompare(b.role);
      });

      res.json({
        checkedAt: new Date().toISOString(),
        summary: {
          governanceRoles: governanceCurrent.size,
          runtimePersonas: runtimePersonas.size,
          activeLeases: exec.rows.length,
          findings: findings.length,
        },
        findings,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/roles/:id — single role
  router.get('/roles/:id', async (req: Request, res: Response) => {
    try {
      const { rows: [role] } = await pool.query(
        'SELECT * FROM nebula.roles WHERE id = $1',
        [req.params.id]
      );
      if (!role) {
        res.status(404).json({ error: 'Role not found' });
        return;
      }
      res.json({
        id: role.id,
        name: role.name,
        displayName: role.display_name,
        description: role.description,
        ownsDomains: role.owns_domains,
        canGreenlight: role.can_greenlight,
        canCreateQuestions: role.can_create_questions,
        canCreateAgendas: role.can_create_agendas,
        canResolveQuestions: role.can_resolve_questions,
        canVerifyWorkRequests: role.can_verify_work_requests,
        maxOpenQuestions: role.max_open_questions,
        requiresApprovalFrom: role.requires_approval_from,
        cronEnabled: role.cron_enabled,
        cronExpression: role.cron_expression,
        cronDescription: role.cron_description,
        escalatesTo: role.escalates_to,
        escalationTriggers: role.escalation_triggers,
        levelFilterPrimary: role.level_filter_primary,
        levelFilterAllowed: role.level_filter_allowed,
        visibilityScope: role.visibility_scope,
        createdAt: role.created_at ? new Date(role.created_at).toISOString() : null,
        updatedAt: role.updated_at ? new Date(role.updated_at).toISOString() : null,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Role row → API JSON (mirrors the GET /roles mapping) ──────────
  const roleToJson = (r: any) => ({
    id: r.id,
    name: r.name,
    displayName: r.display_name,
    description: r.description,
    ownsDomains: r.owns_domains,
    canGreenlight: r.can_greenlight,
    canCreateQuestions: r.can_create_questions,
    canCreateAgendas: r.can_create_agendas,
    canResolveQuestions: r.can_resolve_questions,
    canVerifyWorkRequests: r.can_verify_work_requests,
    maxOpenQuestions: r.max_open_questions,
    requiresApprovalFrom: r.requires_approval_from,
    cronEnabled: r.cron_enabled,
    cronExpression: r.cron_expression,
    cronDescription: r.cron_description,
    escalatesTo: r.escalates_to,
    escalationTriggers: r.escalation_triggers,
    levelFilterPrimary: r.level_filter_primary,
    levelFilterAllowed: r.level_filter_allowed,
    visibilityScope: r.visibility_scope,
    createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
    updatedAt: r.updated_at ? new Date(r.updated_at).toISOString() : null,
  });

  // POST /api/roles — create role metadata (Gap 2: nebula.roles create API)
  router.post('/roles', async (req: Request, res: Response) => {
    try {
      const b = req.body || {};
      if (!b.name || !b.displayName) {
        return res.status(400).json({ error: 'name and displayName are required' });
      }
      if (!/^[a-z_]+$/.test(b.name)) {
        return res.status(400).json({ error: 'name must match ^[a-z_]+$ (lowercase letters and underscores)' });
      }
      const { rows: [role] } = await pool.query(
        `INSERT INTO nebula.roles (
           name, display_name, description, owns_domains,
           can_greenlight, can_create_questions, can_create_agendas,
           can_resolve_questions, can_verify_work_requests,
           max_open_questions, requires_approval_from,
           cron_enabled, cron_expression, cron_description,
           escalates_to, escalation_triggers,
           level_filter_primary, level_filter_allowed, visibility_scope
         ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
         RETURNING *`,
        [
          b.name, b.displayName, b.description ?? null, b.ownsDomains ?? [],
          b.canGreenlight ?? false, b.canCreateQuestions ?? false, b.canCreateAgendas ?? false,
          b.canResolveQuestions ?? false, b.canVerifyWorkRequests ?? false,
          b.maxOpenQuestions ?? null, b.requiresApprovalFrom ?? [],
          b.cronEnabled ?? false, b.cronExpression ?? null, b.cronDescription ?? null,
          b.escalatesTo ?? [], b.escalationTriggers ?? [],
          b.levelFilterPrimary ?? 'level <= 2', b.levelFilterAllowed ?? 'level <= 3',
          b.visibilityScope ?? ['planner', 'all'],
        ]
      );
      await emitRoleLifecycleEvent(pool, 'role.granted', 'nebula-srv.roles', 'role', String(role.id), {
        name: role.name,
        displayName: role.display_name,
        ownsDomains: role.owns_domains,
      });
      res.status(201).json(roleToJson(role));
    } catch (err: any) {
      if (err.code === '23505') {
        return res.status(409).json({ error: `Role '${req.body?.name}' already exists in nebula.roles` });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/roles/:id — update capabilities/visibility/description (Gap 2)
  router.patch('/roles/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const b = req.body || {};
      if (b.name !== undefined && !/^[a-z_]+$/.test(b.name)) {
        return res.status(400).json({ error: 'name must match ^[a-z_]+$ (lowercase letters and underscores)' });
      }
      const sets: string[] = [];
      const vals: any[] = [];
      let i = 1;
      const fieldMap: [string, string][] = [
        ['name', 'name'], ['displayName', 'display_name'], ['description', 'description'],
        ['ownsDomains', 'owns_domains'], ['canGreenlight', 'can_greenlight'],
        ['canCreateQuestions', 'can_create_questions'], ['canCreateAgendas', 'can_create_agendas'],
        ['canResolveQuestions', 'can_resolve_questions'], ['canVerifyWorkRequests', 'can_verify_work_requests'],
        ['maxOpenQuestions', 'max_open_questions'], ['requiresApprovalFrom', 'requires_approval_from'],
        ['cronEnabled', 'cron_enabled'], ['cronExpression', 'cron_expression'],
        ['cronDescription', 'cron_description'], ['escalatesTo', 'escalates_to'],
        ['escalationTriggers', 'escalation_triggers'], ['levelFilterPrimary', 'level_filter_primary'],
        ['levelFilterAllowed', 'level_filter_allowed'], ['visibilityScope', 'visibility_scope'],
      ];
      for (const [camel, col] of fieldMap) {
        if (b[camel] !== undefined) {
          sets.push(`${col} = $${i++}`);
          vals.push(b[camel]);
        }
      }
      if (sets.length === 0) return res.json({ ok: true });
      vals.push(id);
      const { rows: [role] } = await pool.query(
        `UPDATE nebula.roles SET ${sets.join(', ')}, updated_at = NOW() WHERE id = $${i} RETURNING *`,
        vals
      );
      if (!role) return res.status(404).json({ error: 'Role not found' });
      const changedFields = fieldMap
        .filter(([camel]) => b[camel] !== undefined)
        .map(([camel]) => camel);
      await emitRoleLifecycleEvent(pool, 'capability.changed', 'nebula-srv.roles', 'role', String(role.id), {
        name: role.name,
        changedFields,
        ownsDomains: role.owns_domains,
        visibilityScope: role.visibility_scope,
      });
      res.json(roleToJson(role));
    } catch (err: any) {
      if (err.code === '23505') {
        return res.status(409).json({ error: 'Role name already exists in nebula.roles' });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/roles/:id — remove role metadata (Gap 2). Accepts a UUID or
  // a role name (architect review: previously UUID-only). Hard delete guarded:
  // FK references from wind.titles / nebula.roles_history surface as 23503 →
  // 409 with a hint instead of a raw PG error.
  router.delete('/roles/:id', async (req: Request, res: Response) => {
    try {
      const id = String(req.params.id);
      const target = isUuid(id) ? id : null;
      // Capture the role before the V114 INSTEAD OF DELETE soft-expires it
      // (valid_until = now()), so the event payload carries the role name.
      const { rows: [existing] } = target
        ? await pool.query('SELECT id, name FROM nebula.roles WHERE id = $1', [id])
        : await pool.query('SELECT id, name FROM nebula.roles WHERE name = $1', [id]);
      if (!existing) return res.status(404).json({ error: 'Role not found' });
      const { rowCount } = target
        ? await pool.query('DELETE FROM nebula.roles WHERE id = $1', [id])
        : await pool.query('DELETE FROM nebula.roles WHERE name = $1', [id]);
      if (rowCount === 0) return res.status(404).json({ error: 'Role not found' });
      await emitRoleLifecycleEvent(pool, 'role.revoked', 'nebula-srv.roles', 'role', String(existing.id), {
        name: existing.name,
        softExpired: true,
      });
      res.json({ ok: true, id, name: existing.name });
    } catch (err: any) {
      if (err.code === '23503') {
        return res.status(409).json({
          error: 'Role is referenced by other rows (e.g. wind titles) — expire it in nebula.roles_history instead of hard-deleting',
        });
      }
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  ASSESSMENTS
  // ════════════════════════════════════════════════════════════════

  // GET /api/assessments — list with pagination
  router.get('/assessments', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT id, observation_id, outcome, confidence, impact_scope,
                  open_questions, agenda_id, auto_resolve_plan_id,
                  forum_post_id, analysis_detail, created_at
           FROM nebula.assessments
           ORDER BY created_at DESC
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.assessments'),
      ]);

      const items = dataResult.rows.map((r: any) => ({
        id: r.id,
        observationId: r.observation_id,
        outcome: r.outcome,
        confidence: r.confidence != null ? parseFloat(r.confidence) : null,
        impactScope: r.impact_scope,
        openQuestions: r.open_questions,
        agendaId: r.agenda_id,
        autoResolvePlanId: r.auto_resolve_plan_id,
        forumPostId: r.forum_post_id,
        analysisDetail: r.analysis_detail,
        createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
      }));

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/assessments/:id — single assessment
  router.get('/assessments/:id', async (req: Request, res: Response) => {
    try {
      const { rows: [r] } = await pool.query(
        `SELECT id, observation_id, outcome, confidence, impact_scope,
                open_questions, agenda_id, auto_resolve_plan_id,
                forum_post_id, analysis_detail, created_at
         FROM nebula.assessments WHERE id = $1`,
        [req.params.id]
      );
      if (!r) { res.status(404).json({ error: 'Assessment not found' }); return; }
      res.json({
        id: r.id,
        observationId: r.observation_id,
        outcome: r.outcome,
        confidence: r.confidence != null ? parseFloat(r.confidence) : null,
        impactScope: r.impact_scope,
        openQuestions: r.open_questions,
        agendaId: r.agenda_id,
        autoResolvePlanId: r.auto_resolve_plan_id,
        forumPostId: r.forum_post_id,
        analysisDetail: r.analysis_detail,
        createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  OBSERVATIONS
  // ════════════════════════════════════════════════════════════════

  // GET /api/observations — list with pagination
  router.get('/observations', async (req: Request, res: Response) => {
    try {
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT id, trigger_type, source_artifact_type, source_artifact_id,
                  payload, assessed, created_at
           FROM nebula.observations
           ORDER BY created_at DESC
           LIMIT $1 OFFSET $2`,
          [pageSize, offset]
        ),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.observations'),
      ]);

      const items = dataResult.rows.map((r: any) => ({
        id: r.id,
        triggerType: r.trigger_type,
        sourceArtifactType: r.source_artifact_type,
        sourceArtifactId: r.source_artifact_id,
        payload: r.payload,
        assessed: r.assessed,
        createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
      }));

      res.json({ items, total: parseInt(countResult.rows[0].total, 10), page, pageSize, limit, offset });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/observations/:id — single observation
  router.get('/observations/:id', async (req: Request, res: Response) => {
    try {
      const { rows: [r] } = await pool.query(
        `SELECT id, trigger_type, source_artifact_type, source_artifact_id,
                payload, assessed, created_at
         FROM nebula.observations WHERE id = $1`,
        [req.params.id]
      );
      if (!r) { res.status(404).json({ error: 'Observation not found' }); return; }
      res.json({
        id: r.id,
        triggerType: r.trigger_type,
        sourceArtifactType: r.source_artifact_type,
        sourceArtifactId: r.source_artifact_id,
        payload: r.payload,
        assessed: r.assessed,
        createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  OPEN QUESTIONS — detail (list already exists above)
  // ════════════════════════════════════════════════════════════════

  // GET /api/open-questions/:id — single open question
  router.get('/open-questions/:id', async (req: Request, res: Response) => {
    try {
      const { rows: [r] } = await pool.query(
        `SELECT oq.id, oq.requirement_id, oq.candidate_id, oq.title, oq.description,
                oq.category, oq.status, oq.blocking,
                oq.created_by, oq.created_at, oq.updated_at,
                oq.answered_by, oq.answered_at,
                link.entity_type, link.entity_id, link.entity_title
         FROM nebula.open_questions oq
         LEFT JOIN LATERAL (
           SELECT entity_type, entity_id, entity_title
           FROM (
             SELECT 'candidate'::text AS entity_type,
                    oq.candidate_id AS entity_id,
                    (SELECT title FROM nebula.harvest_candidates WHERE id = oq.candidate_id) AS entity_title
             WHERE oq.candidate_id IS NOT NULL
             UNION ALL
             SELECT 'requirement'::text AS entity_type,
                    oq.requirement_id AS entity_id,
                    (SELECT title FROM nebula.requirements WHERE id = oq.requirement_id) AS entity_title
             WHERE oq.requirement_id IS NOT NULL
           ) direct_link
           ORDER BY entity_type
           LIMIT 1
         ) link ON true
         WHERE oq.id = $1`,
        [req.params.id]
      );
      if (!r) { res.status(404).json({ error: 'Open question not found' }); return; }
      res.json({
        id: r.id,
        requirementId: r.requirement_id,
        candidateId: r.candidate_id,
        title: r.title,
        description: r.description,
        category: r.category,
        status: r.status,
        blocking: r.blocking,
        createdBy: r.created_by,
        createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
        updatedAt: r.updated_at ? new Date(r.updated_at).toISOString() : null,
        answeredBy: r.answered_by,
        answeredAt: r.answered_at ? new Date(r.answered_at).toISOString() : null,
        entityType: r.entity_type,
        entityId: r.entity_id,
        entityTitle: r.entity_title,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/open-questions/:id/timeline — deliberation history
  router.get('/open-questions/:id/timeline', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;

      const qResult = await pool.query(
        `SELECT id, title, status, blocking, created_by, created_at FROM nebula.open_questions WHERE id = $1`,
        [id]
      );
      if (qResult.rows.length === 0) {
        return res.status(404).json({ error: 'Question not found' });
      }

      const q = qResult.rows[0];
      const events: any[] = [];

      events.push({
        type: 'created', label: 'Question created', description: q.title,
        timestamp: new Date(q.created_at).toISOString(), actor: q.created_by, icon: 'Circle',
      });
      events.push({
        type: 'status_change', label: `Status: ${q.status}`,
        description: q.blocking ? 'Blocking' : 'Non-blocking',
        timestamp: new Date(q.created_at).toISOString(), actor: null, icon: 'RefreshCw',
      });

      if (q.status === 'RESOLVED') {
        events.push({
          type: 'resolved', label: 'Question resolved', description: null,
          timestamp: new Date(q.created_at).toISOString(), actor: null, icon: 'CheckCircle2',
        });
      }

      // Find related agent records (by question ID in content/title)
      const { rows: agentRows } = await pool.query(
        `SELECT record_type, role, title, created_at FROM nebula.agent_records
         WHERE content ILIKE $1 OR title ILIKE $1 ORDER BY created_at DESC LIMIT 20`,
        [`%${id}%`]
      );
      for (const row of agentRows) {
        events.push({
          type: 'note', label: `${row.record_type} by ${row.role}`, description: row.title,
          timestamp: new Date(row.created_at).toISOString(), actor: row.role, icon: 'FileText',
        });
      }

      events.sort((a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
      res.json(events);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  OPEN QUESTIONS — participants sub-resource
  // ════════════════════════════════════════════════════════════════

  // GET /api/open-questions/:id/participants
  router.get('/open-questions/:id/participants', async (req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT id, open_question_id, role, participated_at, contribution
         FROM nebula.deliberation_participants
         WHERE open_question_id = $1 AND valid_until > now()
         ORDER BY participated_at ASC`,
        [req.params.id]
      );
      res.json({
        openQuestionId: req.params.id,
        participants: rows.map((r: any) => ({
          id: r.id,
          openQuestionId: r.open_question_id,
          role: r.role,
          participatedAt: r.participated_at ? new Date(r.participated_at).toISOString() : null,
          contribution: r.contribution,
        })),
        count: rows.length,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/open-questions/:id/participants
  router.post('/open-questions/:id/participants', async (req: Request, res: Response) => {
    try {
      const { role, contribution } = req.body;
      if (!role) { res.status(400).json({ error: 'role is required' }); return; }
      const { rows: [p] } = await pool.query(
        `INSERT INTO nebula.deliberation_participants
         (open_question_id, role, contribution, participated_at)
         VALUES ($1, $2, $3, now())
         RETURNING id, open_question_id, role, contribution, participated_at`,
        [req.params.id, role, contribution || null]
      );
      res.status(201).json({
        id: p.id,
        openQuestionId: p.open_question_id,
        role: p.role,
        contribution: p.contribution,
        participatedAt: p.participated_at ? new Date(p.participated_at).toISOString() : null,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  CANDIDATE DEPENDENCIES sub-resource
  // ════════════════════════════════════════════════════════════════

  // GET /api/harvest-candidates/:id/dependencies
  router.get('/harvest-candidates/:id/dependencies', async (req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        `SELECT id, candidate_id, depends_on_id, created_at
         FROM nebula.candidate_dependencies
         WHERE candidate_id = $1 AND valid_until > now()
         ORDER BY created_at ASC`,
        [req.params.id]
      );
      res.json({
        candidateId: req.params.id,
        dependencies: rows.map((r: any) => ({
          id: r.id,
          candidateId: r.candidate_id,
          dependsOnId: r.depends_on_id,
          createdAt: r.created_at ? new Date(r.created_at).toISOString() : null,
        })),
        count: rows.length,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SEARCH (cross-entity full-text)
  // ════════════════════════════════════════════════════════════════

  // GET /api/search?q=...
  router.get('/search', async (req: Request, res: Response) => {
    try {
      const q = String(req.query.q || '').trim();
      if (!q || q.length < 2) {
        res.json({ query: q, results: [] });
        return;
      }

      const escapeLike = (value: string) => value.replace(/\\/g, '\\\\').replace(/[%_]/g, '\\$&');
      const pattern = `%${escapeLike(q)}%`;
      const limit = 20;

      const [
        threadResult, requirementResult, agendaResult, candidateResult,
        harvestResult, oqResult, assessmentResult,
        observationResult, agentRecordResult, specificationResult, planResult,
        userResult,
      ] = await Promise.all([
        // Threads
        pool.query(
          `SELECT id, title, text AS body, 'thread' AS result_type FROM assembly.posts
           WHERE title ILIKE $1 ESCAPE '\\' OR text ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Requirements
        pool.query(
          `SELECT id, title, description, status, 'requirement' AS result_type FROM nebula.requirements
           WHERE title ILIKE $1 ESCAPE '\\' OR description ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Agendas
        pool.query(
          `SELECT id, title, planner_analysis AS description, status, 'agenda' AS result_type FROM nebula.agendas
           WHERE title ILIKE $1 ESCAPE '\\' OR planner_analysis ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Harvest candidates
        pool.query(
          `SELECT id, title, intent_description AS description, status, 'candidate' AS result_type FROM nebula.harvest_candidates
           WHERE title ILIKE $1 ESCAPE '\\' OR intent_description ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Harvests
        pool.query(
          `SELECT id, source_filename AS title, source_text AS description, model AS status, 'harvest' AS result_type FROM nebula.harvests
           WHERE source_filename ILIKE $1 ESCAPE '\\' OR source_text ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Open questions
        pool.query(
          `SELECT id, title, description, status, 'open_question' AS result_type FROM nebula.open_questions
           WHERE title ILIKE $1 ESCAPE '\\' OR description ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Assessments
        pool.query(
          `SELECT id, outcome AS title, analysis_detail AS description, outcome AS status, 'assessment' AS result_type FROM nebula.assessments
           WHERE outcome ILIKE $1 ESCAPE '\\' OR analysis_detail ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Observations
        pool.query(
          `SELECT id, trigger_type AS title, payload::text AS description, 'observed' AS status, 'observation' AS result_type FROM nebula.observations
           WHERE trigger_type ILIKE $1 ESCAPE '\\' OR payload::text ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Agent records
        pool.query(
          `SELECT id, title, content AS description, role AS status, 'agent_record' AS result_type FROM nebula.agent_records
           WHERE title ILIKE $1 ESCAPE '\\' OR content ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Specifications
        pool.query(
          `SELECT id, change_summary AS title, revision_type AS description, 'spec' AS status, 'specification' AS result_type FROM nebula.specifications
           WHERE change_summary ILIKE $1 ESCAPE '\\' OR revision_type ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
        // Plans (conduit)
        pool.query(
          `SELECT id, title, goal AS description, COALESCE(derived_status, 'PLAN_CREATE') AS status, 'plan' AS result_type FROM nebula.plan_status
           WHERE id IS NOT NULL AND id != ''
             AND (title ILIKE $1 ESCAPE '\\' OR goal ILIKE $1 ESCAPE '\\' OR content ILIKE $1 ESCAPE '\\')
           LIMIT $2`,
          [pattern, limit]
        ),
        // Assembly users
        pool.query(
          `SELECT id, alias AS title, email AS description, 'user' AS status, 'user' AS result_type FROM assembly.users
           WHERE alias ILIKE $1 ESCAPE '\\' OR email ILIKE $1 ESCAPE '\\' LIMIT $2`,
          [pattern, limit]
        ),
      ]);

      const results = [
        ...threadResult.rows,
        ...requirementResult.rows,
        ...agendaResult.rows,
        ...candidateResult.rows,
        ...harvestResult.rows,
        ...oqResult.rows,
        ...assessmentResult.rows,
        ...observationResult.rows,
        ...agentRecordResult.rows,
        ...specificationResult.rows,
        ...planResult.rows,
        ...userResult.rows,
      ]        .slice(0, 100).map((r: any) => {
        // Route path mapping: DB-friendly result_type → hyphenated URL path
        const routePaths: Record<string, string> = {
          open_question: 'open-questions',
          agent_record: 'agent-records',
        };
        const routePath = routePaths[r.result_type] || `${r.result_type}s`;
        return {
          type: r.result_type,
          id: r.id,
          title: r.title || '',
          description: r.description ? r.description.slice(0, 200) : '',
          status: r.status || null,
          href: `/${routePath}/${r.id}`,
        };
      });

      res.json({ query: q, results, total: results.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  COUNTS (aggregate row counts)
  // ════════════════════════════════════════════════════════════════

  // GET /api/counts
  router.get('/counts', async (_req: Request, res: Response) => {
    try {
      const [
        postsResult, requirementsResult, agendasResult, candidatesResult,
        harvestsResult, oqResult, assessmentsResult,
        observationsResult, agentRecordsResult, specificationsResult, plansResult,
        usersResult, toDoThreadsResult,
      ] = await Promise.all([
        pool.query('SELECT COUNT(*)::int AS total FROM assembly.posts'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.requirements'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.agendas'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.harvest_candidates'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.harvests'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.open_questions'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.assessments'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.observations'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.agent_records'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.specifications'),
        pool.query('SELECT COUNT(*)::int AS total FROM nebula.plan_status WHERE id IS NOT NULL AND id != \'\''),
        pool.query('SELECT COUNT(*)::int AS total FROM assembly.users'),
        pool.query("SELECT COUNT(*)::int AS total FROM assembly.thread_list_v WHERE forum_slug = 'to-do'"),
      ]);

      res.json({
        threads: postsResult.rows[0].total,
        requirements: requirementsResult.rows[0].total,
        agendas: agendasResult.rows[0].total,
        candidates: candidatesResult.rows[0].total,
        harvests: harvestsResult.rows[0].total,
        openQuestions: oqResult.rows[0].total,
        assessments: assessmentsResult.rows[0].total,
        observations: observationsResult.rows[0].total,
        agentRecords: agentRecordsResult.rows[0].total,
        specifications: specificationsResult.rows[0].total,
        plans: plansResult.rows[0].total,
        users: usersResult.rows[0].total,
        toDoThreads: toDoThreadsResult.rows[0].total,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  ARCHITECT SPECS
  // ════════════════════════════════════════════════════════════════

  // GET /api/architect-specs — list with pagination
  router.get('/architect-specs', async (req: Request, res: Response) => {
    try {
      const { requirement_id } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const conditions: string[] = [];
      const params: any[] = [];
      let i = 1;

      if (requirement_id) { conditions.push(`requirement_id = $${i++}`); params.push(requirement_id); }
      const where = conditions.length > 0 ? 'WHERE ' + conditions.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM nebula.architect_specs ${where} ORDER BY created_at DESC LIMIT $${i++} OFFSET $${i}`,
          [...params, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.architect_specs ${where}`,
          params
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/architect-specs/:id — detail
  router.get('/architect-specs/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.architect_specs WHERE id = $1',
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Architect spec not found' });
      res.json(camelCaseRow(row));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/architect-specs — create
  router.post('/architect-specs', async (req: Request, res: Response) => {
    try {
      const { title, requirementId, workRequestId, content, metadata } = req.body;
      if (!title || !requirementId) return res.status(400).json({ error: 'title and requirementId are required' });
      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.architect_specs (title, requirement_id, work_request_id, content, metadata)
         VALUES ($1, $2, $3, $4, $5) RETURNING *`,
        [title, requirementId, workRequestId || null, JSON.stringify(content || {}), JSON.stringify(metadata || {})]
      );
      res.status(201).json(camelCaseRow(row));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/architect-specs/:id
  router.delete('/architect-specs/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query(
        'UPDATE nebula.architect_specs SET valid_until = now() WHERE id = $1 AND valid_until > now()',
        [id]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Architect spec not found' });
      res.json({ ok: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  ARTIFACT PROVENANCE
  // ════════════════════════════════════════════════════════════════

  // GET /api/artifact-provenance — list with pagination
  router.get('/artifact-provenance', async (req: Request, res: Response) => {
    try {
      const { subject_type, subject_id, source_type, source_id } = req.query;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      const conditions: string[] = [];
      const params: any[] = [];
      let i = 1;

      if (subject_type) { conditions.push(`subject_type = $${i++}`); params.push(subject_type); }
      if (subject_id) { conditions.push(`subject_id = $${i++}`); params.push(subject_id); }
      if (source_type) { conditions.push(`source_type = $${i++}`); params.push(source_type); }
      if (source_id) { conditions.push(`source_id = $${i++}`); params.push(source_id); }
      const where = conditions.length > 0 ? 'WHERE ' + conditions.join(' AND ') : '';

      const [dataResult, countResult] = await Promise.all([
        pool.query(
          `SELECT * FROM nebula.artifact_provenance ${where} ORDER BY created_at DESC LIMIT $${i++} OFFSET $${i}`,
          [...params, pageSize, offset]
        ),
        pool.query(
          `SELECT COUNT(*)::int AS total FROM nebula.artifact_provenance ${where}`,
          params
        ),
      ]);

      res.json({
        items: dataResult.rows.map(camelCaseRow),
        total: parseInt(countResult.rows[0].total, 10),
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/artifact-provenance/:id — detail
  router.get('/artifact-provenance/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [row] } = await pool.query(
        'SELECT * FROM nebula.artifact_provenance WHERE id = $1',
        [id]
      );
      if (!row) return res.status(404).json({ error: 'Provenance record not found' });
      res.json(camelCaseRow(row));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/artifact-provenance — create
  router.post('/artifact-provenance', async (req: Request, res: Response) => {
    try {
      const { subjectType, subjectId, sourceType, sourceId, sourceVersion, relationship, metadata } = req.body;
      if (!subjectType || !subjectId || !sourceType || !sourceId) {
        return res.status(400).json({ error: 'subjectType, subjectId, sourceType, and sourceId are required' });
      }
      const { rows: [row] } = await pool.query(
        `INSERT INTO nebula.artifact_provenance
         (subject_type, subject_id, source_type, source_id, source_version, relationship, metadata)
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT ON CONSTRAINT uq_artifact_provenance_pair
         DO UPDATE SET metadata = EXCLUDED.metadata, source_version = EXCLUDED.source_version
         RETURNING *`,
        [subjectType, subjectId, sourceType, sourceId, sourceVersion || null, relationship || 'derived_from', JSON.stringify(metadata || {})]
      );
      res.status(201).json(camelCaseRow(row));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/artifact-provenance/:id
  router.delete('/artifact-provenance/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rowCount } = await pool.query(
        'UPDATE nebula.artifact_provenance SET valid_until = now() WHERE id = $1 AND valid_until > now()',
        [id]
      );
      if (rowCount === 0) return res.status(404).json({ error: 'Provenance record not found' });
      res.json({ expired: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SEMANTIC SEARCH
  // ════════════════════════════════════════════════════════════════

  // POST /api/search/semantic — vector similarity search against knowledge graph
  // Accepts a pre-embedded query vector (768-dim, matching nomic-embed-text)
  // and returns similar entities from knowledge.graph_entity_embeddings.
  router.post('/search/semantic', async (req: Request, res: Response) => {
    try {
      const { queryEmbedding, limit = 10, targetSection } = req.body;

      if (!queryEmbedding || !Array.isArray(queryEmbedding)) {
        return res.status(400).json({ error: 'queryEmbedding (array of 768 floats) is required' });
      }
      if (queryEmbedding.length !== 768) {
        return res.status(400).json({ error: 'queryEmbedding must be a 768-dimensional vector' });
      }

      const resultLimit = Math.min(Math.max(1, parseInt(String(limit), 10) || 10), 100);

      // Format as pgvector string literal: '[0.1,0.2,...]'
      const vectorStr = '[' + queryEmbedding.join(',') + ']';

      const { rows } = await pool.query(
        `SELECT section, entity_id, name, description, similarity
         FROM knowledge.semantic_search($1::vector, $2, $3)`,
        [vectorStr, resultLimit, targetSection || null]
      );

      res.json({
        query: { limit: resultLimit, targetSection: targetSection || null },
        results: rows,
        total: rows.length,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  CPF — COMPILATION READINESS FUNNEL (replaces cpf_api.py port 3108)
  // ════════════════════════════════════════════════════════════════

  // GET /api/cpf — query candidates with readiness scores
  router.get('/cpf', async (req: Request, res: Response) => {
    try {
      const threshold = parseFloat(String(req.query.threshold || '0.7'));
      const candidateId = req.query.candidate as string | undefined;
      const showAll = req.query.all === '1' || req.query.all === 'true';
      const system = req.query.system as string | undefined;
      const subsystem = req.query.subsystem as string | undefined;
      const limit = Math.max(0, parseInt(String(req.query.limit || '0'), 10));
      const offset = Math.max(0, parseInt(String(req.query.offset || '0'), 10));

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (candidateId) {
        clauses.push(`hc.id = $${i++}`);
        vals.push(candidateId);
      } else if (!showAll) {
        clauses.push(`hc.compilation_readiness >= $${i++}`);
        vals.push(threshold);
      }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const { rows } = await pool.query(
        `SELECT hc.id, hc.title, hc.intent_description, hc.status,
                hc.compilation_readiness, hc.completed, hc.tags, hc.type,
                COALESCE(sys.name, '(none)') AS system_name,
                COALESCE(sub.name, '(none)') AS subsystem_name,
                (SELECT count(*)::int FROM nebula.candidate_dependencies cd WHERE cd.candidate_id = hc.id AND cd.valid_until > now()) AS dep_count
         FROM nebula.harvest_candidates hc
         LEFT JOIN nebula.systems sys ON sys.id = hc.system_id
         LEFT JOIN nebula.subsystems sub ON sub.id = hc.subsystem_id
         ${where}
         ORDER BY hc.compilation_readiness DESC NULLS LAST,
                  (hc.type = 'drift') DESC,
                  hc.created_at DESC`,
        vals
      );

      // NOTE: harvest_candidates.compilation_readiness is a NUMERIC column;
      // node-postgres returns those as strings. Coerce so API consumers
      // always get numbers (UI calls .toFixed() on this).
      let data = rows.map((r: any) => ({
        id: r.id,
        title: r.title,
        intent_description: r.intent_description,
        status: r.status,
        compilation_readiness: r.compilation_readiness == null ? null : Number(r.compilation_readiness),
        completed: r.completed,
        tags: r.tags || [],
        type: r.type,
        system_name: r.system_name,
        subsystem_name: r.subsystem_name,
        dep_count: r.dep_count,
        promotable: r.compilation_readiness != null && Number(r.compilation_readiness) >= 0.7,
      }));

      // Hierarchy filter
      if (system) data = data.filter((d: any) => d.system_name?.toLowerCase() === system.toLowerCase());
      if (subsystem) data = data.filter((d: any) => d.subsystem_name?.toLowerCase() === subsystem.toLowerCase());

      const total = data.length;

      // Pagination
      if (limit > 0) {
        data = data.slice(offset, offset + limit);
      }

      res.json({ data, count: total, limit: limit || undefined, offset: offset || undefined });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/cpf/count — readiness band counts
  router.get('/cpf/count', async (req: Request, res: Response) => {
    try {
      const system = req.query.system as string | undefined;
      const subsystem = req.query.subsystem as string | undefined;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (system) { clauses.push(`sys.name = $${i++}`); vals.push(system); }
      if (subsystem) { clauses.push(`sub.name = $${i++}`); vals.push(subsystem); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const { rows } = await pool.query(
        `SELECT
           COUNT(*)::int AS total,
           COUNT(*) FILTER (WHERE hc.compilation_readiness >= 0.7)::int AS ready,
           COUNT(*) FILTER (WHERE hc.status = 'promoted')::int AS promoted,
           COUNT(*) FILTER (WHERE hc.compilation_readiness >= 0.5 AND hc.compilation_readiness < 0.7)::int AS near_miss,
           COUNT(*) FILTER (WHERE hc.compilation_readiness < 0.5 OR hc.compilation_readiness IS NULL)::int AS low
         FROM nebula.harvest_candidates hc
         LEFT JOIN nebula.systems sys ON sys.id = hc.system_id
         LEFT JOIN nebula.subsystems sub ON sub.id = hc.subsystem_id
         ${where}`,
        vals
      );

      res.json(rows[0]);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/cpf/promote — promote a candidate
  router.post('/cpf/promote', async (req: Request, res: Response) => {
    try {
      const candidateId = req.body.candidate_id || req.body.id;
      if (!candidateId) {
        return res.status(400).json({ error: 'candidate_id is required' });
      }

      // Verify candidate exists and is promotable
      const { rows } = await pool.query(
        `SELECT id, title, compilation_readiness, status
         FROM nebula.harvest_candidates WHERE id = $1`,
        [candidateId]
      );
      if (rows.length === 0) {
        return res.status(404).json({ error: 'Candidate not found' });
      }
      const c = rows[0];
      if (c.compilation_readiness == null || c.compilation_readiness < 0.7) {
        return res.status(400).json({ error: 'Candidate is not promotable (CPF < 0.7)', compilation_readiness: c.compilation_readiness });
      }

      // Mark as promoted
      await pool.query(
        `UPDATE nebula.harvest_candidates SET status = 'promoted', updated_at = now() WHERE id = $1`,
        [candidateId]
      );

      res.json({ success: true, message: `Candidate ${candidateId} promoted`, title: c.title });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/refresh-stats — refresh materialized views
  // SECURITY: matviewname is validated against a strict PostgreSQL identifier
  // pattern before interpolation. REFRESH MATERIALIZED VIEW does not accept
  // parameterized identifiers ($1), so we sanitize via regex instead.
  router.post('/refresh-stats', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query(
        "SELECT matviewname FROM pg_matviews WHERE schemaname = 'nebula'"
      );
      // Strict identifier validation: only letters, digits, underscores,
      // starting with a letter or underscore (valid PostgreSQL unquoted ident).
      // This prevents any injection via matviewname interpolation.
      const SAFE_IDENT = /^[a-zA-Z_][a-zA-Z0-9_]*$/;
      const refreshed: string[] = [];
      const skipped: string[] = [];
      const errors: string[] = [];
      for (const row of rows) {
        const name = String(row.matviewname);
        if (!SAFE_IDENT.test(name)) {
          skipped.push(name);
          continue;
        }
        let success = false;
        try {
          // CONCURRENTLY requires a unique index; fall back to a blocking
          // refresh if the matview doesn't have one.
          await pool.query(`REFRESH MATERIALIZED VIEW CONCURRENTLY nebula.${name}`);
          success = true;
        } catch {
          try {
            await pool.query(`REFRESH MATERIALIZED VIEW nebula.${name}`);
            success = true;
          } catch (fallbackErr: any) {
            errors.push(`${name}: ${fallbackErr.message}`);
          }
        }
        if (success) refreshed.push(name);
      }
      res.json({ ok: true, refreshed, skipped, errors });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM INVENTORY (unified cross-schema view)
  // ════════════════════════════════════════════════════════════════

  // GET /api/systems/:id/inventory — unified inventory via asset_relation
  // V076 migration: joins through asset_relation (system OWNS service)
  // instead of the deprecated system_external_ids junction.
  router.get('/systems/:id/inventory', async (req: Request, res: Response) => {
    try {
      const { id } = req.params as { id: string };

      // Validate UUID format before hitting the DB
      const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if (!uuidRe.test(id)) {
        return res.status(400).json({ error: 'invalid_id', message: `'${id}' is not a valid UUID` });
      }

      // 1. Fetch the system (need asset_id for the join)
      const { rows: [sys] } = await pool.query(
        'SELECT id, name, description, path, asset_id FROM systems WHERE id = $1',
        [id],
      );
      if (!sys) return res.status(404).json({ error: 'System not found' });

      // 2. Fetch service assets owned by this system via asset_relation.
      //    Resolve terrain and registry details from the service asset.
      //    V078: registry.services joined via shared asset_id (identity_map
      //    safe pairs now share the same canonical_asset).
      let services: any[] = [];
      try {
        const { rows } = await pool.query(
          `SELECT ar.id AS "relationId", ar.relation_type AS "relationType",
                  ar.effective_at AS "effectiveAt",
                  ca.id AS "assetId", ca.canonical_asset_id AS "canonicalAssetId",
                  ca.asset_kind AS "assetKind",
                  -- terrain layer (matched via asset_id)
                  trs.id AS "terrainId", trs.name AS "terrainName",
                  trs.port AS "terrainPort", trs.status AS "terrainStatus",
                  trs.health_check_url AS "terrainHealthCheckUrl",
                  trs.workspace_path AS "terrainWorkspacePath",
                  trs.is_internal AS "terrainIsInternal",
                  -- registry layer (joined via shared asset_id)
                  rs.id AS "registryId", rs.name AS "registryName",
                  rs.default_port AS "registryPort", rs.status AS "registryStatus",
                  rs.description AS "registryDescription",
                  rs.version AS "registryVersion",
                  rs.repository_url AS "registryRepositoryUrl"
           FROM semantics.asset_relation ar
           JOIN semantics.canonical_asset ca
             ON ca.id = ar.to_asset_id AND ca.expired_at IS NULL
           LEFT JOIN terrain.runnable_services trs
             ON trs.asset_id = ca.id
           LEFT JOIN registry.services rs
             ON rs.asset_id = trs.asset_id AND rs.asset_id IS NOT NULL
           WHERE ar.from_asset_id = $1
             AND ar.expired_at IS NULL
             AND ar.relation_type = 'owns'
           ORDER BY trs.name NULLS LAST`,
          [sys.asset_id]
        );
        services = rows;
      } catch {
        // semantics schema may not be accessible — graceful degrade
      }

      // 3. Assemble: each service gets its resolved layers
      const externalIds = services.map((r: any) => {
        const entry: any = {
          id: r.relationId,
          relationType: r.relationType,
          effectiveAt: r.effectiveAt,
          asset: {
            id: r.assetId,
            canonicalAssetId: r.canonicalAssetId,
            assetKind: r.assetKind,
          },
        };

        if (r.terrainId !== null) {
          entry.terrain = {
            id: r.terrainId, name: r.terrainName, port: r.terrainPort,
            status: r.terrainStatus, healthCheckUrl: r.terrainHealthCheckUrl,
            workspacePath: r.terrainWorkspacePath, isInternal: r.terrainIsInternal,
          };
        }

        if (r.registryId !== null) {
          entry.registry = {
            id: r.registryId, name: r.registryName, port: r.registryPort,
            status: r.registryStatus, description: r.registryDescription,
            version: r.registryVersion, repositoryUrl: r.registryRepositoryUrl,
          };
        }

        return entry;
      });

      // 4. Aggregate counts
      const counts = {
        totalServices: externalIds.length,
        terrainServices: externalIds.filter((e: any) => e.terrain).length,
        registryServices: externalIds.filter((e: any) => e.registry).length,
      };

      res.json({
        system: { id: sys.id, name: sys.name, description: sys.description, path: sys.path },
        externalIds,
        counts,
      });
    } catch (err: any) {
      res.status(500).json({ error: 'inventory_failed', message: err.message });
    }
  });

  // GET /api/inventory — rollup counts for the full hierarchy tree
  // Returns per-node counts (systems/subsystems/features) for tree badges
  // plus global totals. Single query, no per-node N+1.
  router.get('/inventory', async (_req: Request, res: Response) => {
    try {
      // Systems: count subsystems, features, requirements, plans, candidates, ext links
      const { rows: sysRows } = await pool.query(
        `SELECT s.id AS "systemId", s.name AS "systemName",
                COUNT(DISTINCT sub.id)::int AS "subsystemCount",
                COUNT(DISTINCT feat.id)::int AS "featureCount",
                COUNT(DISTINCT f.id)::int AS "folderCount",
                COUNT(DISTINCT req.id)::int AS "reqCount",
                COUNT(DISTINCT ip.id)::int AS "planCount",
                COUNT(DISTINCT hc.id)::int AS "candidateCount",
                COUNT(DISTINCT ar.id)::int AS "extLinkCount"
         FROM systems s
         LEFT JOIN subsystems sub ON sub.system_id = s.id
         LEFT JOIN features feat ON feat.subsystem_id = sub.id
         LEFT JOIN system_folders f ON f.system_id = s.id
         LEFT JOIN requirements req ON req.system_id = s.id
         LEFT JOIN nebula.implementation_plans ip ON ip.requirement_id = req.id
         LEFT JOIN nebula.harvest_candidates hc ON hc.system_id = s.id
         LEFT JOIN semantics.asset_relation ar ON ar.from_asset_id = s.asset_id AND ar.expired_at IS NULL
         GROUP BY s.id, s.name
         ORDER BY s.name`
      );

      // Subsystems: count features, requirements, plans, candidates
      const { rows: subRows } = await pool.query(
        `SELECT sub.id AS "subsystemId", sub.name AS "subsystemName",
                sub.system_id AS "systemId",
                COUNT(DISTINCT feat.id)::int AS "featureCount",
                COUNT(DISTINCT req.id)::int AS "reqCount",
                COUNT(DISTINCT ip.id)::int AS "planCount",
                COUNT(DISTINCT hc.id)::int AS "candidateCount"
         FROM subsystems sub
         LEFT JOIN features feat ON feat.subsystem_id = sub.id
         LEFT JOIN requirements req ON req.subsystem_id = sub.id
         LEFT JOIN nebula.implementation_plans ip ON ip.requirement_id = req.id
         LEFT JOIN nebula.harvest_candidates hc ON hc.subsystem_id = sub.id
         GROUP BY sub.id, sub.name, sub.system_id
         ORDER BY sub.name`
      );

      // Features: count requirements, plans, candidates
      const { rows: featRows } = await pool.query(
        `SELECT feat.id AS "featureId", feat.name AS "featureName",
                feat.subsystem_id AS "subsystemId",
                COUNT(DISTINCT req.id)::int AS "reqCount",
                COUNT(DISTINCT ip.id)::int AS "planCount",
                COUNT(DISTINCT hc.id)::int AS "candidateCount"
         FROM features feat
         LEFT JOIN requirements req ON req.feature_id = feat.id
         LEFT JOIN nebula.implementation_plans ip ON ip.requirement_id = req.id
         LEFT JOIN nebula.harvest_candidates hc ON hc.feature_id = feat.id
         GROUP BY feat.id, feat.name, feat.subsystem_id
         ORDER BY feat.name`
      );

      // Totals
      const totals = {
        systems: sysRows.length,
        subsystems: subRows.length,
        features: featRows.length,
        requirements: sysRows.reduce((sum, r) => sum + (r.reqCount || 0), 0),
        plans: sysRows.reduce((sum, r) => sum + (r.planCount || 0), 0),
        candidates: sysRows.reduce((sum, r) => sum + (r.candidateCount || 0), 0),
      };

      res.json({ systems: sysRows, subsystems: subRows, features: featRows, totals });
    } catch (err: any) {
      res.status(500).json({ error: 'inventory_failed', message: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM EXTERNAL IDS — DEPRECATED (V077 migration)
  //
  //  The system_external_ids junction has been replaced by
  //  asset_relation (system-asset OWNS service-asset).
  //  These endpoints now query asset_relation instead.
  //  Full history remains in system_external_ids_history (append-only).
  // ════════════════════════════════════════════════════════════════

  // GET /api/systems/:id/external-ids — list owned services via asset_relation
  router.get('/systems/:id/external-ids', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { offset, limit, page, pageSize } = parsePagination(req.query);

      // Get system's asset_id first
      const { rows: [sys] } = await pool.query(
        'SELECT asset_id FROM systems WHERE id = $1', [id]
      );
      if (!sys) return res.status(404).json({ error: 'System not found' });

      let items: any[] = [];
      let total = 0;
      try {
        const [dataResult, countResult] = await Promise.all([
          pool.query(
            `SELECT ar.id, ar.relation_type AS "relationType",
                    ar.effective_at AS "effectiveAt",
                    json_build_object('id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                      'assetKind', ca.asset_kind) AS "relatedAsset"
             FROM semantics.asset_relation ar
             JOIN semantics.canonical_asset ca ON ca.id = ar.to_asset_id AND ca.expired_at IS NULL
             WHERE ar.from_asset_id = $1 AND ar.expired_at IS NULL
             ORDER BY ar.relation_type, ar.effective_at DESC
             LIMIT $2 OFFSET $3`,
            [sys.asset_id, pageSize, offset]
          ),
          pool.query(
            'SELECT COUNT(*)::int AS total FROM semantics.asset_relation WHERE from_asset_id = $1 AND expired_at IS NULL',
            [sys.asset_id]
          ),
        ]);
        items = dataResult.rows;
        total = parseInt(countResult.rows[0].total, 10);
      } catch {
        // semantics not available — return empty
      }

      res.json({ items, total, page, pageSize });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/systems/:id/external-ids — create asset_relation edge (deprecated junction)
  router.post('/systems/:id/external-ids', async (req: Request, res: Response) => {
    return res.status(410).json({
      error: 'deprecated',
      message: 'system_external_ids has been replaced by asset_relation. Use POST /api/canonical_asset/:id/external-ids on semantics-srv (port 3160) instead.',
    });
  });

  // DELETE /api/systems/:id/external-ids/:eid — deprecated
  router.delete('/systems/:id/external-ids/:eid', async (req: Request, res: Response) => {
    return res.status(410).json({
      error: 'deprecated',
      message: 'system_external_ids has been replaced by asset_relation. Use DELETE /api/canonical_asset/:id/external-ids/:eid on semantics-srv (port 3160) instead.',
    });
  });

  // GET /api/external-ids — reverse lookup via asset_relation
  router.get('/external-ids', async (req: Request, res: Response) => {
    try {
      const { assetId } = req.query;
      if (!assetId) {
        return res.status(400).json({ error: 'assetId query param is required (migration from sourceSchema/sourceTable/sourceId)' });
      }
      let items: any[] = [];
      try {
        const { rows } = await pool.query(
          `SELECT ar.id, ar.relation_type AS "relationType",
                  ar.effective_at AS "effectiveAt",
                  json_build_object('id', ns.id, 'name', ns.name) AS "system"
           FROM semantics.asset_relation ar
           JOIN nebula.systems ns ON ns.asset_id = ar.from_asset_id
           WHERE ar.to_asset_id = $1 AND ar.expired_at IS NULL`,
          [assetId]
        );
        items = rows;
      } catch {
        // semantics not available
      }
      res.json({ items, total: items.length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PATCH /api/external-ids/:id — deprecated
  router.patch('/external-ids/:id', async (req: Request, res: Response) => {
    return res.status(410).json({
      error: 'deprecated',
      message: 'system_external_ids has been replaced by asset_relation. Use PATCH on semantics-srv (port 3160) for asset_relation updates.',
    });
  });

  return router;
}
