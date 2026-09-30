import { Router } from "express";
import { getDb } from "../db";
import { TABLES, TableMeta } from "../tables";

export const resolutionRouter = Router();

// ── Helpers ──────────────────────────────────────────────────────────────

function tableOf(name: string): TableMeta | undefined {
  return TABLES.find((t) => t.table === name);
}

function activeFilter(t: TableMeta): string {
  // Only apply the expiry filter when the table actually has the column —
  // maturity varies across resolution.* tables.
  return t.expiryCol ? `WHERE ${t.expiryCol} IS NULL` : "";
}

// ── GET /api/meta — registry overview ────────────────────────────────────

resolutionRouter.get("/meta", async (_req, res) => {
  try {
    const db = getDb();
    const items: Record<string, unknown>[] = [];
    for (const t of TABLES) {
      try {
        const where = t.expiryCol ? ` WHERE ${t.expiryCol} IS NULL` : "";
        const r = await db.query(
          `SELECT (SELECT count(*)::int FROM resolution.${t.table}${where}) AS active,
                  (SELECT count(*)::int FROM resolution.${t.table}) AS total`,
        );
        items.push({ table: t.table, label: t.label, group: t.group, ...r.rows[0] });
      } catch (err: any) {
        items.push({ table: t.table, label: t.label, group: t.group, error: err.message });
      }
    }
    res.json({
      service: "resolution-srv",
      schema: "resolution",
      readOnly: true,
      tables: items,
    });
  } catch (err: any) {
    res.status(500).json({ error: "meta_failed", message: err.message });
  }
});

// ── Generic table routes: GET /api/<table> and GET /api/<table>/:id ─────

for (const t of TABLES) {
  resolutionRouter.get(`/${t.table}`, async (req, res) => {
    try {
      const db = getDb();
      const limit = Math.min(Math.max(parseInt(String(req.query.limit ?? "100"), 10) || 100, 1), 1000);
      const offset = Math.max(parseInt(String(req.query.offset ?? "0"), 10) || 0, 0);
      const order = t.orderCol ?? "created_at";
      const where = activeFilter(t);
      // orderCol is registry-validated; limit/offset are parameterized ints.
      const sql = `SELECT * FROM resolution.${t.table} ${where} ORDER BY ${order} DESC LIMIT $1 OFFSET $2`;
      const { rows } = await db.query(sql, [limit, offset]);
      res.json({ table: t.table, count: rows.length, limit, offset, items: rows });
    } catch (err: any) {
      mapQueryError(res, err);
    }
  });

  resolutionRouter.get(`/${t.table}/:id`, async (req, res) => {
    try {
      const db = getDb();
      const { rows } = await db.query(
        `SELECT * FROM resolution.${t.table} WHERE ${t.idCol}::text = $1 LIMIT 1`,
        [req.params.id],
      );
      if (rows.length === 0) {
        return res.status(404).json({ error: "not_found", table: t.table, id: req.params.id });
      }
      res.json(rows[0]);
    } catch (err: any) {
      mapQueryError(res, err);
    }
  });
}

// ── Method guard: v1 is read-only ────────────────────────────────────────
// Every non-GET method on /api/* gets a uniform 405 with the canonical
// explanation. Write paths are producer-authorized and are added only via a
// per-table authorization decision (see To Do 6b3ca700).

resolutionRouter.all("/:table", methodGuard);
resolutionRouter.all("/:table/:id", methodGuard);

function methodGuard(req: any, res: any) {
  const t = tableOf(req.params.table);
  if (!t) {
    return res.status(404).json({ error: "unknown_table", table: req.params.table });
  }
  res.status(405).json({
    error: "read_only",
    message:
      "resolution-srv v1 is read-only. Writes to resolution.* go through producer-authorized paths (SOLScript evaluation, receipts, admission). Per-table write exposure requires an authorization decision on To Do 6b3ca700.",
    table: t.table,
    method: req.method,
  });
}

function mapQueryError(res: any, err: any): void {
  if (err?.code === "42P01") {
    res.status(404).json({ error: "unknown_table", message: err.message });
    return;
  }
  if (err?.code === "22P02") {
    res.status(400).json({ error: "invalid_id", message: err.message });
    return;
  }
  res.status(500).json({ error: "query_failed", message: err.message });
}
