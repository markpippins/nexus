import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { pool } from '../db.js';

export const healthRouter = Router();

// TWIN-ONLY EDIT (CodeQL js/missing-rate-limiting, alert #759): this router
// is dead code in the incumbent — healthRouter is exported by routes/index.js
// but never mounted (the incumbent's /health is registered inline in
// index.js). Because it sits outside the app graph, the app-level limiter in
// services/express-app.ts cannot cover it, so the analyzer flags the orphan
// registration. A route-level limiter here is zero-behavior: the router is
// unreachable at runtime in both incumbent and twin. Kept verbatim otherwise
// so the file stays a byte-parity copy.
healthRouter.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: 'draft-7',
    legacyHeaders: false,
  }),
);

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
