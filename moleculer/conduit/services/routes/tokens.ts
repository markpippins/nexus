// /tokens — token usage reporting by plan, role, and ticket.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import { Router } from "express";
import { queryOne } from "../db/client.js";

const router = Router();

router.get("/plan/:planId", async (req, res) => {
  const { planId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }
  const row = await queryOne<any>(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
     FROM nebula.receipts_unified WHERE plan_id = $1`,
    [planId]
  );
  res.json({ plan_id: planId, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 });
});

router.get("/role/:role", async (req, res) => {
  const { role } = req.params;
  if (!["builder", "reviewer", "planner", "critic"].includes(role)) {
    res.status(400).json({ error: `Invalid role: ${role}` });
    return;
  }
  const row = await queryOne<any>(
    `SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
     FROM nebula.receipts_unified WHERE agent_role = $1`,
    [role]
  );
  res.json({ role, total_tokens: row?.total_tokens ?? 0, receipts: row?.receipts ?? 0 });
});

router.get("/ticket/:ticketId", async (req, res) => {
  const { ticketId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(ticketId)) {
    res.status(400).json({ error: "Invalid ticket ID" });
    return;
  }
  const row = await queryOne<any>(
    `SELECT COALESCE(tokens_used, 0) as tokens_used
     FROM vision.tickets WHERE id = $1`,
    [ticketId]
  );
  res.json({ ticket_id: ticketId, tokens_used: row?.tokens_used ?? 0 });
});

export default router;
