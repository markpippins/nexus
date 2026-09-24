import { Errors } from "moleculer";
import { getDb } from "./store";
import { TABLES, TableMeta } from "./tables";

/**
 * Route handlers for the semantics twin (canary :4160).
 *
 * VERBATIM PORT of typescript/semantics-srv/src/routes/semantics.ts +
 * routes/health.ts — same SQL, same envelopes, same status codes. Handlers
 * keep Express-shaped inputs ({ params, query, body }) and return
 * { status, body }; errors throw MoleculerError(message, status, code) so
 * the gateway's onError emits the incumbent's { error, message } envelope.
 *
 * Envelope parity (incumbent semantics.ts catch blocks):
 *   - every failure: { error: <code>, message } with per-route codes
 *     (list_failed / get_failed / add_failed / update_failed /
 *     soft_delete_failed / meta_failed / envelope_failed / not_found /
 *     duplicate_active_key / missing_field / self_relation /
 *     invalid_status / invalid_transition / ...)
 *   - PG duplicate key (SQLSTATE 23505 on err.code) → duplicate_active_key
 *   - PATCH miss ("no active row" in message) → 404 not_found
 *   - add/update success → 201 raw row / 200 row + superseded_id
 *
 * The per-table CRUD actions are built programmatically from TABLES
 * (handlersFor(t)) — the alias map in api.service.ts is the literal
 * expansion the drift gate parses; handlers stay table-driven.
 */

export type HandlerResult = { status: number; body: unknown };

export interface Req {
  params: Record<string, string>;
  query: Record<string, any>;
  body?: any;
}

export function fail(status: number, code: string, message: string): Errors.MoleculerError {
  return new Errors.MoleculerError(message, status, code);
}

// ── Helpers (verbatim from routes/semantics.ts) ──────────────────────

function coerce(t: TableMeta, paramName: string, value: any): any {
  const col = paramName.replace(/^p_/, "");
  if (t.smallintCols.includes(col)) {
    if (value === null || value === undefined) return null;
    const n = Number(value);
    if (Number.isNaN(n)) throw fail(400, "add_failed", `Invalid numeric value for ${paramName}: ${value}`);
    return n;
  }
  if (t.jsonbCols.includes(col)) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") return JSON.parse(value);
    return value;
  }
  return value;
}

function schemaOf(t: TableMeta): string {
  return t.schema ?? "semantics";
}

function buildAddCall(
  t: TableMeta,
  body: Record<string, any>,
): { sql: string; values: any[] } {
  const values: any[] = [];
  const parts: string[] = [];
  const push = (name: string, val: any) => {
    values.push(coerce(t, name, val));
    parts.push(`${name} => $${values.length}`);
  };
  if (body.p_id !== undefined) push("p_id", body.p_id);
  for (const col of t.writable) {
    const key = `p_${col}`;
    if (body[key] !== undefined) push(key, body[key]);
  }
  const schema = schemaOf(t);
  if (schema !== "semantics") {
    // No add_<table> proc for resolution.* ontology tables — use direct INSERT
    // (matching the epistemologist's write convention). body is already p_*-keyed.
    const cols: string[] = [];
    const vals: any[] = [];
    const pushCol = (name: string, val: any) => {
      vals.push(coerce(t, name, val));
      cols.push(name);
    };
    if (body.p_id !== undefined) pushCol("id", body.p_id);
    for (const col of t.writable) {
      const key = `p_${col}`;
      if (body[key] !== undefined && col !== "expired_at") pushCol(col, body[key]);
    }
    return {
      sql: `INSERT INTO ${schema}.${t.table} (${cols.join(", ")}) VALUES (${vals.map((_, i) => `$${i + 1}`).join(", ")}) RETURNING *`,
      values: vals,
    };
  }
  return { sql: `SELECT * FROM semantics.add_${t.table}(${parts.join(", ")})`, values };
}

