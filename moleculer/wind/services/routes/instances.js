import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError, NotFoundError } from '../errors.js';

export const instancesRouter = Router();

// ── Harness integration ─────────────────────────────────────────────

// M2 repoint: default execution surface is the broker gateway's worker.harness
// port (POST /api/workers/harness/run) — same request/response contract as the
// legacy direct call (wind_task_id in, job_id/role/exit_code/stdout/stderr/
// duration_ms/outcome(s) out; admission denials come back as 200 with an
// `error` field on both surfaces).
// Rollback: set HARNESS_SURFACE=legacy to call harness-srv directly again
// (base :3420, path /run). An explicit HARNESS_URL always wins as the base
// URL for the selected surface.
const HARNESS_SURFACE = (process.env.HARNESS_SURFACE || 'broker').toLowerCase();
const HARNESS_URL = process.env.HARNESS_URL ||
  (HARNESS_SURFACE === 'legacy' ? 'http://127.0.0.1:3420' : 'http://127.0.0.1:4080');
const HARNESS_RUN_PATH = HARNESS_SURFACE === 'legacy' ? '/run' : '/api/workers/harness/run';

/** Which execution surface this process is wired to (ops introspection + tests). */
export const harnessEndpoint = {
  surface: HARNESS_SURFACE,
  baseUrl: HARNESS_URL,
  runPath: HARNESS_RUN_PATH,
};

/**
 * Call the harness execution surface (broker worker.harness by default) to
 * execute a task. Returns the harness result (exit_code, stdout, stderr,
 * role, task).
 */
async function callHarness(windTaskId, overrides) {
  const resp = await fetch(`${HARNESS_URL}${HARNESS_RUN_PATH}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wind_task_id: windTaskId, ...overrides }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Harness call failed (${resp.status}): ${body}`);
  }
  return resp.json();
}

// List instances (optionally filter by status or workflow_id)
instancesRouter.get('/', async (req, res, next) => {
  try {
    const { status, workflow_id } = req.query;
    let sql = `
      SELECT wi.id, wi.workflow_version_id, wi.status, wi.created_at, wi.updated_at,
             w.name AS workflow_name, wv.version_number
      FROM wind.workflow_instances wi
      JOIN wind.workflow_versions wv ON wi.workflow_version_id = wv.id
      JOIN wind.workflows w ON wv.workflow_id = w.id
    `;
    const conditions = [];
    const params = [];
    let idx = 1;
    if (status) { conditions.push(`wi.status = $${idx++}`); params.push(status); }
    if (workflow_id) { conditions.push(`wv.workflow_id = $${idx++}`); params.push(workflow_id); }
    if (conditions.length > 0) sql += ' WHERE ' + conditions.join(' AND ');
    sql += ' ORDER BY wi.created_at DESC';
    const result = await query(sql, params);
    res.json(result.rows);
  } catch (err) { next(err); }
});

// Get instance by ID (with tickets)
instancesRouter.get('/:id', async (req, res, next) => {
  try {
    const instResult = await query(`
      SELECT wi.id, wi.workflow_version_id, wi.status, wi.created_at, wi.updated_at,
             w.name AS workflow_name, wv.version_number
      FROM wind.workflow_instances wi
      JOIN wind.workflow_versions wv ON wi.workflow_version_id = wv.id
      JOIN wind.workflows w ON wv.workflow_id = w.id
      WHERE wi.id = $1
    `, [req.params.id]);
    if (instResult.rows.length === 0) throw new NotFoundError('Instance not found');

    const ticketsResult = await query(
      `SELECT t.id, t.status, t.input_artifact_type, t.input_artifact_id, t.created_at, t.updated_at,
              n.name AS node_name, ti.display_name AS title_name
       FROM wind.tickets t
       JOIN wind.workflow_nodes n ON t.node_id = n.id
       JOIN wind.titles ti ON t.assigned_title_id = ti.id
       WHERE t.workflow_instance_id = $1
       ORDER BY t.created_at`,
      [req.params.id]
    );

    res.json({
      ...instResult.rows[0],
      tickets: ticketsResult.rows,
    });
  } catch (err) { next(err); }
});

