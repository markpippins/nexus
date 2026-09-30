import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const edgesRouter = Router();

// List edges for a version
edgesRouter.get('/', async (req, res, next) => {
  try {
    const { version_id } = req.query;
    if (!version_id) throw new BadRequestError('version_id query parameter is required');
    const result = await query(
      `SELECT e.id, e.workflow_version_id, e.from_node_id, e.from_task_id, e.outcome_id, e.to_node_id, e.created_at,
              nf.name AS from_node_name, nt.name AS to_node_name, o.code AS outcome_code
       FROM wind.workflow_edges e
       JOIN wind.workflow_nodes nf ON e.from_node_id = nf.id
       JOIN wind.workflow_nodes nt ON e.to_node_id = nt.id
       JOIN wind.task_outcomes o ON e.outcome_id = o.id
       WHERE e.workflow_version_id = $1
       ORDER BY nf.name, o.code`,
      [version_id]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Create edge
edgesRouter.post('/', async (req, res, next) => {
  try {
    const { workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id } = req.body;
    if (!workflow_version_id || !from_node_id || !from_task_id || !outcome_id || !to_node_id) {
      throw new BadRequestError('workflow_version_id, from_node_id, from_task_id, outcome_id, and to_node_id are required');
    }
    const result = await query(
      `INSERT INTO wind.workflow_edges (workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING id, workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id, created_at`,
      [workflow_version_id, from_node_id, from_task_id, outcome_id, to_node_id]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete edge
edgesRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.workflow_edges WHERE id = $1 RETURNING id', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Edge not found');
    res.json({ deleted: true, id: result.rows[0].id });
  } catch (err) { next(err); }
});