function buildUpdateCall(
  t: TableMeta,
  body: Record<string, any>,
): { sql: string; values: any[] } {
  const schema = schemaOf(t);
  if (schema !== "semantics") {
    // Append-only replace for resolution.* ontology tables: expire the old
    // row, then insert a new version with the supplied writable fields.
    const id = body.id ?? body.p_id;
    const values: any[] = [id];
    const updatable = ["id", ...t.writable.filter((c) => c !== "expired_at")];
    const setCols: string[] = [];
    updatable.forEach((col, i) => {
      const val = body[col] ?? body[`p_${col}`];
      if (val === undefined) return;
      setCols.push(col);
      values.push(coerce(t, col, val));
    });
    if (setCols.length === 0) throw fail(400, "update_failed", "no writable fields provided for update");
    const placeholders = setCols.map((_, i) => `$${i + 2}`).join(", ");
    const sql =
      `WITH expired AS (` +
      `  UPDATE ${schema}.${t.table} SET expired_at = now() WHERE id = $1 AND expired_at IS NULL RETURNING id` +
      `) INSERT INTO ${schema}.${t.table} (${setCols.join(", ")}) ` +
      `SELECT ${placeholders} RETURNING *`;
    return { sql, values };
  }

  const values: any[] = [];
  const parts: string[] = [];
  const push = (name: string, val: any) => {
    values.push(coerce(t, name, val));
    parts.push(`${name} => $${values.length}`);
  };
  const idParam = t.idParam ?? "p_id";
  push(idParam, body[idParam] ?? body.p_id);
  if (t.table === "relationship_type") {
    if (body.p_new_name === undefined) {
      throw fail(400, "update_failed", "update relationship_type requires p_new_name (the new type name)");
    }
    push("p_new_name", body.p_new_name);
  }
  for (const col of t.writable) {
    const key = `p_${col}`;
    if (key === idParam) continue; // id already pushed above
    if (body[key] !== undefined) push(key, body[key]);
  }
  return { sql: `SELECT * FROM semantics.update_${t.table}(${parts.join(", ")})`, values };
}

// ── GET /api/health (routes/health.ts verbatim; twin port via env) ───

export async function healthHandler(): Promise<Record<string, unknown>> {
  // Incumbent health.ts is a pure responder; the DB probe lives in index.ts
  // startup only. Mirror the exact body shape; the twin's port comes from
  // SERVICE_PORT so a canary diff must normalize {port, pid, timestamp}.
  return {
    status: "ok",
    service: "semantics-srv",
    port: parseInt(process.env.SERVICE_PORT || process.env.SEMANTICS_SRV_PORT || "3160", 10),
    pid: process.pid,
    timestamp: new Date().toISOString(),
  };
}

// ── GET /api/meta — schema overview (verbatim) ───────────────────────

export async function metaHandler(): Promise<HandlerResult> {
  try {
    const db = getDb();
    const items = [];
    for (const t of TABLES) {
      const schema = schemaOf(t);
      const r = await db.query(
        `SELECT
           (SELECT count(*)::int FROM ${schema}.${t.table} WHERE expired_at IS NULL) AS active,
           (SELECT count(*)::int FROM ${schema}.${t.table}) AS total`,
      );
      items.push({ table: t.table, label: t.label, idType: t.idType, idAuto: t.idAuto, schema, ...r.rows[0] });
    }
    const { rows: procRows } = await db.query(`
      SELECT count(*)::int AS procs
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'semantics'
        AND (p.proname LIKE 'add_%' OR p.proname LIKE 'soft_delete_%'
             OR p.proname LIKE 'update_%' OR p.proname LIKE 'resolve_%')`);
    return {
      status: 200,
      body: {
        service: "semantics-srv",
        schema: "semantics",
        tables: items,
        procs: procRows[0]?.procs ?? 0,
        writableParams: Object.fromEntries(TABLES.map((t) => [t.table, ["p_id", ...t.writable.map((c) => `p_${c}`)]])),
      },
    };
  } catch (err: any) {
    throw fail(500, "meta_failed", err.message);
  }
}

// ── T02: Asset identity spine — envelope routes (verbatim) ──────────

