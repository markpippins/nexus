import { Router } from "express";
import { getAuditCategories, queryAuditTrail } from "../audit";

export const auditRouter = Router();

// ── Audit trail (read-only) ─────────────────────────────────────────
// Browsable surface over the canonical audit categories
// (REGISTRY_AUDIT / NEBULA_AUDIT / KG_AUDIT — defined by
// tackle.audit_log_categories(), V161) stored in tackle.system_logs.
// Every route here is strictly read-only: the audit trail is append-only
// by design, and V157's erase guard refuses audit-row deletion unless a
// transaction explicitly opts in (SET LOCAL tackle.allow_audit_erase).
//
// Category filtering:
//   ?category=X          legacy singular filter (backward compatible;
//                        normalized to uppercase)
//   ?categories=A,B,C    plural multi-category filter (new)
// Both are validated against tackle.audit_log_categories(); an unknown
// category is refused with 400 so a typo can never silently widen (or
// empty) the result set — previously ?category=lowercase matched nothing
// without saying why.

const cachedCats: { value: string[] | null; at: number } = { value: null, at: 0 };
const CATS_TTL_MS = 60_000;

async function currentCategories(): Promise<string[]> {
  // Short cache so a burst of requests doesn't turn into a burst of
  // function-call round trips; short enough that a new category added via
  // migration is served within a minute without a restart.
  if (cachedCats.value && Date.now() - cachedCats.at < CATS_TTL_MS) {
    return cachedCats.value;
  }
  const cats = await getAuditCategories();
  cachedCats.value = cats;
  cachedCats.at = Date.now();
  return cats;
}

function parseCategoriesParam(raw: unknown): string[] | undefined {
  if (raw === undefined) return undefined;
  const parts = String(raw)
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  return parts.length ? parts : undefined;
}

// GET /audit-trail — filtered listing with bounded default page
auditRouter.get("/", async (req, res) => {
  try {
    const { table, op, category, categories, since, limit } = req.query;
    const parsedLimit = limit ? parseInt(String(limit), 10) : 100;
    const canonical = await currentCategories();

    const pluralReq = parseCategoriesParam(categories);
    const singularReq =
      !pluralReq && category ? String(category).trim().toUpperCase() : undefined;

    // Both forms validate against the canonical set; unknown → 400 so a typo
    // can never silently widen (or empty) the result set.
    const toValidate = pluralReq ?? (singularReq ? [singularReq] : []);
    if (toValidate.length) {
      const unknown = toValidate.filter((c) => !canonical.includes(c));
      if (unknown.length) {
        res.status(400).json({
          error: `unknown audit categor${unknown.length === 1 ? "y" : "ies"}: ${unknown.join(", ")}`,
          canonical_categories: canonical,
        });
        return;
      }
    }

    const rows = await queryAuditTrail({
      table: table ? String(table) : undefined,
      op: op ? String(op) : undefined,
      category: singularReq,
      categories: pluralReq,
      since: since ? String(since) : undefined,
      limit: Number.isFinite(parsedLimit) ? parsedLimit : 100,
    });
    res.json({
      count: rows.length,
      categories: canonical,
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
