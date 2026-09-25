import { Router } from 'express';
import { pool } from '../db.js';

export const healthRouter = Router();

healthRouter.get('/', async (_req, res, next) => {
  try {
    const result = await pool.query(
      `SELECT
        (SELECT COUNT(*)::int FROM peb.governance_events)    AS event_count,
        (SELECT COUNT(*)::int FROM peb.transactions)         AS transaction_count,
        (SELECT COUNT(*)::int FROM peb.violations)            AS violation_count,
        (SELECT COUNT(*)::int FROM peb.decisions)             AS decision_count,
        (SELECT COUNT(*)::int FROM peb.traces)               AS trace_count,
        (SELECT COUNT(*)::int FROM peb.role_circuit_breaker
                                       WHERE tripped > 0)     AS circuit_breakers_tripped`
    );
    res.json({
      status: 'healthy',
      counts: result.rows[0],
    });
  } catch (err) {
    next(err);
  }
});