export async function canonicalAssetEnvelope(id: string): Promise<HandlerResult> {
  try {
    const db = getDb();
    const assetId = id;

    // 1. Fetch the asset (match on uuid or canonical_asset_id)
    const { rows: [asset] } = await db.query(
      `SELECT * FROM semantics.canonical_asset
       WHERE (id::text = $1 OR canonical_asset_id = $1)
         AND expired_at IS NULL
       LIMIT 1`,
      [assetId],
    );
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${assetId} not found`);
    }

    // 2–4: run parallel queries (no cross-schema — those can fail independently)
    const [revResult, claimResult, relResult] = await Promise.all([
      db.query(
        `SELECT ar.*,
                COALESCE(json_agg(
                  json_build_object(
                    'id', so.id, 'platform', so.platform,
                    'platformIdentifier', so.platform_identifier,
                    'namespace', so.namespace, 'rawLocation', so.raw_location,
                    'observedAt', so.observed_at, 'ingestionRunId', so.ingestion_run_id,
                    'rawHash', so.raw_hash
                  ) ORDER BY so.observed_at DESC
                ) FILTER (WHERE so.id IS NOT NULL), '[]'::json) AS "sourceObservations",
                parent.revision_id AS "parentRevisionId"
         FROM semantics.asset_revision ar
         LEFT JOIN semantics.source_observation so ON so.revision_id = ar.id AND so.expired_at IS NULL
         LEFT JOIN semantics.asset_revision parent ON parent.id = ar.parent_revision_id
         WHERE ar.asset_id = $1 AND ar.expired_at IS NULL
         GROUP BY ar.id, parent.revision_id
         ORDER BY ar.recording_start DESC NULLS LAST, ar.created_at DESC`,
        [asset.id],
      ),
      db.query(
        `SELECT aic.*,
                json_build_object(
                  'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                  'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
                ) AS "candidateAsset"
         FROM semantics.asset_identity_claim aic
         LEFT JOIN semantics.canonical_asset ca ON ca.id = aic.candidate_asset_id AND ca.expired_at IS NULL
         WHERE aic.asset_id = $1 AND aic.expired_at IS NULL
         ORDER BY aic.created_at DESC`,
        [asset.id],
      ),
      db.query(
        `SELECT ar.*,
                json_build_object(
                  'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                  'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
                ) AS "relatedAsset",
                CASE WHEN ar.from_asset_id = $1 THEN 'outbound' ELSE 'inbound' END AS direction
         FROM semantics.asset_relation ar
         JOIN semantics.canonical_asset ca ON ca.id =
           CASE WHEN ar.from_asset_id = $1 THEN ar.to_asset_id ELSE ar.from_asset_id END
           AND ca.expired_at IS NULL
         WHERE (ar.from_asset_id = $1 OR ar.to_asset_id = $1)
           AND ar.expired_at IS NULL
         ORDER BY ar.effective_at DESC`,
        [asset.id],
      ),
    ]);

    // 5. Cross-schema external IDs — V076 migration: replaced
    //    system_external_ids junction with asset_relation.
    let extRows: any[] = [];
    try {
      const { rows } = await db.query(
        `SELECT ar.id, ar.relation_type AS "relationType",
                ar.effective_at AS "effectiveAt",
                json_build_object(
                  'id', ns.id, 'name', ns.name,
                  'description', ns.description
                ) AS "nebulaSystem"
         FROM semantics.asset_relation ar
         JOIN nebula.systems ns ON ns.asset_id = ar.from_asset_id
         WHERE ar.to_asset_id = $1
           AND ar.expired_at IS NULL
         ORDER BY ar.effective_at DESC`,
        [asset.id],
      );
      extRows = rows;
    } catch {
      // nebula schema may not be accessible in all environments
    }

    const revisions = (revResult.rows || []).map((r: any) => ({
      id: r.id,
      revisionId: r.revision_id,
      contentHash: r.content_hash,
      sourceHash: r.source_hash,
      recordingStart: r.recording_start,
      recordingEnd: r.recording_end,
      createdBy: r.created_by,
      createdAt: r.created_at,
      parentRevisionId: r.parentRevisionId || null,
      sourceObservations: r.sourceObservations || [],
    }));

    const identityClaims = (claimResult.rows || []).map((c: any) => ({
      id: c.id,
      claimType: c.claim_type,
      confidence: c.confidence,
      basis: c.basis,
      status: c.status,
      decidedBy: c.decided_by,
      decidedAt: c.decided_at,
      candidateAsset: c.candidateAsset || null,
    }));

    const relations = (relResult.rows || []).map((r: any) => ({
      id: r.id,
      relationType: r.relation_type,
      direction: r.direction,
      effectiveAt: r.effective_at,
      decidedBy: r.decided_by,
      decidedAt: r.decided_at,
      relatedAsset: r.relatedAsset,
    }));

    return {
      status: 200,
      body: {
        id: asset.id,
        canonicalAssetId: asset.canonical_asset_id,
        assetKind: asset.asset_kind,
        canonicalKey: asset.canonical_key,
        sourceHash: asset.source_hash,
        contentHash: asset.content_hash,
        validityStart: asset.validity_start,
        validityEnd: asset.validity_end,
        createdAt: asset.created_at,
        expiredAt: asset.expired_at,
        revisions,
        identityClaims,
        relations,
        externalIds: extRows,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "envelope_failed", err.message);
  }
}

export async function assetRevisionEnvelope(id: string): Promise<HandlerResult> {
  try {
    const db = getDb();

    const { rows: [rev] } = await db.query(
      `SELECT * FROM semantics.asset_revision
       WHERE (id::text = $1 OR revision_id = $1)
         AND expired_at IS NULL
       LIMIT 1`,
      [id],
    );
    if (!rev) {
      throw fail(404, "not_found", `asset_revision ${id} not found`);
    }

    const [assetResult, soResult, parentResult, childResult] = await Promise.all([
      db.query("SELECT * FROM semantics.canonical_asset WHERE id = $1 AND expired_at IS NULL", [rev.asset_id]),
      db.query("SELECT * FROM semantics.source_observation WHERE revision_id = $1 AND expired_at IS NULL ORDER BY observed_at DESC", [rev.id]),
      rev.parent_revision_id
        ? db.query("SELECT id, revision_id, content_hash, created_at FROM semantics.asset_revision WHERE id = $1", [rev.parent_revision_id])
        : Promise.resolve({ rows: [] }),
      db.query("SELECT id, revision_id, content_hash, created_at FROM semantics.asset_revision WHERE parent_revision_id = $1 AND expired_at IS NULL ORDER BY created_at DESC", [rev.id]),
    ]);

    return {
      status: 200,
      body: {
        id: rev.id,
        revisionId: rev.revision_id,
        contentHash: rev.content_hash,
        sourceHash: rev.source_hash,
        recordingStart: rev.recording_start,
        recordingEnd: rev.recording_end,
        createdBy: rev.created_by,
        createdAt: rev.created_at,
        asset: assetResult.rows[0] || null,
        sourceObservations: soResult.rows,
        parentRevision: parentResult.rows[0] || null,
        childRevisions: childResult.rows,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "envelope_failed", err.message);
  }
}

// ── Evidence filter routes (verbatim) ────────────────────────────────

export async function evidenceItemFilter(query: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const includeExpired = query.includeExpired === "true" || query.includeExpired === "1";
    const limit = Math.min(parseInt(String(query.limit || "100"), 10) || 100, 500);
    const offset = Math.max(parseInt(String(query.offset || "0"), 10) || 0, 0);

    const clauses: string[] = includeExpired ? [] : ["ei.expired_at IS NULL"];
    const values: any[] = [];
    let i = 1;

    if (query.evidenceType) {
      clauses.push(`et.name = $${i++}`);
      values.push(query.evidenceType);
    }
    if (query.origin) {
      clauses.push(`ei.origin = $${i++}`);
      values.push(query.origin);
    }
    if (query.uri) {
      clauses.push(`ei.uri LIKE $${i++}`);
      values.push(`${query.uri}%`);
    }
    if (query.sourceHash) {
      clauses.push(`ei.source_hash = $${i++}`);
      values.push(query.sourceHash);
    }

    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

    const [dataResult, countResult] = await Promise.all([
      db.query(
        `SELECT ei.*, et.name AS "evidenceType"
         FROM semantics.evidence_item ei
         JOIN semantics.evidence_type et ON et.id = ei.evidence_type_id
         ${where}
         ORDER BY ei.captured_at DESC NULLS LAST
         LIMIT $${i} OFFSET $${i + 1}`,
        [...values, limit, offset],
      ),
      db.query(
        `SELECT count(*)::int AS total
         FROM semantics.evidence_item ei
         JOIN semantics.evidence_type et ON et.id = ei.evidence_type_id
         ${where}`,
        values,
      ),
    ]);

    return {
      status: 200,
      body: {
        items: dataResult.rows,
        total: countResult.rows[0]?.total ?? 0,
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
      },
    };
  } catch (err: any) {
    throw fail(500, "list_failed", err.message);
  }
}

export async function statementEvidenceFilter(query: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const includeExpired = query.includeExpired === "true" || query.includeExpired === "1";
    const limit = Math.min(parseInt(String(query.limit || "100"), 10) || 100, 500);
    const offset = Math.max(parseInt(String(query.offset || "0"), 10) || 0, 0);

    const clauses: string[] = includeExpired ? [] : ["se.expired_at IS NULL"];
    const values: any[] = [];
    let i = 1;

    if (query.statementType) {
      clauses.push(`se.statement_type = $${i++}`);
      values.push(query.statementType);
    }
    if (query.statementId) {
      clauses.push(`se.statement_id = $${i++}`);
      values.push(query.statementId);
    }
    if (query.role) {
      clauses.push(`se.role = $${i++}`);
      values.push(query.role);
    }

    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

    const [dataResult, countResult] = await Promise.all([
      db.query(
        `SELECT se.*, et.name AS "evidenceType", ei.uri, ei.excerpt
         FROM semantics.statement_evidence se
         JOIN semantics.evidence_item ei ON ei.id = se.evidence_item_id
         JOIN semantics.evidence_type et ON et.id = ei.evidence_type_id
         ${where}
         ORDER BY se.effective_at DESC
         LIMIT $${i} OFFSET $${i + 1}`,
        [...values, limit, offset],
      ),
      db.query(
        `SELECT count(*)::int AS total
         FROM semantics.statement_evidence se
         ${where}`,
        values,
      ),
    ]);

    return {
      status: 200,
      body: {
        items: dataResult.rows,
        total: countResult.rows[0]?.total ?? 0,
        page: Math.floor(offset / limit) + 1,
        pageSize: limit,
      },
    };
  } catch (err: any) {
    throw fail(500, "list_failed", err.message);
  }
}

// ── Generated per-table CRUD (verbatim loop body) ────────────────────

export function handlersFor(t: TableMeta) {
  const base = `/${t.table}`;

  async function list(query: Record<string, any>): Promise<HandlerResult> {
    try {
      const includeExpired =
        query.includeExpired === "true" || query.includeExpired === "1";
      const limit = Math.min(parseInt(String(query.limit || "100"), 10) || 100, 500);
      const offset = Math.max(parseInt(String(query.offset || "0"), 10) || 0, 0);
      const where = includeExpired ? "" : "WHERE expired_at IS NULL";
      const { rows } = await getDb().query(
        `SELECT * FROM ${schemaOf(t)}.${t.table} ${where} ORDER BY id LIMIT $1 OFFSET $2`,
        [limit, offset],
      );
      return { status: 200, body: { table: t.table, count: rows.length, items: rows } };
    } catch (err: any) {
      throw fail(500, "list_failed", err.message);
    }
  }

  async function get(id: string): Promise<HandlerResult> {
    try {
      const idCol = t.idCol ?? "id";
      // idCol tables (relationship_type) match on either the uuid PK or the
      // natural key; the uuid side is cast to text so the shared $1 placeholder
      // resolves (avoiding 'operator does not exist: text = uuid' ambiguity).
      const match =
        idCol === "id" ? "id = $1" : "id::text = $1 OR " + idCol + " = $1";
      const { rows } = await getDb().query(
        `SELECT * FROM ${schemaOf(t)}.${t.table} WHERE ${match} LIMIT 1`,
        [id],
      );
      if (!rows.length) {
        throw fail(404, "not_found", `${t.table} ${id} not found`);
      }
      return { status: 200, body: rows[0] };
    } catch (err: any) {
      if (err?.name === "MoleculerError") throw err;
      throw fail(500, "get_failed", err.message);
    }
  }

  async function add(body: Record<string, any>): Promise<HandlerResult> {
    try {
      const { sql, values } = buildAddCall(t, body || {});
      const { rows } = await getDb().query(sql, values);
      return { status: 201, body: rows[0] };
    } catch (err: any) {
      // node-postgres exposes the SQLSTATE on err.code (e.g. '23505'), not in
      // the message text — match on err.code so duplicate detection works.
      const isDup = err?.code === "23505";
      const dup = isDup ? "duplicate_active_key" : "add_failed";
      throw fail(400, dup, err.message);
    }
  }

  async function update(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
    try {
      const body = { ...(reqBody || {}), [t.idParam ?? "p_id"]: id };
      const { sql, values } = buildUpdateCall(t, body);
      const { rows } = await getDb().query(sql, values);
      return { status: 200, body: { ...rows[0], superseded_id: id } };
    } catch (err: any) {
      const isDup = err?.code === "23505";
      const code = err.message?.includes("no active row")
        ? "not_found"
        : isDup
          ? "duplicate_active_key"
          : "update_failed";
      const status = code === "not_found" ? 404 : 400;
      throw fail(status, code, err.message);
    }
  }

  async function remove(id: string): Promise<HandlerResult> {
    try {
      const schema = schemaOf(t);
      let deleted = 0;
      if (schema !== "semantics") {
        // No soft_delete_<table> proc for resolution.* — expire-not-delete via
        // direct SQL.
        const { rows } = await getDb().query(
          `UPDATE ${schema}.${t.table} SET expired_at = now() WHERE id = $1 AND expired_at IS NULL RETURNING id`,
          [id],
        );
        deleted = rows.length;
      } else {
        const { rows } = await getDb().query(
          `SELECT semantics.soft_delete_${t.table}(${t.idParam ?? "p_id"} => $1) AS deleted`,
          [id],
        );
        deleted = rows[0].deleted;
      }
      return { status: 200, body: { table: t.table, id, deleted } };
    } catch (err: any) {
      throw fail(500, "soft_delete_failed", err.message);
    }
  }

  return { base, list, get, add, update, remove };
}

// ── T02: Asset sub-resource routes (verbatim) ────────────────────────

async function resolveAsset(db: any, id: string): Promise<any> {
  const { rows: [asset] } = await db.query(
    "SELECT * FROM semantics.canonical_asset WHERE (id::text = $1 OR canonical_asset_id = $1) AND expired_at IS NULL LIMIT 1",
    [id],
  );
  return asset || null;
}

export async function caRevisionsList(id: string, query: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const limit = Math.min(parseInt(String(query.limit || "50"), 10) || 50, 200);
    const offset = Math.max(parseInt(String(query.offset || "0"), 10) || 0, 0);

    const { rows: [asset] } = await db.query(
      "SELECT id, canonical_asset_id, asset_kind FROM semantics.canonical_asset WHERE (id::text = $1 OR canonical_asset_id = $1) AND expired_at IS NULL LIMIT 1",
      [id],
    );
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const { rows: revisions } = await db.query(
      `SELECT ar.*,
              COALESCE(json_agg(
                json_build_object(
                  'id', so.id, 'platform', so.platform,
                  'platformIdentifier', so.platform_identifier,
                  'namespace', so.namespace, 'rawLocation', so.raw_location,
                  'observedAt', so.observed_at, 'ingestionRunId', so.ingestion_run_id,
                  'rawHash', so.raw_hash
                ) ORDER BY so.observed_at DESC
              ) FILTER (WHERE so.id IS NOT NULL), '[]'::json) AS "sourceObservations",
              parent.revision_id AS "parentRevisionId"
       FROM semantics.asset_revision ar
       LEFT JOIN semantics.source_observation so ON so.revision_id = ar.id AND so.expired_at IS NULL
       LEFT JOIN semantics.asset_revision parent ON parent.id = ar.parent_revision_id
       WHERE ar.asset_id = $1 AND ar.expired_at IS NULL
       GROUP BY ar.id, parent.revision_id
       ORDER BY ar.recording_start DESC NULLS LAST, ar.created_at DESC
       LIMIT $2 OFFSET $3`,
      [asset.id, limit, offset],
    );

    const { rows: [{ count }] } = await db.query(
      "SELECT count(*)::int FROM semantics.asset_revision WHERE asset_id = $1 AND expired_at IS NULL",
      [asset.id],
    );

    return {
      status: 200,
      body: {
        asset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
        revisions: (revisions || []).map((r: any) => ({
          id: r.id,
          revisionId: r.revision_id,
          contentHash: r.content_hash,
          sourceHash: r.source_hash,
          recordingStart: r.recording_start,
          recordingEnd: r.recording_end,
          createdBy: r.created_by,
          createdAt: r.created_at,
          parentRevisionId: r.parentRevisionId || null,
          sourceObservations: r.sourceObservations || [],
        })),
        count,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "revisions_failed", err.message);
  }
}

export async function caRevisionsAdd(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const asset = await resolveAsset(db, id);
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const body = reqBody || {};
    const params: string[] = [];
    const values: any[] = [];
    const push = (name: string, val: any) => { values.push(val); params.push(`${name} => $${values.length}`); };

    push("p_asset_id", asset.id);
    if (body.revisionId !== undefined) push("p_revision_id", body.revisionId);
    if (body.contentHash !== undefined) push("p_content_hash", body.contentHash);
    if (body.sourceHash !== undefined) push("p_source_hash", body.sourceHash);
    if (body.parentRevisionId !== undefined) push("p_parent_revision_id", body.parentRevisionId);
    if (body.recordingStart !== undefined) push("p_recording_start", body.recordingStart);
    if (body.recordingEnd !== undefined) push("p_recording_end", body.recordingEnd);
    if (body.createdBy !== undefined) push("p_created_by", body.createdBy);

    const { rows: [revision] } = await db.query(
      `SELECT * FROM semantics.add_asset_revision(${params.join(", ")})`,
      values,
    );
    return { status: 201, body: revision };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    const isDup = err?.code === "23505";
    throw fail(isDup ? 400 : 500, isDup ? "duplicate_active_key" : "add_revision_failed", err.message);
  }
}

export async function caClaimsList(id: string): Promise<HandlerResult> {
  try {
    const db = getDb();

    const { rows: [asset] } = await db.query(
      "SELECT id, canonical_asset_id, asset_kind FROM semantics.canonical_asset WHERE (id::text = $1 OR canonical_asset_id = $1) AND expired_at IS NULL LIMIT 1",
      [id],
    );
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const { rows: claims } = await db.query(
      `SELECT aic.*,
              json_build_object(
                'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
              ) AS "candidateAsset"
       FROM semantics.asset_identity_claim aic
       LEFT JOIN semantics.canonical_asset ca ON ca.id = aic.candidate_asset_id AND ca.expired_at IS NULL
       WHERE aic.asset_id = $1 AND aic.expired_at IS NULL
       ORDER BY aic.created_at DESC`,
      [asset.id],
    );

    return {
      status: 200,
      body: {
        asset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
        claims: (claims || []).map((c: any) => ({
          id: c.id,
          claimType: c.claim_type,
          confidence: c.confidence,
          basis: c.basis,
          status: c.status,
          decidedBy: c.decided_by,
          decidedAt: c.decided_at,
          createdAt: c.created_at,
          candidateAsset: c.candidateAsset || null,
        })),
        count: claims.length,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "claims_failed", err.message);
  }
}

export async function caClaimsAdd(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const asset = await resolveAsset(db, id);
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const body = reqBody || {};
    const params: string[] = [];
    const values: any[] = [];
    const push = (name: string, val: any) => { values.push(val); params.push(`${name} => $${values.length}`); };

    push("p_asset_id", asset.id);
    if (body.candidateAssetId !== undefined) push("p_candidate_asset_id", body.candidateAssetId);
    if (body.claimType !== undefined) push("p_claim_type", body.claimType);
    if (body.confidence !== undefined) push("p_confidence", body.confidence);
    if (body.basis !== undefined) push("p_basis", body.basis);
    if (body.status !== undefined) push("p_status", body.status);
    if (body.decidedBy !== undefined) push("p_decided_by", body.decidedBy);

    const { rows: [claim] } = await db.query(
      `SELECT * FROM semantics.add_asset_identity_claim(${params.join(", ")})`,
      values,
    );
    return { status: 201, body: claim };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    const isDup = err?.code === "23505";
    throw fail(isDup ? 400 : 500, isDup ? "duplicate_active_key" : "add_claim_failed", err.message);
  }
}

export async function caRelationsList(id: string): Promise<HandlerResult> {
  try {
    const db = getDb();

    const { rows: [asset] } = await db.query(
      "SELECT id, canonical_asset_id, asset_kind FROM semantics.canonical_asset WHERE (id::text = $1 OR canonical_asset_id = $1) AND expired_at IS NULL LIMIT 1",
      [id],
    );
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const { rows: relations } = await db.query(
      `SELECT ar.*,
              json_build_object(
                'id', ca.id, 'canonicalAssetId', ca.canonical_asset_id,
                'assetKind', ca.asset_kind, 'canonicalKey', ca.canonical_key
              ) AS "relatedAsset",
              CASE WHEN ar.from_asset_id = $1 THEN 'outbound' ELSE 'inbound' END AS direction
       FROM semantics.asset_relation ar
       JOIN semantics.canonical_asset ca ON ca.id =
         CASE WHEN ar.from_asset_id = $1 THEN ar.to_asset_id ELSE ar.from_asset_id END
         AND ca.expired_at IS NULL
       WHERE (ar.from_asset_id = $1 OR ar.to_asset_id = $1)
         AND ar.expired_at IS NULL
       ORDER BY ar.effective_at DESC`,
      [asset.id],
    );

    return {
      status: 200,
      body: {
        asset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
        relations: (relations || []).map((r: any) => ({
          id: r.id,
          relationType: r.relation_type,
          direction: r.direction,
          effectiveAt: r.effective_at,
          decidedBy: r.decided_by,
          decidedAt: r.decided_at,
          relatedAsset: r.relatedAsset,
        })),
        count: relations.length,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "relations_failed", err.message);
  }
}

export async function caRelationsAdd(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const asset = await resolveAsset(db, id);
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const body = reqBody || {};
    if (!body.relatedAssetId) {
      throw fail(400, "missing_field", "relatedAssetId is required");
    }
    if (!body.relationType) {
      throw fail(400, "missing_field", "relationType is required");
    }

    const related = await resolveAsset(db, body.relatedAssetId);
    if (!related) {
      throw fail(404, "not_found", `related asset ${body.relatedAssetId} not found`);
    }

    if (asset.id === related.id) {
      throw fail(400, "self_relation", "Cannot relate an asset to itself");
    }

    const params: string[] = [];
    const values: any[] = [];
    const push = (name: string, val: any) => { values.push(val); params.push(`${name} => $${values.length}`); };

    push("p_from_asset_id", asset.id);
    push("p_to_asset_id", related.id);
    push("p_relation_type", body.relationType);
    if (body.decidedBy !== undefined) push("p_decided_by", body.decidedBy);
    if (body.effectiveAt !== undefined) push("p_effective_at", body.effectiveAt);

    const { rows: [relation] } = await db.query(
      `SELECT * FROM semantics.add_asset_relation(${params.join(", ")})`,
      values,
    );

    return {
      status: 201,
      body: {
        ...relation,
        fromAsset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
        toAsset: { id: related.id, canonicalAssetId: related.canonical_asset_id, assetKind: related.asset_kind },
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    const isDup = err?.code === "23505";
    throw fail(isDup ? 400 : 500, isDup ? "duplicate_active_key" : "add_relation_failed", err.message);
  }
}

export async function claimResolve(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const body = reqBody || {};
    const status = body.status;

    if (!status || !["resolved", "rejected"].includes(status)) {
      throw fail(400, "invalid_status", "status must be 'resolved' or 'rejected'");
    }

    const { rows: [claim] } = await db.query(
      "SELECT * FROM semantics.asset_identity_claim WHERE id = $1 AND expired_at IS NULL",
      [id],
    );
    if (!claim) {
      throw fail(404, "not_found", `claim ${id} not found`);
    }
    if (claim.status !== "open") {
      throw fail(400, "invalid_transition", `Claim is already ${claim.status} — only 'open' claims can be resolved`);
    }

    const { rows: [updated] } = await db.query(
      `SELECT * FROM semantics.update_asset_identity_claim(
         p_id => $1, p_asset_id => $2, p_candidate_asset_id => $3,
         p_claim_type => $4, p_confidence => $5, p_basis => $6,
         p_status => $7, p_decided_by => $8, p_decided_at => $9
       )`,
      [
        id,
        claim.asset_id,
        claim.candidate_asset_id,
        claim.claim_type,
        claim.confidence,
        claim.basis,
        status,
        body.decidedBy || claim.decided_by || null,
        new Date().toISOString(),
      ],
    );

    return {
      status: 200,
      body: {
        ...updated,
        supersededId: id,
        previousStatus: "open",
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "resolve_failed", err.message);
  }
}

export async function caExtIdsList(id: string): Promise<HandlerResult> {
  try {
    const db = getDb();

    const { rows: [asset] } = await db.query(
      "SELECT id, canonical_asset_id, asset_kind FROM semantics.canonical_asset WHERE (id::text = $1 OR canonical_asset_id = $1) AND expired_at IS NULL LIMIT 1",
      [id],
    );
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    let externalIds: any[] = [];
    try {
      const { rows } = await db.query(
        `SELECT ar.id, ar.relation_type AS "relationType",
                ar.effective_at AS "effectiveAt",
                json_build_object(
                  'id', ns.id, 'name', ns.name,
                  'description', ns.description
                ) AS "nebulaSystem"
         FROM semantics.asset_relation ar
         JOIN nebula.systems ns ON ns.asset_id = ar.from_asset_id
         WHERE ar.to_asset_id = $1
           AND ar.expired_at IS NULL
         ORDER BY ar.effective_at DESC`,
        [asset.id],
      );
      externalIds = rows;
    } catch {
      // nebula schema may not be accessible in all environments — graceful degrade
    }

    return {
      status: 200,
      body: {
        asset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
        externalIds,
        count: externalIds.length,
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "external_ids_failed", err.message);
  }
}

export async function caExtIdsAdd(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const db = getDb();
    const asset = await resolveAsset(db, id);
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const body = reqBody || {};
    if (!body.nebulaSystemId) {
      throw fail(400, "missing_field", "nebulaSystemId is required");
    }

    const { rows: [sys] } = await db.query(
      "SELECT id, name, description, asset_id FROM nebula.systems WHERE id = $1",
      [body.nebulaSystemId],
    );
    if (!sys) {
      throw fail(404, "not_found", `nebula system ${body.nebulaSystemId} not found`);
    }
    if (!sys.asset_id) {
      throw fail(400, "no_asset", `nebula system ${body.nebulaSystemId} has no asset_id — run V075 first`);
    }

    // Check for existing relation (idempotent guard)
    const { rows: [existing] } = await db.query(
      `SELECT id FROM semantics.asset_relation
       WHERE from_asset_id = $1 AND to_asset_id = $2
         AND relation_type = $3 AND expired_at IS NULL`,
      [sys.asset_id, asset.id, body.relationType || "owns"],
    );
    if (existing) {
      throw fail(409, "duplicate_active_key", "An active relation already exists between these assets");
    }

    const { rows: [relation] } = await db.query(
      `SELECT * FROM semantics.add_asset_relation(
         p_from_asset_id => $1, p_to_asset_id => $2,
         p_relation_type => $3, p_decided_by => $4
       )`,
      [sys.asset_id, asset.id, body.relationType || "owns", body.decidedBy || null],
    );

    return {
      status: 201,
      body: {
        ...relation,
        nebulaSystem: { id: sys.id, name: sys.name, description: sys.description },
        canonicalAsset: { id: asset.id, canonicalAssetId: asset.canonical_asset_id, assetKind: asset.asset_kind },
      },
    };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    const isDup = err?.code === "23505";
    throw fail(isDup ? 409 : 500, isDup ? "duplicate_active_key" : "link_failed", err.message);
  }
}

export async function caExtIdsRemove(id: string, eid: string): Promise<HandlerResult> {
  try {
    const db = getDb();

    const asset = await resolveAsset(db, id);
    if (!asset) {
      throw fail(404, "not_found", `canonical_asset ${id} not found`);
    }

    const { rows: [result] } = await db.query(
      `UPDATE semantics.asset_relation
       SET expired_at = now()
       WHERE id = $1
         AND to_asset_id = $2
         AND expired_at IS NULL
       RETURNING id`,
      [eid, asset.id],
    );

    if (!result) {
      throw fail(404, "not_found", `Relation ${eid} not found or already expired for this asset`);
    }

    return { status: 200, body: { id: eid, deleted: true } };
  } catch (err: any) {
    if (err?.name === "MoleculerError") throw err;
    throw fail(500, "unlink_failed", err.message);
  }
}

// ── Drift lifecycle (verbatim) ───────────────────────────────────────

export async function driftResolve(id: string, reqBody: Record<string, any>): Promise<HandlerResult> {
  try {
    const resolvedAt = (reqBody || {}).p_resolved_at ?? null;
    const { rows } = await getDb().query(
      "SELECT semantics.resolve_drift_finding($1, $2) AS resolved",
      [id, resolvedAt],
    );
    return { status: 200, body: { id, resolved: rows[0].resolved } };
  } catch (err: any) {
    throw fail(500, "resolve_failed", err.message);
  }
}
