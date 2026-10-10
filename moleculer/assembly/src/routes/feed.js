import { Router } from 'express';
import { pool } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const feedRouter = Router();

feedRouter.get('/', async (_req, res, next) => {
  try {
    const result = await pool.query('SELECT post_id, text, created, user_id, alias, avatar_url, forum_id, forum_slug, forum_name, comment_count FROM assembly.feed_posts_v LIMIT 50');

    const posts = result.rows.map(row => ({
      id: row.post_id,
      title: row.text ? row.text.split('\n')[0].slice(0, 120) : 'Untitled',
      content: row.text || '',
      createdAt: new Date(row.created).toISOString(),
      comments: parseInt(row.comment_count, 10),
      author: {
        id: row.user_id,
        name: row.alias,
        avatar: row.avatar_url || '',
      },
      forum: row.forum_id
        ? { id: row.forum_id, slug: row.forum_slug, name: row.forum_name }
        : null,
    }));

    res.json(posts);
  } catch (err) {
    next(err);
  }
});

feedRouter.delete('/:id', async (req, res, next) => {
  try {
    const { id } = req.params;

    const result = await pool.query(
      'SELECT * FROM assembly.soft_delete_thread($1)',
      [id]
    );

    if (result.rowCount === 0) {
      throw new NotFoundError('Post not found');
    }

    res.json({ id: result.rows[0].id });
  } catch (err) {
    if (err.code === 'P0002') return next(new NotFoundError('Post not found'));
    next(err);
  }
});

feedRouter.post('/', async (req, res, next) => {
  try {
    const { text, postedById } = req.body;
    if (!text || !postedById) {
      throw new BadRequestError('Text and postedById are required');
    }

    const result = await pool.query(
      `INSERT INTO assembly.posts (id, posted_by_id, title, text, created)
       VALUES (gen_random_uuid(), $1, $2, $3, NOW())
       RETURNING id`,
      [postedById, String(text).slice(0, 500), String(text)]
    );

    res.status(201).json({ id: result.rows[0].id });
  } catch (err) {
    next(err);
  }
});
