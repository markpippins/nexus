// ── Event processor ─────────────────────────────────────────────────
//
// Polls unconsumed events from wind.events, computes dedup keys,
// creates workflow instances, and emits agent record notifications.
//
// Dual-path architecture:
//   Real-time: NATS subscription handles events immediately.
//   Recovery:  This processor polls events that were missed (lag > 5s).

import { pool } from './db.js';
import { config } from './config.js';

// ── State ───────────────────────────────────────────────────────────

let pollingInterval = null;
let isProcessing = false;

// ── Agent record notification ──────────────────────────────────────
//
// NOTE (2026-08-29, architect): inbox-tagging agent records on every
// processed event floods role inboxes (the "Wind: event harvest.created
// → instance ..." spam). The event/instance/ticket data is already
// durable in the `wind` schema (wind.events, wind.workflow_instances,
// wind.tickets) and visible via cascade/nebula/assembly. We therefore do
// NOT emit `to:<role>` inbox-routed agent records here anymore. The
// function is retained (disabled) to document the previous behavior; if
// role-directed outbox notification is ever needed, re-enable with a
// non-inbox visibility scope rather than the `to:<role>` tag.

/**
 * (Disabled) Post a nebula agent record notification for a role's inbox.
 * Previously tagged records with `to:<role>` for inbox routing, which
 * flooded inboxes on high-volume events such as `harvest.created`.
 */
// eslint-disable-next-line no-unused-vars
async function notifyRole(event, instanceId, ticketIds, role) {
  // Intentionally inert: see NOTE above. Kept so call sites remain valid
  // and the durable inventory of work (tickets/instances) is retained
  // without inbox noise.
  void event; void instanceId; void ticketIds; void role;
}

// ── Core processing ─────────────────────────────────────────────────

/**
 * Process a single event: compute dedup key, start instance, create tickets,
 * mark as consumed, and notify roles.
 */
async function processEvent(event, client) {
  // 1. Look up event type binding
  const etResult = await client.query(
    'SELECT workflow_id, dedup_key_template FROM wind.event_types WHERE event_type = $1',
    [event.event_type]
  );
  if (etResult.rows.length === 0) {
    console.warn(`[event-processor] No event_type registered: ${event.event_type}`);
    await client.query(
      'UPDATE wind.events SET consumed_at = clock_timestamp() WHERE id = $1',
      [event.id]
    );
    return;
  }

  const et = etResult.rows[0];
  if (!et.workflow_id) {
    console.warn(`[event-processor] No workflow bound to event_type: ${event.event_type} (${event.id})`);
    await client.query(
      'UPDATE wind.events SET consumed_at = clock_timestamp() WHERE id = $1',
      [event.id]
    );
    return;
  }

  // 2. Compute dedup key
  let dedupKey = null;
  if (et.dedup_key_template) {
    try {
      const path = et.dedup_key_template.replace(/^\$\./, '');
      dedupKey = path.split('.').reduce((obj, key) => obj?.[key], event.payload || {});
      if (dedupKey !== undefined && dedupKey !== null) {
        dedupKey = String(dedupKey);
      }
    } catch {
      dedupKey = null;
    }
  }

  // 3. Check for existing instance with same dedup key
  if (dedupKey) {
    const existing = await client.query(
      'SELECT id FROM wind.workflow_instances WHERE dedup_key = $1 AND workflow_version_id = (SELECT id FROM wind.workflow_versions WHERE workflow_id = $2 ORDER BY version_number DESC LIMIT 1) LIMIT 1',
      [dedupKey, et.workflow_id]
    );
    if (existing.rows.length > 0) {
      console.log(`[event-processor] Dedup match: event ${event.event_type}(${dedupKey}) → existing instance ${existing.rows[0].id}`);
      await client.query(
        'UPDATE wind.events SET consumed_at = clock_timestamp() WHERE id = $1',
        [event.id]
      );
      return;
    }
  }

  // 4. Find the latest active version of the workflow
  const wvResult = await client.query(
    "SELECT id FROM wind.workflow_versions WHERE workflow_id = $1 ORDER BY version_number DESC LIMIT 1",
    [et.workflow_id]
  );
  if (wvResult.rows.length === 0) {
    console.warn(`[event-processor] No versions for workflow ${et.workflow_id}`);
    return;
  }
  const workflowVersionId = wvResult.rows[0].id;

  // 5. Create instance (with dedup_key and event_id)
  const instResult = await client.query(
    `INSERT INTO wind.workflow_instances
     (workflow_version_id, status, dedup_key, event_id)
     VALUES ($1, 'ACTIVE', $2, $3)
     RETURNING id`,
    [workflowVersionId, dedupKey, event.id]
  );
  const instanceId = instResult.rows[0].id;
  console.log(`[event-processor] Created instance ${instanceId} for ${event.event_type}`);

  // 6. Find entrypoint nodes
  const entryResult = await client.query(
    `SELECT id, task_id FROM wind.workflow_nodes
     WHERE workflow_version_id = $1 AND is_entrypoint = true`,
    [workflowVersionId]
  );

  const ticketIds = [];
  if (entryResult.rows.length > 0) {
    for (const node of entryResult.rows) {
      // Find the title bound to this node's task
      const titleResult = await client.query(
        'SELECT title_id FROM wind.tasks WHERE id = $1',
        [node.task_id]
      );
      if (titleResult.rows.length === 0) continue;

      // Find the v_role associated with this title for notification routing
      const vRoleResult = await client.query(
        `SELECT vr.name FROM wind.v_roles vr
         JOIN wind.titles ti ON ti.role_id = vr.id
         WHERE ti.id = $1`,
        [titleResult.rows[0].title_id]
      );
      const assignedRole = vRoleResult.rows.length > 0 ? vRoleResult.rows[0].name : null;

      const ticketResult = await client.query(
        `INSERT INTO wind.tickets
         (workflow_instance_id, workflow_version_id, node_id, node_task_id, assigned_title_id,
          input_artifact_type, input_artifact_id, status)
         VALUES ($1, $2, $3, $4, $5, 'event', $6, 'PENDING')
         RETURNING id`,
        [instanceId, workflowVersionId, node.id, node.task_id,
         titleResult.rows[0].title_id, event.id]
      );
      ticketIds.push(ticketResult.rows[0].id);

      // Notify the assigned role
      if (assignedRole) {
        await notifyRole(event, instanceId, [ticketResult.rows[0].id], assignedRole);
      }
    }
  }

  // 7. Mark event as consumed
  await client.query(
    'UPDATE wind.events SET consumed_at = clock_timestamp() WHERE id = $1',
    [event.id]
  );

  console.log(`[event-processor] Processed ${event.event_type} → instance ${instanceId} (${ticketIds.length} tickets)`);
}

