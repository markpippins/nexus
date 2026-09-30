import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const ticketsRouter = Router();

// List tickets (optionally filter by instance, status, or title)
ticketsRouter.get('/', async (req, res, next) => {
  try {
    const { instance_id, status, title_id } = req.query;
    let sql = `
      SELECT t.id, t.workflow_instance_id, t.workflow_version_id, t.node_id, t.node_task_id,
             t.assigned_title_id, t.status, t.input_artifact_type, t.input_artifact_id,
             t.created_at, t.updated_at,
             n.name AS node_name, ti.display_name AS title_name, task.name AS task_name
      FROM wind.tickets t
      JOIN wind.workflow_nodes n ON t.node_id = n.id
      JOIN wind.titles ti ON t.assigned_title_id = ti.id
      JOIN wind.tasks task ON t.node_task_id = task.id
    `;
    const conditions = [];
    const params = [];
    let idx = 1;
    if (instance_id) { conditions.push(`t.workflow_instance_id = $${idx++}`); params.push(instance_id); }
    if (status) { conditions.push(`t.status = $${idx++}`); params.push(status); }
    if (title_id) { conditions.push(`t.assigned_title_id = $${idx++}`); params.push(title_id); }
    if (conditions.length > 0) sql += ' WHERE ' + conditions.join(' AND ');
    sql += ' ORDER BY t.created_at DESC';
    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get ticket by ID
ticketsRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      `SELECT t.id, t.workflow_instance_id, t.workflow_version_id, t.node_id, t.node_task_id,
              t.assigned_title_id, t.status, t.input_artifact_type, t.input_artifact_id,
              t.created_at, t.updated_at,
              n.name AS node_name, ti.display_name AS title_name, task.name AS task_name
       FROM wind.tickets t
       JOIN wind.workflow_nodes n ON t.node_id = n.id
       JOIN wind.titles ti ON t.assigned_title_id = ti.id
       JOIN wind.tasks task ON t.node_task_id = task.id
       WHERE t.id = $1`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Ticket not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Update ticket status (e.g., PENDING → IN_PROGRESS)
ticketsRouter.put('/:id/status', async (req, res, next) => {
  try {
    const { status } = req.body;
    if (!status) throw new BadRequestError('status is required');
    const allowed = ['PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'];
    if (!allowed.includes(status)) throw new BadRequestError(`status must be one of: ${allowed.join(', ')}`);

    const result = await query(
      `UPDATE wind.tickets SET status = $1, updated_at = clock_timestamp()
       WHERE id = $2
       RETURNING id, status, updated_at`,
      [status, req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Ticket not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Cancel a ticket
ticketsRouter.post('/:id/cancel', async (req, res, next) => {
  try {
    const result = await query(
      `UPDATE wind.tickets SET status = 'CANCELLED', updated_at = clock_timestamp()
       WHERE id = $1 AND status IN ('PENDING', 'IN_PROGRESS')
       RETURNING id, status, updated_at`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Ticket not found or not cancellable');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});
