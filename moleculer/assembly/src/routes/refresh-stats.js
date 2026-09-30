import { Router } from 'express';
import { pool } from '../db.js';

export const refreshStatsRouter = Router();

function isValidIdentifier(name) {
  return /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name);
}

refreshStatsRouter.post('/', async (_req, res, next) => {
  try {
    const result = await pool.query(
      `SELECT matviewname AS name
       FROM pg_matviews
       WHERE schemaname = 'nebula'`
    );

    for (const row of result.rows) {
      if (!isValidIdentifier(row.name)) continue;
      await pool.query(`REFRESH MATERIALIZED VIEW nebula."${row.name}"`).catch(() => {});
    }

    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});
