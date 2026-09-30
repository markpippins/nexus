import { Router } from 'express';
import { fetchNebula } from '../utils/fetchNebula.js';
import { pool } from '../db.js';

export const countsRouter = Router();

countsRouter.get('/', async (req, res, next) => {
  try {
    // Delegate nebula counts to nebula-srv
    const nebulaCounts = await fetchNebula('/counts');

    // Assembly-local counts
    const [forumsResult, postsResult] = await Promise.all([
      pool.query("SELECT COUNT(*)::int AS total FROM assembly.forums WHERE expiration_dt = 'infinity'::timestamptz OR expiration_dt > now()"),
      pool.query("SELECT COUNT(*)::int AS total FROM assembly.posts WHERE expiration_dt = 'infinity'::timestamptz OR expiration_dt > now()"),
    ]);

    res.json({
      ...nebulaCounts,
      forums: forumsResult.rows[0].total,
      posts: postsResult.rows[0].total,
    });
  } catch (err) {
    next(err);
  }
});
