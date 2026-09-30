import { Router } from 'express';
import { pool } from '../db.js';
import { fetchNebula } from '../utils/fetchNebula.js';

export const searchRouter = Router();

searchRouter.get('/', async (req, res, next) => {
  try {
    const q = String(req.query.q || '').trim();
    if (!q || q.length < 2) {
      return res.json({ query: q, results: [] });
    }

    const limit = 20;

    // Delegate nebula-side search to nebula-srv
    const nebulaResponse = await fetchNebula('/search', { q });

    // Assembly-local searches via stored procedures
    const [forumResult, threadResult, commentResult] = await Promise.all([
      pool.query('SELECT * FROM assembly.search_forums($1, $2)', [q, limit]),
      pool.query('SELECT * FROM assembly.search_posts($1, $2)', [q, limit]),
      pool.query('SELECT * FROM assembly.search_comments($1, $2)', [q, limit]),
    ]);

    const assemblyResults = [
      ...forumResult.rows.map(row => ({
        type: 'forum',
        id: row.id,
        title: row.name,
        description: row.description || '',
        href: `/forums/${row.slug}`,
      })),
      ...threadResult.rows.map(row => ({
        type: 'post',
        id: row.id,
        title: row.title,
        description: row.body ? row.body.slice(0, 200) : '',
        href: `/forums/${row.forum_slug}/${row.id}`,
      })),
      ...commentResult.rows.map(row => ({
        type: 'post',
        id: row.id,
        title: row.thread_title,
        description: row.body ? row.body.slice(0, 200) : '',
        href: `/forums/${row.forum_slug}/${row.thread_id}`,
      })),
    ];

    // Merge nebula results with assembly-local results, cap at 100
    const allResults = [
      ...(nebulaResponse.results || []),
      ...assemblyResults,
    ].slice(0, 100);

    res.json({ query: q, results: allResults, total: allResults.length });
  } catch (err) {
    next(err);
  }
});
