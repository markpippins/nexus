import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const versionsRouter = Router();

// List versions for a workflow
versionsRouter.get('/', async (req, res, next) => {
  try {
    const { workflow_id } = req.query;
    if (!workflow_id) throw new BadRequestError('workflow_id query parameter is required');
    const result = await query(
      'SELECT id, workflow_id, version_number, is_active, created_at FROM wind.workflow_versions WHERE workflow_id = $1 ORDER BY version_number',
      [workflow_id]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get version by ID (with nodes and edges)
versionsRouter.get('/:id', async (req, res, next) => {
  try {
    const verResult = await query(
      'SELECT id, workflow_id, version_number, is_active, created_at FROM wind.workflow_versions WHERE id = $1',
      [req.params.id]
    );
    if (verResult.rows.length === 0) throw new NotFoundError('Version not found');

    const nodesResult = await query(
      `SELECT n.id, n.workflow_version_id, n.task_id, n.name, n.is_entrypoint, n.is_terminal, n.created_at,
              t.name AS task_name
       FROM wind.workflow_nodes n
       JOIN wind.tasks t ON n.task_id = t.id
       WHERE n.workflow_version_id = $1
       ORDER BY n.name`,
      [req.params.id]
    );

    const edgesResult = await query(
      `SELECT e.id, e.workflow_version_id, e.from_node_id, e.from_task_id, e.outcome_id, e.to_node_id, e.created_at,
              nf.name AS from_node_name, nt.name AS to_node_name, o.code AS outcome_code
       FROM wind.workflow_edges e
       JOIN wind.workflow_nodes nf ON e.from_node_id = nf.id
       JOIN wind.workflow_nodes nt ON e.to_node_id = nt.id
       JOIN wind.task_outcomes o ON e.outcome_id = o.id
       WHERE e.workflow_version_id = $1
       ORDER BY nf.name, o.code`,
      [req.params.id]
    );

    res.json({
      ...verResult.rows[0],
      nodes: nodesResult.rows,
      edges: edgesResult.rows,
    });
  } catch (err) { next(err); }
});

// Create version (auto-increments version_number)
versionsRouter.post('/', async (req, res, next) => {
  try {
    const { workflow_id } = req.body;
    if (!workflow_id) throw new BadRequestError('workflow_id is required');

    // Get max version number
    const maxResult = await query(
      'SELECT COALESCE(MAX(version_number), 0) AS max_ver FROM wind.workflow_versions WHERE workflow_id = $1',
      [workflow_id]
    );
    const nextVer = maxResult.rows[0].max_ver + 1;

    const result = await query(
      'INSERT INTO wind.workflow_versions (workflow_id, version_number) VALUES ($1, $2) RETURNING id, workflow_id, version_number, is_active, created_at',
      [workflow_id, nextVer]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) { next(err); }
});

// Activate a version (deactivate all others for that workflow)
versionsRouter.post('/:id/activate', async (req, res, next) => {
  try {
    const verResult = await query(
      'SELECT id, workflow_id FROM wind.workflow_versions WHERE id = $1',
      [req.params.id]
    );
    if (verResult.rows.length === 0) throw new NotFoundError('Version not found');

    const wfId = verResult.rows[0].workflow_id;

    // Deactivate all versions for this workflow
    await query(
      'UPDATE wind.workflow_versions SET is_active = false WHERE workflow_id = $1',
      [wfId]
    );

    // Activate this version
    const result = await query(
      'UPDATE wind.workflow_versions SET is_active = true WHERE id = $1 RETURNING id, workflow_id, version_number, is_active, created_at',
      [req.params.id]
    );
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete version
versionsRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query(
      'DELETE FROM wind.workflow_versions WHERE id = $1 RETURNING id, version_number',
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Version not found');
    res.json({ deleted: true, id: result.rows[0].id, version_number: result.rows[0].version_number });
  } catch (err) { next(err); }
});
