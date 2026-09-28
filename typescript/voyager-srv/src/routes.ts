import { Request, Response, Router } from 'express';
import { Pool } from 'pg';

// ── Helpers ───────────────────────────────────────────────────────
// Exported for tests (src/normaliser.test.ts); behaviour is unchanged.
export function toNumber(v: any, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function camelCaseRow(row: Record<string, any>): Record<string, any> {
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

export function camelCaseRows(rows: Record<string, any>[]): Record<string, any>[] {
  return rows.map(camelCaseRow);
}

export function createRoutes(pool: Pool): Router {
  const router = Router();

  // ════════════════════════════════════════════════════════════════
  //  HEALTH
  // ════════════════════════════════════════════════════════════════

  router.get('/health', async (_req: Request, res: Response) => {
    try {
      const { rows } = await pool.query('SELECT 1 as ok');
      res.json({ status: 'ok', db: rows[0].ok === 1, service: 'voyager-srv' });
    } catch (err: any) {
      res.status(503).json({ status: 'error', message: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  SCAN EPOCHS
  // ════════════════════════════════════════════════════════════════

  // GET /api/scan-epochs — list scan epochs (most recent first)
  router.get('/scan-epochs', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 20)));
      const offset = (page - 1) * pageSize;

      const [countResult, { rows }] = await Promise.all([
        pool.query('SELECT COUNT(*)::int AS total FROM scan_epoch'),
        pool.query(
          'SELECT * FROM scan_epoch ORDER BY started_at DESC LIMIT $1 OFFSET $2',
          [pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/scan-epochs/:id — single scan epoch
  router.get('/scan-epochs/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [epoch] } = await pool.query('SELECT * FROM scan_epoch WHERE id = $1', [id]);
      if (!epoch) return res.status(404).json({ error: 'Scan epoch not found' });
      res.json(camelCaseRow(epoch));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  FILE OBSERVATIONS
  // ════════════════════════════════════════════════════════════════

  // GET /api/observations/files — list file observations (paginated, filterable)
  router.get('/observations/files', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { scanEpochId, path: pathFilter, deviceId, inode } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
      if (pathFilter) { clauses.push(`path ILIKE $${i++}`); vals.push(`%${pathFilter}%`); }
      if (deviceId) { clauses.push(`device_id = $${i++}`); vals.push(deviceId); }
      if (inode) { clauses.push(`inode = $${i++}`); vals.push(inode); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM file_observation ${where}`, vals),
        pool.query(
          `SELECT * FROM file_observation ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/observations/files/by-id/:observationId — lookup by observation_id (UUID)
  // NOTE: must be declared BEFORE /:id to avoid route collision
  router.get('/observations/files/by-id/:observationId', async (req: Request, res: Response) => {
    try {
      const { observationId } = req.params;
      const { rows: [obs] } = await pool.query(
        'SELECT * FROM file_observation WHERE observation_id = $1',
        [observationId]
      );
      if (!obs) return res.status(404).json({ error: 'File observation not found' });
      res.json(camelCaseRow(obs));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/observations/files/:id — single file observation by surrogate id
  router.get('/observations/files/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [obs] } = await pool.query('SELECT * FROM file_observation WHERE id = $1', [id]);
      if (!obs) return res.status(404).json({ error: 'File observation not found' });
      res.json(camelCaseRow(obs));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  DIRECTORY OBSERVATIONS
  // ════════════════════════════════════════════════════════════════

  // GET /api/observations/directories — list directory observations
  router.get('/observations/directories', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { scanEpochId, path: pathFilter } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
      if (pathFilter) { clauses.push(`path ILIKE $${i++}`); vals.push(`%${pathFilter}%`); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM directory_observation ${where}`, vals),
        pool.query(
          `SELECT * FROM directory_observation ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  TOPOLOGY SIGNALS
  // ════════════════════════════════════════════════════════════════

  // GET /api/topology/signals — list topology signals
  router.get('/topology/signals', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { scanEpochId, structureType } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
      if (structureType) { clauses.push(`structure->>'type' = $${i++}`); vals.push(structureType); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM topology_signal ${where}`, vals),
        pool.query(
          `SELECT * FROM topology_signal ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/topology/signals/:id
  router.get('/topology/signals/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [sig] } = await pool.query('SELECT * FROM topology_signal WHERE id = $1', [id]);
      if (!sig) return res.status(404).json({ error: 'Topology signal not found' });
      res.json(camelCaseRow(sig));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  OBSERVATION EDGE HINTS
  // ════════════════════════════════════════════════════════════════

  // GET /api/topology/edge-hints — list observation edge hints
  router.get('/topology/edge-hints', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { evidenceType, minConfidence } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (evidenceType) { clauses.push(`evidence->>'type' = $${i++}`); vals.push(evidenceType); }
      if (minConfidence) { clauses.push(`confidence >= $${i++}`); vals.push(minConfidence); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM observation_edge_hint ${where}`, vals),
        pool.query(
          `SELECT * FROM observation_edge_hint ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Identity candidates removed (T04: Voyager is physical observer only).
  //    Entities, drifts, and requirements also pruned — the claim-making
  //    agent lives above Voyager in the semantics layer.
  //    See legacy/identity.py and legacy/losm.py for aspirational code.

  // ════════════════════════════════════════════════════════════════
  //  ENTITIES
  // ════════════════════════════════════════════════════════════════

  // GET /api/entities — list entities
  router.get('/entities', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { minStability, canonicalPath } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (minStability) { clauses.push(`stability_score >= $${i++}`); vals.push(minStability); }
      if (canonicalPath) { clauses.push(`state->>'canonical_path' ILIKE $${i++}`); vals.push(`%${canonicalPath}%`); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM entity ${where}`, vals),
        pool.query(
          `SELECT * FROM entity ${where} ORDER BY stability_score DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/entities/by-id/:entityId — lookup by entity_id (UUID)
  // NOTE: must be declared BEFORE /:id to avoid route collision
  router.get('/entities/by-id/:entityId', async (req: Request, res: Response) => {
    try {
      const { entityId } = req.params;
      const { rows: [ent] } = await pool.query('SELECT * FROM entity WHERE entity_id = $1', [entityId]);
      if (!ent) return res.status(404).json({ error: 'Entity not found' });

      const { rows: drifts } = await pool.query(
        'SELECT * FROM entity_drift WHERE entity_id = $1 ORDER BY discovered_at DESC',
        [ent.id]
      );

      res.json({
        ...camelCaseRow(ent),
        drifts: camelCaseRows(drifts),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/entities/:id — single entity with drift history
  router.get('/entities/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [ent] } = await pool.query('SELECT * FROM entity WHERE id = $1', [id]);
      if (!ent) return res.status(404).json({ error: 'Entity not found' });

      const { rows: drifts } = await pool.query(
        'SELECT * FROM entity_drift WHERE entity_id = $1 ORDER BY discovered_at DESC',
        [id]
      );

      res.json({
        ...camelCaseRow(ent),
        drifts: camelCaseRows(drifts),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ════════════════════════════════════════════════════════════════
  //  METADATA SPANS
  // ════════════════════════════════════════════════════════════════

  // GET /api/spans — list metadata spans (paginated, filterable)
  router.get('/spans', async (req: Request, res: Response) => {
    try {
      const page = Math.max(1, toNumber(req.query.page, 1));
      const pageSize = Math.min(100, Math.max(1, toNumber(req.query.pageSize, 50)));
      const offset = (page - 1) * pageSize;
      const { spanType, markdownRole, minConfidence, observationId } = req.query;

      const clauses: string[] = [];
      const vals: any[] = [];
      let i = 1;
      if (spanType) { clauses.push(`span_type = $${i++}`); vals.push(spanType); }
      if (markdownRole) { clauses.push(`markdown_role = $${i++}`); vals.push(markdownRole); }
      if (minConfidence) { clauses.push(`confidence >= $${i++}`); vals.push(minConfidence); }
      if (observationId) { clauses.push(`observation_id = $${i++}`); vals.push(observationId); }
      const where = clauses.length > 0 ? `WHERE ${clauses.join(' AND ')}` : '';

      const [countResult, { rows }] = await Promise.all([
        pool.query(`SELECT COUNT(*)::int AS total FROM metadata_span ${where}`, vals),
        pool.query(
          `SELECT id, span_id, observation_id, span_type, text, start_pos, end_pos,
                  confidence, markdown_role, discourse_role, event_candidate,
                  provenance, discovered_at
           FROM metadata_span ${where}
           ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
          [...vals, pageSize, offset]
        ),
      ]);

      res.json({
        items: camelCaseRows(rows),
        total: countResult.rows[0].total,
        page,
        pageSize,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/spans/:id — single metadata span
  router.get('/spans/:id', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const { rows: [span] } = await pool.query(
        `SELECT id, span_id, observation_id, span_type, text, start_pos, end_pos,
                confidence, markdown_role, discourse_role, event_candidate,
                provenance, discovered_at
         FROM metadata_span WHERE id = $1`,
        [id]
      );
      if (!span) return res.status(404).json({ error: 'Metadata span not found' });
      res.json(camelCaseRow(span));
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Requirement candidates removed (T04: claim extraction lives above Voyager).

  // ════════════════════════════════════════════════════════════════
  //  STATS — summary across all voyager tables
  // ════════════════════════════════════════════════════════════════

  router.get('/stats', async (req: Request, res: Response) => {
    try {
      const queries = [
        ['file_observations', 'SELECT COUNT(*)::int FROM file_observation'],
        ['directory_observations', 'SELECT COUNT(*)::int FROM directory_observation'],
        ['topology_signals', 'SELECT COUNT(*)::int FROM topology_signal'],
        ['edge_hints', 'SELECT COUNT(*)::int FROM observation_edge_hint'],
        ['metadata_spans', 'SELECT COUNT(*)::int FROM metadata_span'],
        ['scan_epochs', 'SELECT COUNT(*)::int FROM scan_epoch'],
        ['latest_epoch', 'SELECT id, status, started_at FROM scan_epoch ORDER BY started_at DESC LIMIT 1'],
        ['span_types', "SELECT span_type, COUNT(*)::int FROM metadata_span GROUP BY span_type ORDER BY count DESC"],
      ];

      const stats: Record<string, any> = {};
      for (const [key, sql] of queries) {
        try {
          const { rows } = await pool.query(sql);
          if (key === 'span_types') {
            stats[key] = rows;
          } else if (key === 'latest_epoch') {
            stats[key] = rows.length > 0 ? camelCaseRow(rows[0]) : null;
          } else {
            stats[key] = rows[0]?.count ?? rows[0]?.count ?? 0;
          }
        } catch {
          stats[key] = null;
        }
      }

      res.json(stats);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
