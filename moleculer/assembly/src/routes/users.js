import { Router } from 'express';
import { pool } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const usersRouter = Router();

usersRouter.get('/', async (_req, res, next) => {
  try {
    const result = await pool.query('SELECT id, alias, email, avatar_url, created_at FROM assembly.user_list_v');

    const users = result.rows.map(row => ({
      id: row.id,
      name: row.alias,
      email: row.email || null,
      avatar: row.avatar_url || '',
      createdAt: new Date(row.created_at).toISOString(),
    }));

    res.json(users);
  } catch (err) {
    next(err);
  }
});

usersRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT id, alias, email, avatar_url, created_at FROM assembly.user_by_id_v WHERE id = $1',
      [req.params.id]
    );

    if (result.rows.length === 0) {
      throw new NotFoundError('Not found');
    }

    const row = result.rows[0];
    res.json({
      id: row.id,
      name: row.alias,
      email: row.email || null,
      avatar: row.avatar_url || '',
      createdAt: new Date(row.created_at).toISOString(),
    });
  } catch (err) {
    next(err);
  }
});

// ── Missing routes (migrated from assembly-mcp db.ts) ───────────────

usersRouter.get('/by-alias/:alias', async (req, res, next) => {
  try {
    const result = await pool.query(
      'SELECT id, identifier, admin, alias, email, avatar_url FROM assembly.users WHERE alias = $1',
      [req.params.alias]
    );
    if (result.rows.length === 0) throw new NotFoundError('User not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

usersRouter.post('/', async (req, res, next) => {
  try {
    const { alias, email, password, avatar_url, admin } = req.body;
    if (!alias || !email) throw new BadRequestError('alias and email are required');
    const pwd = password || 'changeme';
    const result = await pool.query(
      'INSERT INTO assembly.users (id, alias, email, password, avatar_url, admin) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5) RETURNING id, identifier, admin, alias, email, avatar_url',
      [alias, email, pwd, avatar_url || null, admin || false]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});
