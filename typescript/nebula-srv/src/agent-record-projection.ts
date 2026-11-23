// ── Agent-record list projection ─────────────────────────────────────────────
// Shared by GET /agent-records and POST /agent-records/search.
//
// content is excluded from the default projection to keep list payloads
// small (MCP inbox checks, etc.); contentLength (octet_length of content)
// is ALWAYS projected so consumers can distinguish "body exists, not
// projected" from "empty body" — the reader-side blind spot behind the
// lost-write incident (record b9b88010, engineer finding eb4e219a): the
// list/search endpoints previously omitted the content field entirely, so
// every record looked body-less in list-based audits.
//
// The full shape adds the content column itself for ?full=1 (legacy alias:
// includeContent=true).
//
// This module is intentionally import-safe (no DB/Redis/service imports) so
// the shape contract is unit-testable without the routes.ts import graph.
// routes.ts re-exports these symbols.

/** Convert snake_case DB row keys to camelCase and Date values to epoch ms */
export function camelCaseRow(row: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [key, value] of Object.entries(row)) {
    const camelKey = key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
    if (value instanceof Date) {
      out[camelKey] = value.getTime();
    } else {
      out[camelKey] = value;
    }
  }
  return out;
}

/** Default list/search projection: metadata + contentLength, never content. */
// harness_job_id is projected because it is the only field that ties a record
// to the harness run that wrote it, and the run-vs-record audit
// (bin/check_harness_silent_success.py) reads it from list queries.
//
// metadata is projected because it carries correction provenance
// (metadata.corrections[], metadata.residue_closed) written alongside the
// corrected-by:/residue-closed-by: tags. Omitting it left the correction note
// readable only through psql, so an auditor reading the record's tags could
// see that it had been corrected but not by what — the same reader-side blind
// spot the contentLength contract above closes for bodies.
export const AGENT_RECORD_LIST_COLUMNS = `id, record_type, role, model, title, source_path, tags, metadata,
         octet_length(content)::int AS content_length,
         system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt,
         level, visibility_scope, harness_job_id`;

/** Full projection (?full=1 / legacy includeContent=true): adds content. */
export const AGENT_RECORD_LIST_COLUMNS_FULL = `id, record_type, role, model, title, source_path, tags, metadata,
         octet_length(content)::int AS content_length, content,
         system_id, subsystem_id, feature_id, plan_ref, created_at, recorded_on_dt,
         level, visibility_scope, harness_job_id`;

/**
 * Point projection (GET /api/agent-records/:id): the full single-record
 * read. It MUST carry metadata (and harness_job_id) too — omitting them
 * left a consumer reading one record unable to see the same provenance
 * the list had just returned (corrected-by: tag present, corrections[]
 * absent — the psql-only blind spot #1044 closed for lists). Kept as a
 * named constant so the shape is unit-testable like the list contract.
 */
export const AGENT_RECORD_POINT_COLUMNS = `id, record_type, role, model, title, source_path, tags, metadata,
                content,
                system_id, subsystem_id, feature_id, plan_ref, candidate_id,
                requirement_id, created_at, recorded_on_dt, level, visibility_scope,
                harness_job_id`;
