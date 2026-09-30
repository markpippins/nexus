import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const outcomesRouter = Router();

// List outcomes for a task
outcomesRouter.get('/', async (req, res, next) => {
  try {
    const { task_id } = req.query;
    if (!task_id) throw new BadRequestError('task_id query parameter is required');
    const result = await query(
      'SELECT id, task_id, code, description, output_spec, created_at FROM wind.task_outcomes WHERE task_id = $1 ORDER BY code',
      [task_id]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get outcome by ID
outcomesRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      'SELECT id, task_id, code, description, output_spec, created_at FROM wind.task_outcomes WHERE id = $1',
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Outcome not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Create outcome
outcomesRouter.post('/', async (req, res, next) => {
  try {
    const { task_id, code, description, output_spec } = req.body;
    if (!task_id || !code) throw new BadRequestError('task_id and code are required');
    const result = await query(
      `INSERT INTO wind.task_outcomes (task_id, code, description, output_spec)
       VALUES ($1, $2, $3, $4)
       RETURNING id, task_id, code, description, output_spec, created_at`,
      [task_id, code, description || null, output_spec || {}]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete outcome
outcomesRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.task_outcomes WHERE id = $1 RETURNING id, code', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Outcome not found');
    res.json({ deleted: true, id: result.rows[0].id, code: result.rows[0].code });
  } catch (err) { next(err); }
});
