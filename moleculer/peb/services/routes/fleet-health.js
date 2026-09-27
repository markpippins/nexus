import { Router } from 'express';
import { pool } from '../db.js';
import { parseTimeWindow } from '../lib/pagination.js';
import { badRequest } from '../errors.js';

export const fleetHealthRouter = Router();

// GET /api/peb/health/circuit-breakers
// Spec: role_circuit_breaker, tripped-first sort
fleetHealthRouter.get('/circuit-breakers', async (_req, res, next) => {
  try {
    const r = await pool.query(
      `SELECT role, tripped, tripped_at, retry_after, error, failure_count,
              updated_at,
              CASE WHEN tripped_at IS NOT NULL
                        AND retry_after IS NOT NULL
                        AND (now() - tripped_at) < make_interval(secs => retry_after)
                   THEN 'OPEN'
                   WHEN tripped_at IS NOT NULL AND tripped > 0
                   THEN 'RECOVERING'
                   ELSE 'CLOSED'
              END AS state
         FROM peb.role_circuit_breaker
        ORDER BY tripped DESC, tripped_at DESC NULLS LAST`
    );
    res.json({ circuit_breakers: r.rows });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/health/violations/summary?window=24h&group_by=severity|violation_type|entity_id
fleetHealthRouter.get('/violations/summary', async (req, res, next) => {
  try {
    const groupBy = String(req.query.group_by ?? 'severity').toLowerCase();
    const valid = ['severity', 'violation_type', 'entity_id'];
    if (!valid.includes(groupBy)) {
      return next(badRequest('group_by must be one of: ' + valid.join(', ')));
    }
    const since = parseTimeWindow(req.query.window);
    const args = [];
    if (since) { args.push(since); }
    const where = since ? `WHERE v.created_at >= $1` : '';
    const r = await pool.query(
      `SELECT v.${groupBy} AS key, count(*)::int AS total,
              count(*) FILTER (WHERE v.resolution = 'resolved')::int AS resolved_total
         FROM peb.violations v
       ${where}
      GROUP BY v.${groupBy}
      ORDER BY total DESC`,
      args
    );
    res.json({ group_by: groupBy, window: req.query.window ?? null, summary: r.rows });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/health/entropy?group_by=entropy_class
// Spec: decisions.entropy_class over time — churn/stability signal.
fleetHealthRouter.get('/entropy', async (req, res, next) => {
  try {
    const groupBy = String(req.query.group_by ?? 'entropy_class').toLowerCase();
    // Only meaningful group_by options for decisions
    const valid = ['entropy_class', 'author_id', 'status'];
    if (!valid.includes(groupBy)) {
      return next(badRequest('group_by must be one of: ' + valid.join(', ')));
    }
    const since = parseTimeWindow(req.query.window);
    const args = [];
    if (since) { args.push(since); }
    const where = since ? `WHERE d.created_at >= $1` : '';
    const r = await pool.query(
      `SELECT d.${groupBy} AS key,
              count(*)::int AS total,
              max(d.created_at) AS last_seen,
              min(d.created_at) AS first_seen
         FROM peb.decisions d
       ${where}
      GROUP BY d.${groupBy}
      ORDER BY total DESC`,
      args
    );
    // Time-bucket rollup as well: counts per day per key, for trend chart.
    // Bind $1 only when a window was supplied; otherwise fall back to the SQL
    // default so we never reference an unbound parameter (previously 500).
    const trendArgs = since ? [since] : [];
    const startExpr = since ? '$1::timestamptz' : "now() - interval '7 days'";
    const trend = await pool.query(
      `SELECT dates.day AS day,
              COALESCE(d.${groupBy}, '_no_data') AS key,
              count(*)::int AS total
         FROM generate_series(
            ${startExpr},
            now(),
            interval '1 day'
         ) AS dates(day)
         LEFT JOIN peb.decisions d
           ON date_trunc('day', d.created_at) = dates.day
       ${since ? 'AND d.created_at >= $1' : ''}
       GROUP BY dates.day, COALESCE(d.${groupBy}, '_no_data')
       ORDER BY dates.day ASC, key ASC`,
      trendArgs
    );
    res.json({ group_by: groupBy, window: req.query.window ?? null, summary: r.rows, trend: trend.rows });
  } catch (err) {
    next(err);
  }
});
