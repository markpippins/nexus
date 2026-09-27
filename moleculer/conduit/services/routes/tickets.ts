// /tickets — ticket lifecycle detection and lineage queries.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import crypto from "node:crypto";
import { Router } from "express";
import { query } from "../db/client.js";

const router = Router();

router.post("/detect", async (_req, res) => {
  const staleResult = await query<any>(
    `UPDATE vision.tickets SET status = 'stale'
     WHERE status = 'claimed'
       AND last_activity IS NOT NULL
       AND last_activity < (NOW() - INTERVAL '6 hours')
     RETURNING id`
  );
  for (const row of staleResult) {
    await query(
      `INSERT INTO kernel.transition_event
         (event_id, event_type, aggregate_type, aggregate_id, actor, authority, payload)
       VALUES ($1, 'transition.requested', 'ticket', $2, 'conduit-srv', 'system', $3::jsonb)`,
      [crypto.randomUUID(), row.id, JSON.stringify({ from_status: "claimed", to_status: "stale", reason: "stale_detection" })]
    );
  }
  const stale = staleResult.length;

  const expiredAffected = await query<any>(
    `SELECT id, status FROM vision.tickets
     WHERE status IN ('open', 'claimed', 'stale')
       AND expires_at IS NOT NULL
       AND expires_at < NOW()`
  );
  if (expiredAffected.length > 0) {
    await query(
      `UPDATE vision.tickets SET status = 'expired'
       WHERE id = ANY($1::text[])`,
      [expiredAffected.map((r) => r.id)]
    );
  }
  for (const row of expiredAffected) {
    await query(
      `INSERT INTO kernel.transition_event
         (event_id, event_type, aggregate_type, aggregate_id, actor, authority, payload)
       VALUES ($1, 'transition.rejected', 'ticket', $2, 'conduit-srv', 'system', $3::jsonb)`,
      [crypto.randomUUID(), row.id, JSON.stringify({ from_status: row.status, to_status: "expired", reason: "expiry_detection" })]
    );
  }
  const expired = expiredAffected.length;

  res.json({ detected: true, stale, expired, timestamp: new Date().toISOString() });
});

router.get("/lineage/:planId", async (req, res) => {
  const { planId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }
  const tickets = await query(
    `SELECT id, role, status, tokens_used,
            parent_ticket_id, spawn_reason,
            replacement_of, closure_reason,
            created_at, closed_at
     FROM vision.tickets WHERE plan_id = $1
     ORDER BY created_at ASC`,
    [planId]
  );
  res.json({ plan_id: planId, tickets });
});

export default router;
