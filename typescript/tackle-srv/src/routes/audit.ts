import { Router } from "express";
import { queryAuditTrail } from "../audit";

export const auditRouter = Router();

// ── Audit trail (read-only) ─────────────────────────────────────────
// Browsable surface over the V155/V156 statement-level audit categories
// (REGISTRY_AUDIT, NEBULA_AUDIT) stored in tackle.system_logs.
// Every route here is strictly read-only: the audit trail is append-only
// by design, and V157's erase guard refuses audit-row deletion unless a
// transaction explicitly opts in (SET LOCAL tackle.allow_audit_erase).

// GET /audit-trail — filtered listing with bounded default page
auditRouter.get("/", async (req, res) => {
  try {
    const { table, op, category, since, limit } = req.query;
    const parsedLimit = limit ? parseInt(String(limit), 10) : 100;
    const rows = await queryAuditTrail({
      table: table ? String(table) : undefined,
      op: op ? String(op) : undefined,
      category: category ? String(category) : undefined,
      since: since ? String(since) : undefined,
      limit: Number.isFinite(parsedLimit) ? parsedLimit : 100,
    });
    res.json({
      count: rows.length,
      categories: ["REGISTRY_AUDIT", "NEBULA_AUDIT"],
      entries: rows,
      note: "append-only; audit-row deletion is guarded (V157)",
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /audit-trail/recent — convenience window (default last 24h, max 500)
auditRouter.get("/recent", async (req, res) => {
  try {
    const hours = req.query.hours ? parseFloat(String(req.query.hours)) : 24;
    const table = req.query.table ? String(req.query.table) : undefined;
    const limit = req.query.limit ? parseInt(String(req.query.limit), 10) : 100;
    const safeHours = Number.isFinite(hours) ? Math.min(Math.max(hours, 0.1), 24 * 30) : 24;
    const safeLimit = Number.isFinite(limit) ? Math.min(Math.max(limit, 1), 500) : 100;
    const rows = await queryAuditTrail({
      since: new Date(Date.now() - safeHours * 3600_000).toISOString(),
      table,
      limit: safeLimit,
    });
    res.json({ count: rows.length, hours: safeHours, entries: rows });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
