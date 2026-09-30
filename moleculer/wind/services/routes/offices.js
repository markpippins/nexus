import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const officesRouter = Router();

// List all offices
officesRouter.get('/', async (_req, res, next) => {
  try {
    const result = await query(
      'SELECT id, name, description, created_at FROM wind.offices ORDER BY name'
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get office by ID
officesRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      'SELECT id, name, description, created_at FROM wind.offices WHERE id = $1',
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Office not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Create office
officesRouter.post('/', async (req, res, next) => {
  try {
    const { name, description } = req.body;
    if (!name) throw new BadRequestError('name is required');
    const result = await query(
      'INSERT INTO wind.offices (name, description) VALUES ($1, $2) RETURNING id, name, description, created_at',
      [name, description || null]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Update office
officesRouter.put('/:id', async (req, res, next) => {
  try {
    const { name, description } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (name !== undefined) { sets.push(`name = $${idx++}`); params.push(name); }
    if (description !== undefined) { sets.push(`description = $${idx++}`); params.push(description); }
    if (sets.length === 0) {
      const r = await query('SELECT id, name, description, created_at FROM wind.offices WHERE id = $1', [req.params.id]);
      if (r.rows.length === 0) throw new NotFoundError('Office not found');
      return res.json(r.rows[0]);
    }
    params.push(req.params.id);
    const result = await query(
      `UPDATE wind.offices SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, name, description, created_at`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Office not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete office (cascade deletes titles, tasks, outcomes)
officesRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.offices WHERE id = $1 RETURNING id, name', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Office not found');
    res.json({ deleted: true, id: result.rows[0].id, name: result.rows[0].name });
  } catch (err) { next(err); }
});
