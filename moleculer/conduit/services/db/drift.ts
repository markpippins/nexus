// ── Projection drift check ──────────────────────────────────────────
// Moved from conduit-mcp per plan 1285 (Architect decision: conduit-srv
// owns SQL, conduit-mcp delegates — pg single-writer). All projection /
// replay SQL lives here in conduit-srv; conduit-mcp imports no pg for
// these paths.
//
// `conduit.check_projection_drift($1)` is a pre-existing PL/pgSQL
// function (migration 033 / V051) that replays conduit.work_request_events
// in sequence order to compute the expected state and compares it against
// the live conduit.work_request_state projection. Non-destructive: it only
// reads, never writes.
import { query, queryOne, PG_SCHEMA, VISION_SCHEMA } from "./client.js";

export interface ProjectionDriftResult {
  expected_state: string;
  expected_vision_stage: string | null;
  expected_vision_ir_version: number;
  expected_last_event_id: string | null;
  live_state: string | null;
  live_vision_stage: string | null;
  live_vision_ir_version: number | null;
  live_last_event_id: string | null;
  has_drift: boolean;
}

export async function resolveWrUuid(wrIdOrUuid: string): Promise<string> {
  const uuidRegex =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuidRegex.test(wrIdOrUuid)) return wrIdOrUuid;
  const row = await queryOne<{ work_request_uuid: string }>(
    `SELECT work_request_uuid FROM ${VISION_SCHEMA}.work_requests WHERE wr_id = $1`,
    [wrIdOrUuid]
  );
  if (!row) return wrIdOrUuid;
  return row.work_request_uuid;
}

export async function checkProjectionDrift(
  workRequestId: string
): Promise<ProjectionDriftResult | undefined> {
  return queryOne<ProjectionDriftResult>(
    `SELECT * FROM ${PG_SCHEMA}.check_projection_drift($1::uuid)`,
    [workRequestId]
  );
}

export interface DriftScanRow {
  work_request_uuid: string;
  wr_id: string | null;
  status: string;
  drift: ProjectionDriftResult;
}

export async function scanProjectionDrift(opts: {
  limit?: number;
  statusFilter?: string[];
}): Promise<{ scanned: DriftScanRow[]; drifted: DriftScanRow[] }> {
  const raw = Number.isFinite(opts.limit as number) ? (opts.limit as number) : 100;
  const limit = Math.min(Math.max(raw, 1), 500);
  const rows = await query<{
    work_request_uuid: string;
    wr_id: string | null;
    status: string;
  }>(
    opts.statusFilter?.length
      ? `SELECT work_request_uuid, wr_id, status
         FROM ${VISION_SCHEMA}.work_requests
         WHERE status = ANY($1::text[])
         ORDER BY recorded_on_dt DESC
         LIMIT $2`
      : `SELECT work_request_uuid, wr_id, status
         FROM ${VISION_SCHEMA}.work_requests
         WHERE status NOT IN ('completed', 'cancelled', 'failed', 'settled',
                              'rejected', 'noop', 'deferred')
         ORDER BY recorded_on_dt DESC
         LIMIT $1`,
    opts.statusFilter?.length
      ? [opts.statusFilter, limit]
      : [limit]
  );
  const scanned: DriftScanRow[] = [];
  const drifted: DriftScanRow[] = [];
  for (const row of rows) {
    try {
      const drift = await checkProjectionDrift(row.work_request_uuid);
      if (!drift) continue;
      const entry: DriftScanRow = {
        work_request_uuid: row.work_request_uuid,
        wr_id: row.wr_id,
        status: row.status,
        drift,
      };
      scanned.push(entry);
      if (drift.has_drift) drifted.push(entry);
    } catch (err: any) {
      console.error(
        `[drift-scan] failed for ${row.work_request_uuid}: ${err?.message ?? err}`
      );
    }
  }
  return { scanned, drifted };
}
