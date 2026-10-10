import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const receiptsRouter = Router();

// List receipts (optionally filter by ticket)
receiptsRouter.get('/', async (req, res, next) => {
  try {
    const { ticket_id } = req.query;
    let sql = `
      SELECT r.id, r.ticket_id, r.ticket_task_id, r.outcome_id, r.work_request_id,
             r.output_artifact_type, r.output_artifact_id, r.completed_at, r.metadata,
             o.code AS outcome_code, task.name AS task_name
      FROM wind.receipts r
      JOIN wind.task_outcomes o ON r.outcome_id = o.id
      JOIN wind.tasks task ON r.ticket_task_id = task.id
    `;
    const params = [];
    if (ticket_id) {
      sql += ' WHERE r.ticket_id = $1';
      params.push(ticket_id);
    }
    sql += ' ORDER BY r.completed_at DESC';
    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get receipt by ID
receiptsRouter.get('/:id', async (req, res, next) => {
  try {
    const result = await query(
      `SELECT r.id, r.ticket_id, r.ticket_task_id, r.outcome_id, r.work_request_id,
              r.output_artifact_type, r.output_artifact_id, r.completed_at, r.metadata,
              o.code AS outcome_code, task.name AS task_name
       FROM wind.receipts r
       JOIN wind.task_outcomes o ON r.outcome_id = o.id
       JOIN wind.tasks task ON r.ticket_task_id = task.id
       WHERE r.id = $1`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Receipt not found');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});
