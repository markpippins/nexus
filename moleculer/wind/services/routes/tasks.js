import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const tasksRouter = Router();

// List tasks (optionally filter by office)
tasksRouter.get('/', async (req, res, next) => {
  try {
    const { office_id } = req.query;
    let sql = `
      SELECT t.id, t.office_id, t.title_id, t.tackle_task_id, t.name, t.description, t.input_spec, t.created_at,
             o.name AS office_name, ti.display_name AS title_name
      FROM wind.tasks t
      JOIN wind.offices o ON t.office_id = o.id
      JOIN wind.titles ti ON t.title_id = ti.id
    `;
    const params = [];
    if (office_id) {
      sql += ' WHERE t.office_id = $1';
      params.push(office_id);
    }
    sql += ' ORDER BY o.name, t.name';
    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get task by ID (with outcomes)
tasksRouter.get('/:id', async (req, res, next) => {
  try {
    const taskResult = await query(`
      SELECT t.id, t.office_id, t.title_id, t.tackle_task_id, t.name, t.description, t.input_spec, t.created_at,
             o.name AS office_name, ti.display_name AS title_name
      FROM wind.tasks t
      JOIN wind.offices o ON t.office_id = o.id
      JOIN wind.titles ti ON t.title_id = ti.id
      WHERE t.id = $1
    `, [req.params.id]);
    if (taskResult.rows.length === 0) throw new NotFoundError('Task not found');

    const outcomesResult = await query(
      'SELECT id, code, description, output_spec, created_at FROM wind.task_outcomes WHERE task_id = $1 ORDER BY code',
      [req.params.id]
    );

    res.json({
      ...taskResult.rows[0],
      outcomes: outcomesResult.rows,
    });
  } catch (err) { next(err); }
});

// Create task
tasksRouter.post('/', async (req, res, next) => {
  try {
    const { office_id, title_id, tackle_task_id, name, description, input_spec } = req.body;
    if (!office_id || !title_id || !name) {
      throw new BadRequestError('office_id, title_id, and name are required');
    }
    const result = await query(
      `INSERT INTO wind.tasks (office_id, title_id, tackle_task_id, name, description, input_spec)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, office_id, title_id, tackle_task_id, name, description, input_spec, created_at`,
      [office_id, title_id, tackle_task_id || null, name, description || null, input_spec || {}]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Update task
tasksRouter.put('/:id', async (req, res, next) => {
  try {
    const { name, description, input_spec, tackle_task_id } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (name !== undefined) { sets.push(`name = $${idx++}`); params.push(name); }
    if (description !== undefined) { sets.push(`description = $${idx++}`); params.push(description); }
    if (input_spec !== undefined) { sets.push(`input_spec = $${idx++}`); params.push(input_spec); }
    if (tackle_task_id !== undefined) { sets.push(`tackle_task_id = $${idx++}`); params.push(tackle_task_id); }
    if (sets.length === 0) {
      const r = await query('SELECT id, name, description, input_spec, tackle_task_id, created_at FROM wind.tasks WHERE id = $1', [req.params.id]);
      if (r.rows.length === 0) throw new NotFoundError('Task not found');
      return res.json(r.rows[0]);
    }
    params.push(req.params.id);
    const result = await query(
      `UPDATE wind.tasks SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, office_id, title_id, tackle_task_id, name, description, input_spec, created_at`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Task not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete task
tasksRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.tasks WHERE id = $1 RETURNING id, name', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Task not found');
    res.json({ deleted: true, id: result.rows[0].id, name: result.rows[0].name });
  } catch (err) { next(err); }
});
