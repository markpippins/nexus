import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';
import { normalizeRequirements, insertRequirement } from '../requirements.js';

export const nodesRouter = Router();

// List nodes for a version
nodesRouter.get('/', async (req, res, next) => {
  try {
    const { version_id } = req.query;
    if (!version_id) throw new BadRequestError('version_id query parameter is required');
    const result = await query(
      `SELECT n.id, n.workflow_version_id, n.task_id, n.name, n.is_entrypoint, n.is_terminal, n.created_at,
              t.name AS task_name
       FROM wind.workflow_nodes n
       JOIN wind.tasks t ON n.task_id = t.id
       WHERE n.workflow_version_id = $1
       ORDER BY n.name`,
      [version_id]
    );
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get node by ID
nodesRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      `SELECT n.id, n.workflow_version_id, n.task_id, n.name, n.is_entrypoint, n.is_terminal, n.created_at,
              t.name AS task_name, t.input_spec
       FROM wind.workflow_nodes n
       JOIN wind.tasks t ON n.task_id = t.id
       WHERE n.id = $1`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Node not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Create node — optionally with demands: `requirements` accepts capability
// keys (string or {capabilityKey}) and/or role credentials ({roleCredential}).
// Demands land in wind.node_requirements (V181); open-interval unique
// indexes fork-proof the set. Seeding failures do NOT roll back the node:
// the demand set is data, and the soak reports demand-side anomalies.
nodesRouter.post('/', async (req, res, next) => {
  try {
    const { workflow_version_id, task_id, name, is_entrypoint, is_terminal, requirements } = req.body;
    if (!workflow_version_id || !task_id || !name) {
      throw new BadRequestError('workflow_version_id, task_id, and name are required');
    }
    const result = await query(
      `INSERT INTO wind.workflow_nodes (workflow_version_id, task_id, name, is_entrypoint, is_terminal)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING id, workflow_version_id, task_id, name, is_entrypoint, is_terminal, created_at`,
      [workflow_version_id, task_id, name, is_entrypoint || false, is_terminal || false]
    );
    const node = result.rows[0];
    const demands = normalizeRequirements(requirements);
    const inserted = [];
    const failed = [];
    for (const demand of demands) {
      try {
        inserted.push(await insertRequirement(node.id, demand));
      } catch (e) {
        failed.push({ demand, error: String(e.message || e) });
      }
    }
    res.status(201).json({ ...node, requirements: inserted, requirements_failed: failed });
  } catch (err) { next(err); }
});

// Update node
nodesRouter.put('/:id', async (req, res, next) => {
  try {
    const { name, is_entrypoint, is_terminal } = req.body;
    const sets = [];
    const params = [];
    let idx = 1;
    if (name !== undefined) { sets.push(`name = $${idx++}`); params.push(name); }
    if (is_entrypoint !== undefined) { sets.push(`is_entrypoint = $${idx++}`); params.push(is_entrypoint); }
    if (is_terminal !== undefined) { sets.push(`is_terminal = $${idx++}`); params.push(is_terminal); }
    if (sets.length === 0) {
      const r = await query('SELECT id, name, is_entrypoint, is_terminal FROM wind.workflow_nodes WHERE id = $1', [req.params.id]);
      if (r.rows.length === 0) throw new NotFoundError('Node not found');
      return res.json(r.rows[0]);
    }
    params.push(req.params.id);
    const result = await query(
      `UPDATE wind.workflow_nodes SET ${sets.join(', ')} WHERE id = $${idx} RETURNING id, workflow_version_id, task_id, name, is_entrypoint, is_terminal, created_at`,
      params
    );
    if (result.rows.length === 0) throw new NotFoundError('Node not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Delete node
nodesRouter.delete('/:id', async (req, res, next) => {
  try {
    const result = await query('DELETE FROM wind.workflow_nodes WHERE id = $1 RETURNING id, name', [req.params.id]);
    if (result.rows.length === 0) throw new NotFoundError('Node not found');
    res.json({ deleted: true, id: result.rows[0].id, name: result.rows[0].name });
  } catch (err) { next(err); }
});