// Start a workflow instance
// Creates an instance and tickets for the entrypoint node(s)
instancesRouter.post('/', async (req, res, next) => {
  try {
    const { workflow_version_id } = req.body;
    if (!workflow_version_id) throw new BadRequestError('workflow_version_id is required');

    // Verify version exists
    const verResult = await query(
      'SELECT id, workflow_id FROM wind.workflow_versions WHERE id = $1',
      [workflow_version_id]
    );
    if (verResult.rows.length === 0) throw new NotFoundError('Workflow version not found');

    // Create instance
    const instResult = await query(
      `INSERT INTO wind.workflow_instances (workflow_version_id, status)
       VALUES ($1, 'ACTIVE')
       RETURNING id, workflow_version_id, status, created_at, updated_at`,
      [workflow_version_id]
    );
    const instance = instResult.rows[0];

    // Find entrypoint nodes
    const entryResult = await query(
      `SELECT id, task_id FROM wind.workflow_nodes
       WHERE workflow_version_id = $1 AND is_entrypoint = true`,
      [workflow_version_id]
    );

    if (entryResult.rows.length === 0) {
      // No entrypoint — mark instance as failed
      await query(
        "UPDATE wind.workflow_instances SET status = 'FAILED', updated_at = clock_timestamp() WHERE id = $1",
        [instance.id]
      );
      throw new BadRequestError('No entrypoint node found in workflow version');
    }

    // Create tickets for each entrypoint node
    const tickets = [];
    for (const node of entryResult.rows) {
      // Find the title bound to this node's task
      const titleResult = await query(
        'SELECT title_id FROM wind.tasks WHERE id = $1',
        [node.task_id]
      );
      if (titleResult.rows.length === 0) continue;

      const ticketResult = await query(
        `INSERT INTO wind.tickets
         (workflow_instance_id, workflow_version_id, node_id, node_task_id, assigned_title_id,
          input_artifact_type, input_artifact_id, status)
         VALUES ($1, $2, $3, $4, $5, 'workflow_start', $1, 'PENDING')
         RETURNING id, status, created_at`,
        [instance.id, workflow_version_id, node.id, node.task_id, titleResult.rows[0].title_id]
      );
      tickets.push(ticketResult.rows[0]);
    }

    res.status(201).json({ ...instance, tickets });
  } catch (err) { next(err); }
});

