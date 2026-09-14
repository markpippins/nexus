// ── Audit trail (read-only surface over the V155/V156 audit categories) ──
// Query helpers for tackle.audit_trail (V157) — the browsable view over
// REGISTRY_AUDIT + NEBULA_AUDIT rows in system_logs. Read-only by design:
// the audit trail is append-only and its erasure is guarded by the V157
// trigger (SET LOCAL tackle.allow_audit_erase escape hatch).

import { qAll } from "./db";

export interface AuditTrailParams {
  table?: string;
  op?: string;
  category?: string;
  since?: string;
  limit?: number;
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
  if (params.category) {
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
