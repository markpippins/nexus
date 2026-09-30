import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const workflowsRouter = Router();

// List all workflows
workflowsRouter.get('/', async (_req, res, next) => {
  try {
    const result = await query(`
      SELECT w.id, w.name, w.description, w.created_at,
             (SELECT COUNT(*) FROM wind.workflow_versions v WHERE v.workflow_id = w.id) AS version_count,
             (SELECT v2.version_number FROM wind.workflow_versions v2
              WHERE v2.workflow_id = w.id AND v2.is_active = true LIMIT 1) AS active_version
      FROM wind.workflows w
      ORDER BY w.name
    `);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get workflow by ID (with versions)
workflowsRouter.get('/:id', async (req, res, next) => {
  try {
    const wfResult = await query(
      'SELECT id, name, description, created_at FROM wind.workflows WHERE id = $1',
      [req.params.id]
    );
    if (wfResult.rows.length === 0) throw new NotFoundError('Workflow not found');

    const versionsResult = await query(
      'SELECT id, workflow_id, version_number, is_active, created_at FROM wind.workflow_versions WHERE workflow_id = $1 ORDER BY version_number',
      [req.params.id]
    );

    res.json({
      ...wfResult.rows[0],
      versions: versionsResult.rows,
    });
  } catch (err) { next(err); }
});

// Create workflow
workflowsRouter.post('/', async (req, res, next) => {
  try {
    const { name, description } = req.body;
    if (!name) throw new BadRequestError('name is required');
    const result = await query(
      'INSERT INTO wind.workflows (name, description) VALUES ($1, $2) RETURNING id, name, description, created_at',
      [name, description || null]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Update workflow
workflowsRouter.put('/:id', async (req, res, next) => {
  try {
    const { name, description } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (name !== undefined) { sets.push(`name = $${idx++}`); params.push(name); }
    if (description !== undefined) { sets.push(`description = $${idx++}`); params.push(description); }
    if (sets.length === 0) {
      const r = await query('SELECT id, name, description, created_at FROM wind.workflows WHERE id = $1', [req.params.id]);
      if (r.rows.length === 0) throw new NotFoundError('Workflow not found');
      return res.json(r.rows[0]);
    }
    params.push(req.params.id);
    const result = await query(
      `UPDATE wind.workflows SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, name, description, created_at`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Workflow not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete workflow
workflowsRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.workflows WHERE id = $1 RETURNING id, name', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Workflow not found');
    res.json({ deleted: true, id: result.rows[0].id, name: result.rows[0].name });
  } catch (err) { next(err); }
});