// Pause an instance
instancesRouter.post('/:id/pause', async (req, res, next) => {
  try {
    const result = await query(
      `UPDATE wind.workflow_instances
       SET status = 'PAUSED', updated_at = clock_timestamp()
       WHERE id = $1 AND status = 'ACTIVE'
       RETURNING id, status, updated_at`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Instance not found or not active');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Resume a paused instance
instancesRouter.post('/:id/resume', async (req, res, next) => {
  try {
    const result = await query(
      `UPDATE wind.workflow_instances
       SET status = 'ACTIVE', updated_at = clock_timestamp()
       WHERE id = $1 AND status = 'PAUSED'
       RETURNING id, status, updated_at`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Instance not found or not paused');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Stop (cancel) an instance
instancesRouter.post('/:id/stop', async (req, res, next) => {
  try {
    const result = await query(
      `UPDATE wind.workflow_instances
       SET status = 'FAILED', updated_at = clock_timestamp()
       WHERE id = $1 AND status IN ('ACTIVE', 'PAUSED')
       RETURNING id, status, updated_at`,
      [req.params.id]
    );
    if (result.rows.length === 0) throw new NotFoundError('Instance not found or not stoppable');
    res.json(result.rows[0]);
  } catch (err) { next(err); }
});

// Execute — run the harness for a ticket's task
instancesRouter.post('/:id/execute', async (req, res, next) => {
  try {
    const { ticket_id, context } = req.body;
    if (!ticket_id) throw new BadRequestError('ticket_id is required');

    // Verify instance is active
    const instResult = await query(
      "SELECT id, workflow_version_id FROM wind.workflow_instances WHERE id = $1 AND status = 'ACTIVE'",
      [req.params.id]
    );
    if (instResult.rows.length === 0) throw new NotFoundError('Instance not found or not active');

    // Get the ticket and its task
    const ticketResult = await query(
      `SELECT t.id, t.node_id, t.node_task_id, t.status, t.input_artifact_id,
              n.name as node_name
       FROM wind.tickets t
       JOIN wind.workflow_nodes n ON t.node_id = n.id
       WHERE t.id = $1 AND t.workflow_instance_id = $2`,
      [ticket_id, req.params.id]
    );
    if (ticketResult.rows.length === 0) throw new NotFoundError('Ticket not found');
    const ticket = ticketResult.rows[0];

    if (ticket.status !== 'PENDING') {
      throw new BadRequestError(`Ticket is ${ticket.status}, not PENDING`);
    }

    // Mark ticket as in-progress
    await query(
      "UPDATE wind.tickets SET status = 'IN_PROGRESS', updated_at = clock_timestamp() WHERE id = $1",
      [ticket_id]
    );

    // Call harness-srv
    const harnessResult = await callHarness(ticket.node_task_id, {
      context: context || {},
      work_dir: process.env.HARNESS_WORK_DIR || '/home/codex/dev',
    });

    res.json({
      ticket_id,
      node_name: ticket.node_name,
      harness: {
        job_id: harnessResult.job_id,
        role: harnessResult.role,
        exit_code: harnessResult.exit_code,
        stdout: harnessResult.stdout,
        stderr: harnessResult.stderr,
        duration_ms: harnessResult.duration_ms,
      },
      outcome: harnessResult.outcome,
      outcomes: harnessResult.outcomes || [],
    });
  } catch (err) { next(err); }
});

// Advance — complete a ticket with an outcome, create next tickets
// This is the core workflow traversal step
instancesRouter.post('/:id/advance', async (req, res, next) => {
  try {
    const { ticket_id, outcome_id } = req.body;
    if (!ticket_id || !outcome_id) throw new BadRequestError('ticket_id and outcome_id are required');

    // Verify instance is active
    const instResult = await query(
      "SELECT id, workflow_version_id, status FROM wind.workflow_instances WHERE id = $1 AND status = 'ACTIVE'",
      [req.params.id]
    );
    if (instResult.rows.length === 0) throw new NotFoundError('Instance not found or not active');
    const instance = instResult.rows[0];

    // Verify ticket belongs to this instance and is completable
    const ticketResult = await query(
      `SELECT id, node_id, node_task_id, status
       FROM wind.tickets
       WHERE id = $1 AND workflow_instance_id = $2 AND status IN ('PENDING', 'IN_PROGRESS')`,
      [ticket_id, req.params.id]
    );
    if (ticketResult.rows.length === 0) throw new NotFoundError('Ticket not found or not completable');
    const ticket = ticketResult.rows[0];

    // Verify outcome belongs to the ticket's task
    const outcomeResult = await query(
      'SELECT id, code FROM wind.task_outcomes WHERE id = $1 AND task_id = $2',
      [outcome_id, ticket.node_task_id]
    );
    if (outcomeResult.rows.length === 0) throw new NotFoundError('Outcome not valid for this ticket\'s task');
    const outcome = outcomeResult.rows[0];

    // Complete the ticket
    await query(
      "UPDATE wind.tickets SET status = 'COMPLETED', updated_at = clock_timestamp() WHERE id = $1",
      [ticket_id]
    );

    // Create receipt
    await query(
      `INSERT INTO wind.receipts (ticket_id, ticket_task_id, outcome_id, work_request_id)
       VALUES ($1, $2, $3, $4)`,
      [ticket.id, ticket.node_task_id, outcome_id, ticket_id]
    );

    // Find edges from this node with this outcome
    const edgeResult = await query(
      `SELECT to_node_id FROM wind.workflow_edges
       WHERE workflow_version_id = $1 AND from_node_id = $2 AND outcome_id = $3`,
      [instance.workflow_version_id, ticket.node_id, outcome_id]
    );

    const newTickets = [];

    if (edgeResult.rows.length === 0) {
      // No outgoing edges — check if this is a terminal node
      const nodeResult = await query(
        'SELECT is_terminal FROM wind.workflow_nodes WHERE id = $1',
        [ticket.node_id]
      );
      if (nodeResult.rows.length > 0 && nodeResult.rows[0].is_terminal) {
        // Check if all tickets for this instance are completed
        const pendingResult = await query(
          `SELECT COUNT(*) AS pending
           FROM wind.tickets
           WHERE workflow_instance_id = $1 AND status IN ('PENDING', 'IN_PROGRESS')`,
          [req.params.id]
        );
        if (parseInt(pendingResult.rows[0].pending) === 0) {
          await query(
            "UPDATE wind.workflow_instances SET status = 'COMPLETED', updated_at = clock_timestamp() WHERE id = $1",
            [req.params.id]
          );
        }
      }
    } else {
      // Create tickets for downstream nodes
      for (const edge of edgeResult.rows) {
        const nodeResult = await query(
          'SELECT task_id FROM wind.workflow_nodes WHERE id = $1',
          [edge.to_node_id]
        );
        if (nodeResult.rows.length === 0) continue;

        const titleResult = await query(
          'SELECT title_id FROM wind.tasks WHERE id = $1',
          [nodeResult.rows[0].task_id]
        );
        if (titleResult.rows.length === 0) continue;

        const newTicketResult = await query(
          `INSERT INTO wind.tickets
           (workflow_instance_id, workflow_version_id, node_id, node_task_id, assigned_title_id,
            input_artifact_type, input_artifact_id, status)
           VALUES ($1, $2, $3, $4, $5, 'from_outcome', $6, 'PENDING')
           RETURNING id, status, created_at`,
          [instance.id, instance.workflow_version_id, edge.to_node_id, nodeResult.rows[0].task_id,
           titleResult.rows[0].title_id, ticket_id]
        );
        newTickets.push(newTicketResult.rows[0]);
      }
    }

    res.json({
      ticket_id,
      outcome: outcome.code,
      new_tickets: newTickets,
    });
  } catch (err) { next(err); }
});

// ── Run — fully automatic: execute → advance → execute → ... until terminal ──

/**
 * Run an instance to completion.
 *
 * Body:
 *   max_steps?: number     — max nodes to execute (default: 10, safety cap)
 *   timeout_ms?: number    — per-node harness timeout (default: 120000)
 *
 * The run loop:
 *   1. Find next PENDING ticket for this instance
 *   2. Execute it via harness-srv
 *   3. If outcome parsed, advance the ticket
 *   4. Repeat until no PENDING tickets or instance is COMPLETED
 *   5. Return the full execution log
 */
// Run an instance to completion (loop: execute → advance → … until terminal).
instancesRouter.post('/:id/run', async (req, res, next) => {
  try {
    const { max_steps = 10, timeout_ms = 120_000 } = req.body;
    const stepLog = [];

    // Verify instance is active
    let instResult = await query(
      "SELECT id, workflow_version_id, status FROM wind.workflow_instances WHERE id = $1 AND status = 'ACTIVE'",
      [req.params.id]
    );
    if (instResult.rows.length === 0) throw new NotFoundError('Instance not found or not active');
    const workflowVersionId = instResult.rows[0].workflow_version_id;

    for (let step = 0; step < max_steps; step++) {
      // Re-check instance status
      instResult = await query(
        "SELECT status FROM wind.workflow_instances WHERE id = $1",
        [req.params.id]
      );
      if (instResult.rows[0].status !== 'ACTIVE') {
        stepLog.push({ step, action: 'instance_not_active', status: instResult.rows[0].status });
        break;
      }

      // Find next PENDING ticket
      const ticketResult = await query(
        `SELECT t.id, t.node_id, t.node_task_id, n.name as node_name
         FROM wind.tickets t
         JOIN wind.workflow_nodes n ON t.node_id = n.id
         WHERE t.workflow_instance_id = $1 AND t.status = 'PENDING'
         ORDER BY t.created_at ASC LIMIT 1`,
        [req.params.id]
      );

      if (ticketResult.rows.length === 0) {
        stepLog.push({ step, action: 'no_pending_tickets' });
        break;
      }

      const ticket = ticketResult.rows[0];

      // Mark ticket as IN_PROGRESS
      await query(
        "UPDATE wind.tickets SET status = 'IN_PROGRESS', updated_at = clock_timestamp() WHERE id = $1",
        [ticket.id]
      );

      // Execute via harness-srv
      const harnessResult = await callHarness(ticket.node_task_id, {
        work_dir: process.env.HARNESS_WORK_DIR || '/home/codex/dev',
        timeout_ms,
      });

      const outcome = harnessResult.outcome;
      stepLog.push({
        step,
        action: 'execute',
        node_name: ticket.node_name,
        role: harnessResult.role,
        exit_code: harnessResult.exit_code,
        outcome: outcome ? outcome.code : null,
        confidence: outcome ? outcome.confidence : null,
        duration_ms: harnessResult.duration_ms,
      });

      // If no outcome parsed, stop — workflow is blocked
      if (!outcome) {
        // Reset ticket to PENDING so it can be retried
        await query(
          "UPDATE wind.tickets SET status = 'PENDING', updated_at = clock_timestamp() WHERE id = $1",
          [ticket.id]
        );
        stepLog.push({ step, action: 'blocked_no_outcome' });
        break;
      }

      // Advance the ticket
      await query(
        "UPDATE wind.tickets SET status = 'COMPLETED', updated_at = clock_timestamp() WHERE id = $1",
        [ticket.id]
      );
      await query(
        `INSERT INTO wind.receipts (ticket_id, ticket_task_id, outcome_id, work_request_id)
         VALUES ($1, $2, $3, $4)`,
        [ticket.id, ticket.node_task_id, outcome.id, ticket.id]
      );

      // Find edges from this node with this outcome
      const edgeResult = await query(
        `SELECT to_node_id FROM wind.workflow_edges
         WHERE workflow_version_id = $1 AND from_node_id = $2 AND outcome_id = $3`,
        [workflowVersionId, ticket.node_id, outcome.id]
      );

      const newTickets = [];
      if (edgeResult.rows.length === 0) {
        // No outgoing edges — check if terminal
        const nodeResult = await query(
          'SELECT is_terminal FROM wind.workflow_nodes WHERE id = $1',
          [ticket.node_id]
        );
        if (nodeResult.rows.length > 0 && nodeResult.rows[0].is_terminal) {
          const pendingResult = await query(
            `SELECT COUNT(*) AS pending FROM wind.tickets
             WHERE workflow_instance_id = $1 AND status IN ('PENDING', 'IN_PROGRESS')`,
            [req.params.id]
          );
          if (parseInt(pendingResult.rows[0].pending) === 0) {
            await query(
              "UPDATE wind.workflow_instances SET status = 'COMPLETED', updated_at = clock_timestamp() WHERE id = $1",
              [req.params.id]
            );
          }
        }
        stepLog.push({ step, action: 'terminal_or_no_edges' });
        break;
      } else {
        // Create tickets for downstream nodes
        for (const edge of edgeResult.rows) {
          const nodeResult = await query(
            'SELECT task_id FROM wind.workflow_nodes WHERE id = $1',
            [edge.to_node_id]
          );
          if (nodeResult.rows.length === 0) continue;

          const titleResult = await query(
            'SELECT title_id FROM wind.tasks WHERE id = $1',
            [nodeResult.rows[0].task_id]
          );
          if (titleResult.rows.length === 0) continue;

          const newTicketResult = await query(
            `INSERT INTO wind.tickets
             (workflow_instance_id, workflow_version_id, node_id, node_task_id, assigned_title_id,
              input_artifact_type, input_artifact_id, status)
             VALUES ($1, $2, $3, $4, $5, 'from_outcome', $6, 'PENDING')
             RETURNING id, status`,
            [req.params.id, workflowVersionId, edge.to_node_id,
             nodeResult.rows[0].task_id, titleResult.rows[0].title_id, ticket.id]
          );
          newTickets.push(newTicketResult.rows[0]);
        }
        stepLog.push({ step, action: 'advance', outcome: outcome.code, new_tickets: newTickets.length });
      }
    }

    // Get final instance status
    const finalResult = await query(
      'SELECT status FROM wind.workflow_instances WHERE id = $1',
      [req.params.id]
    );

    res.json({
      instance_id: req.params.id,
      final_status: finalResult.rows[0].status,
      steps_executed: stepLog.length,
      steps: stepLog,
    });
  } catch (err) { next(err); }
});