// ── Poll cycle ──────────────────────────────────────────────────────

/**
 * One poll cycle: fetch unconsumed events from DB and process them
 * in a transaction.
 */
async function pollCycle() {
  if (isProcessing) return;
  isProcessing = true;

  const client = await pool.connect();
  try {
    // Fetch unconsumed events (with recovery lag to let real-time path handle first)
    const eventsResult = await client.query(
      `SELECT id, event_type, payload, source, metadata, created_at
       FROM wind.events
       WHERE consumed_at IS NULL
         AND created_at < clock_timestamp() - make_interval(secs => $1)
       ORDER BY created_at ASC
       LIMIT $2
       FOR UPDATE SKIP LOCKED`,
      [config.recoveryLagMs / 1000, config.batchSize]
    );

    for (const event of eventsResult.rows) {
      try {
        await processEvent(event, client);
      } catch (err) {
        console.error(`[event-processor] Error processing event ${event.id}:`, err.message);
        // Mark as consumed to avoid infinite retries on bad events
        try {
          await client.query(
            'UPDATE wind.events SET consumed_at = clock_timestamp() WHERE id = $1',
            [event.id]
          );
        } catch (_) { /* connection gone */ }
      }
    }
  } catch (err) {
    console.error('[event-processor] Poll cycle error:', err.message);
  } finally {
    client.release();
    isProcessing = false;
  }
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Start the event processor polling loop.
 * Returns a cleanup function that stops polling.
 */
export function startEventProcessor() {
  if (pollingInterval) {
    console.warn('[event-processor] Already running');
    return () => stopEventProcessor();
  }

  // Fire immediately then poll at interval
  pollCycle();
  pollingInterval = setInterval(pollCycle, config.pollIntervalMs);
  console.log(`[event-processor] Started (poll every ${config.pollIntervalMs}ms, batch ${config.batchSize}, recovery lag ${config.recoveryLagMs}ms)`);

  return () => stopEventProcessor();
}

/**
 * Stop the event processor polling loop.
 */
export function stopEventProcessor() {
  if (pollingInterval) {
    clearInterval(pollingInterval);
    pollingInterval = null;
    console.log('[event-processor] Stopped');
  }
}
