// /vision — work requests and receipts for the Python LOSM bridge.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import crypto from "node:crypto";
import { Router } from "express";
import { query, queryOne, VISION_SCHEMA } from "../db/client.js";

const router = Router();

router.post("/work-requests", async (req, res) => {
  try {
    const { id, work_request_uuid, dco_json, context, status, title, entity_key } = req.body;
    if (!id) {
      res.status(400).json({ ok: false, error: "Missing required field: id" });
      return;
    }
    const uuid = work_request_uuid || crypto.randomUUID();
    const rows = await query<any>(
      `INSERT INTO ${VISION_SCHEMA}.work_requests (wr_id, work_request_uuid, dco_json, context, status, title, entity_key)
       VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
       ON CONFLICT (wr_id) DO UPDATE SET
         dco_json = EXCLUDED.dco_json,
         context = EXCLUDED.context,
         status = EXCLUDED.status,
         title = EXCLUDED.title,
         entity_key = COALESCE(EXCLUDED.entity_key, ${VISION_SCHEMA}.work_requests.entity_key),
         updated_at = NOW()
       RETURNING work_request_uuid, (xmax = 0) AS inserted`,
      [id, uuid, dco_json || "{}", JSON.stringify(context || {}), status || "pending", title || "", entity_key || null]
    );
    const inserted = rows[0]?.inserted === true;
    res.json({ ok: true, id, work_request_uuid: rows[0]?.work_request_uuid || uuid, action: inserted ? "created" : "updated" });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

router.get("/work-requests", async (req, res) => {
  try {
    const status = req.query.status as string | undefined;
    const limit = req.query.limit ? parseInt(req.query.limit as string, 10) : 50;
    let sql = `SELECT id, wr_id, work_request_uuid, dco_json, context, status, title, recorded_on_dt, updated_at
               FROM ${VISION_SCHEMA}.work_requests`;
    const params: any[] = [];
    if (status) {
      sql += ` WHERE status = $1`;
      params.push(status);
    }
    sql += ` ORDER BY recorded_on_dt DESC LIMIT $${params.length + 1}`;
    params.push(limit);
    const wrs = await query(sql, params);
    res.json({ ok: true, work_requests: wrs });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

router.get("/work-requests/:id", async (req, res) => {
  try {
    const wr = await queryOne<any>(
      `SELECT id, wr_id, work_request_uuid, dco_json, context, status, title, recorded_on_dt, updated_at
       FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = $1`,
      [req.params.id]
    );
    if (!wr) {
      res.status(404).json({ ok: false, error: "Not found" });
      return;
    }
    res.json({ ok: true, work_request: wr });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

router.get("/receipts", async (req, res) => {
  try {
    const planId = req.query.planId as string;
    if (!planId) {
      res.status(400).json({ ok: false, error: "Missing required query: planId" });
      return;
    }
    const receipts = await query(
      `SELECT id, plan_id, type, agent_role, session_id, ticket_id,
              artifact_path, summary, metadata_json, tokens_used, created_at, sequence
       FROM nebula.receipts_unified
       WHERE plan_id = $1
       ORDER BY sequence ASC NULLS LAST, created_at ASC`,
      [planId]
    );
    res.json({ ok: true, receipts });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

export default router;
