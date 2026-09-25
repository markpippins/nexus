// /governance — governance event replay and listing.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import { Router } from "express";
import { query, PEB_SCHEMA, VISION_SCHEMA } from "../db/client.js";

const router = Router();

router.post("/replay", async (_req, res) => {
  try {
    const result = await query(
      `INSERT INTO ${PEB_SCHEMA}.governance_events (receipt_id, event_type, work_request_id, plan_id, agent_role, payload, created_at)
       SELECT
         r.id,
         'receipt:' || r.type,
         wr.work_request_uuid,
         r.plan_id,
         r.agent_role,
         jsonb_build_object(
           'session_id', r.session_id,
           'artifact_path', r.artifact_path,
           'summary', r.summary,
           'ticket_id', r.ticket_id,
           'tokens_used', r.tokens_used
         ),
         r.created_at
       FROM ${VISION_SCHEMA}.receipts r
       LEFT JOIN ${VISION_SCHEMA}.work_requests wr ON wr.wr_id = r.plan_id
       WHERE NOT EXISTS (
         SELECT 1 FROM ${PEB_SCHEMA}.governance_events g WHERE g.receipt_id = r.id
       )
       ON CONFLICT (receipt_id) DO NOTHING
       RETURNING id`
    );
    res.json({ ok: true, replayed: result.length });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

router.get("/events", async (req, res) => {
  try {
    const planId = req.query.planId as string | undefined;
    const eventType = req.query.eventType as string | undefined;
    const limit = req.query.limit ? parseInt(req.query.limit as string, 10) : 50;
    let sql = `SELECT id, receipt_id, event_type, work_request_id, plan_id, agent_role, payload, created_at, replayed_at
               FROM ${PEB_SCHEMA}.governance_events`;
    const conditions: string[] = [];
    const params: any[] = [];
    let i = 1;
    if (planId) {
      conditions.push(`plan_id = $${i++}`);
      params.push(planId);
    }
    if (eventType) {
      conditions.push(`event_type = $${i++}`);
      params.push(eventType);
    }
    if (conditions.length > 0) sql += ` WHERE ${conditions.join(" AND ")}`;
    sql += ` ORDER BY created_at DESC LIMIT $${i++}`;
    params.push(limit);
    const events = await query(sql, params);
    res.json({ ok: true, events });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

export default router;
