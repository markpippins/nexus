// ── Audit trail (read-only surface over the canonical audit categories) ──
// Query helpers for tackle.audit_trail (V157/V159) — the browsable view over
// the audit categories in system_logs. Read-only by design: the audit trail
// is append-only and its erasure is guarded by the V157 trigger
// (SET LOCAL tackle.allow_audit_erase escape hatch).
//
// The canonical category set lives in tackle.audit_log_categories() (V161)
// and is read from the database at query time — a future audit category
// added there (plus the V157 view/guard extensions) is served here with no
// service change.

import { qAll, qOne } from "./db";

export interface AuditTrailParams {
  table?: string;
  op?: string;
  category?: string;
  categories?: string[];
  since?: string;
  limit?: number;
}

/** Read the canonical audit-category set from the DB (V161 definition).
 *  Falls back to the V157-era pair only if the function is absent (e.g. a
 *  pre-V161 database), so the route stays up everywhere. */
export async function getAuditCategories(): Promise<string[]> {
  try {
    const row = await qOne(`SELECT tackle.audit_log_categories() AS cats`);
    const cats = row?.cats;
    if (Array.isArray(cats) && cats.length > 0) return cats.map(String);
  } catch {
    // undefined_function on pre-V161 DBs — fall through to the legacy default
  }
  return ["REGISTRY_AUDIT", "NEBULA_AUDIT"];
}

export async function queryAuditTrail(params: AuditTrailParams): Promise<any[]> {
  const limit = Math.min(Math.max(params.limit ?? 100, 1), 500);
  const conditions: string[] = [];
  const binds: Record<string, any> = { limit };

  if (params.table) {
    conditions.push(`audited_table = @table`);
    binds.table = params.table;
  }
  if (params.op) {
    conditions.push(`operation = @op`);
    binds.op = params.op.toUpperCase();
  }
  if (params.categories && params.categories.length > 0) {
    // Multi-category filter (plural). Expanded per-name so the existing
    // convertParams binds each value as its own parameter (repo pattern,
    // cf. db.ts system_logs listing) — no string interpolation of input.
    const names = params.categories.map((_, i) => `@cat${i}`);
    conditions.push(`category = ANY(ARRAY[${names.join(", ")}])`);
    params.categories.forEach((c, i) => {
      binds[`cat${i}`] = c;
    });
  } else if (params.category) {
    // Legacy singular filter kept for backward compatibility.
    conditions.push(`category = @category`);
    binds.category = params.category;
  }
  if (params.since) {
    conditions.push(`timestamp > @since`);
    binds.since = params.since;
  }

  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  return qAll(
    `SELECT timestamp, category, audited_table, operation, row_count,
            keys, application_name, client_addr, txid, message
       FROM tackle.audit_trail
       ${where}
      ORDER BY timestamp DESC
      LIMIT @limit`,
    binds
  );
}
