import { Service, ServiceBroker, Context } from "moleculer";
import { Errors } from "moleculer";
import { Pool } from "pg";

/**
 * moleculer port of typescript/knowledge-srv (:3109) — knowledge graph REST.
 *
 * PARITY SURFACE (all from src/routes/knowledge.ts + src/index.ts):
 *   - every SQL statement is VERBATIM (table/column names, WHERE construction,
 *     ORDER BY, LIMIT/OFFSET placeholders, substring abbreviation, ::int casts)
 *   - envelope parity per route family:
 *       reads:      200 {entities|edges|crossReferences, count, limit, offset}
 *       writes:     201 raw returned row
 *       validation: 400 {error: "...required..."}   (exact strings)
 *       misses:     404 {error: "Entity not found: s/id"} etc.
 *       transport:  500 {error: "Failed to ...", message}
 *   - the health triple: 200 {status:"healthy",port,db} (db:"unknown" when the
 *     probe row is not 1 — still 200) / 503 {status:"unhealthy",error}
 *   - the root index body (name/version/port/source/endpoints[]) with the
 *     incumbent's port echoed verbatim (cascade finding: health bodies echo
 *     the incumbent's port even when served from the twin)
 *
 * ERRORS THROUGH MOLECULER: handlers throw MoleculerError(status in .code,
 * full body in .data.envelope); the gateway's onError renders data.envelope.
 * This mirrors the draft port's envelope-carrying mechanism.
 *
 * NOT PORTED: the service-registry heartbeat (heartbeat-client, serviceId
 * 109). A second heartbeating twin would poison the registry's liveness view
 * of knowledge-srv; heartbeat becomes a cutover step, not a canary act.
 *
 * Env names mirror typescript/knowledge-srv/src/db/client.ts exactly
 * (PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD) plus the search_path pin
 * (options: "-c search_path=knowledge,public") that keeps the SQL
 * unambiguous.
 */

const INCUMBENT_PORT_ECHO = 3109; // cascade finding: echo the incumbent's port

// ── helpers (verbatim from routes/knowledge.ts) ──────────────────────

export function intParam(v: unknown, dflt: number, min = 0, max = 500): number {
  const n = v === undefined ? dflt : parseInt(String(v), 10);
  if (Number.isNaN(n)) return dflt;
  return Math.max(min, Math.min(max, n));
}

export function strParam(v: unknown, dflt: string = ""): string {
  return typeof v === "string" ? v : dflt;
}

/** Transport-error thrower: the 500 family envelopes carry error+message. */
export function dbFailure(errorField: string, err: unknown): Error {
  const message = err instanceof Error ? err.message : String(err);
  return new Errors.MoleculerError(message, 500, "KNOWLEDGE_DB_FAILED", {
    envelope: { error: errorField, message },
  });
}

/** Validation/refusal thrower: 4xx envelopes are single-field {error}. */
export function badRequest(errorText: string): Error {
  return new Errors.MoleculerError(errorText, 400, "KNOWLEDGE_BAD_REQUEST", {
    envelope: { error: errorText },
  });
}

/** Miss thrower: 404 envelopes are single-field {error}. */
export function notFound(errorText: string): Error {
  return new Errors.MoleculerError(errorText, 404, "KNOWLEDGE_NOT_FOUND", {
    envelope: { error: errorText },
  });
}

/** Test hooks: pure helpers, no pool needed. */
export const __test = { intParam, strParam, dbFailure, badRequest, notFound, INCUMBENT_PORT_ECHO };

export default class KnowledgeService extends Service {
  public pool: Pool;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.pool = new Pool({
      host: process.env.PGHOST || "localhost",
      port: parseInt(process.env.PGPORT || "5432", 10),
      database: process.env.PGDATABASE || "nexus",
      user: process.env.PGUSER || "pguser",
      password: process.env.PGPASSWORD || "pgpass",
      // Default search_path keeps SQL unambiguous (incumbent db/client.ts).
      options: "-c search_path=knowledge,public",
      max: 10,
      idleTimeoutMillis: 30000,
    });

    this.parseServiceSchema({
      name: "knowledge",

      actions: {
        // ── graph_entities ──────────────────────────────────────────

        // GET /knowledge/entities?section=&entity_type=&status=&search=&limit=&offset=
        "entities.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string | undefined>;
            const section = q.section;
            const entityType = q["entity_type"];
            const status = q.status;
            const search = q.search;
            const limit = intParam(q.limit, 100, 1, 500);
            const offset = intParam(q.offset, 0, 0);

            const conditions: string[] = [];
            const params: any[] = [];
            let i = 1;
            if (section) { conditions.push(`section = $${i++}`); params.push(section); }
            if (entityType) { conditions.push(`entity_type = $${i++}`); params.push(entityType); }
            if (status) { conditions.push(`status = $${i++}`); params.push(status); }
            if (search) { conditions.push(`(name ILIKE $${i} OR description ILIKE $${i})`); params.push(`%${search}%`); i++; }

            const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
            params.push(limit); params.push(offset);

            const sql = `
      SELECT id, section, entity_id, name, entity_type, status,
             substring(description, 1, 500) AS description_abbr,
             created_at, updated_at
      FROM graph_entities
      ${where}
      ORDER BY section, name
      LIMIT $${i++} OFFSET $${i}
    `;
            const countSql = `SELECT COUNT(*)::int AS count FROM graph_entities ${where}`;
            try {
              const [rows, countResult] = await Promise.all([
                this.pool.query({ text: sql, values: params }),
                this.pool.query({ text: countSql, values: params.slice(0, params.length - 2) }),
              ]);
              return {
                entities: rows.rows,
                count: countResult.rows[0]?.count ?? 0,
                limit,
                offset,
              };
            } catch (err: unknown) {
              throw dbFailure("Failed to list entities", err);
            }
          },
        },

        // GET /knowledge/entities/:section/:entity_id
        "entities.get": {
          async handler(ctx: Context<any>) {
            const p = (ctx.params ?? {}) as any;
            try {
              const result = await this.pool.query(
                `SELECT * FROM graph_entities WHERE section = $1 AND entity_id = $2`,
                [p.section, p.entity_id]
              );
              const row = result.rows[0] ?? null;
              if (!row) throw notFound(`Entity not found: ${p.section}/${p.entity_id}`);
              return row;
            } catch (err: unknown) {
              if (err instanceof Errors.MoleculerError) throw err;
              throw dbFailure("Failed to get entity", err);
            }
          },
        },

        // POST /knowledge/entities — upsert on (section, entity_id)
        "entities.create": {
          async handler(ctx: Context<any>) {
            const body = (ctx.params ?? {}) as any;
            const section = strParam(body.section);
            const entity_id = strParam(body.entity_id);
            if (!section || !entity_id) {
              throw badRequest("section and entity_id are required");
            }
            const name = strParam(body.name, entity_id);
            const entity_type = body.entity_type ?? null;
            const status = body.status ?? null;
            const description = body.description ?? null;
            const properties = body.properties ?? {};
            const source_file = body.source_file ?? null;
            try {
              const result = await this.pool.query(
                `INSERT INTO graph_entities (section, entity_id, name, entity_type, status, description, properties, source_file)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
       ON CONFLICT (section, entity_id) DO UPDATE SET
         name = EXCLUDED.name,
         entity_type = EXCLUDED.entity_type,
         status = EXCLUDED.status,
         description = EXCLUDED.description,
         properties = EXCLUDED.properties,
         source_file = EXCLUDED.source_file,
         updated_at = now()
       RETURNING id, section, entity_id, name, entity_type, status, created_at, updated_at`,
                [section, entity_id, name, entity_type, status, description, properties, source_file]
              );
              return result.rows[0];
            } catch (err: unknown) {
              throw dbFailure("Failed to create entity", err);
            }
          },
        },

        // PUT /knowledge/entities/:section/:entity_id
        "entities.update": {
          async handler(ctx: Context<any>) {
            // moleculer-web merges path params + JSON body into ctx.params.
            const p = (ctx.params ?? {}) as any;
            const { section, entity_id } = p;
            const name = p.name !== undefined ? p.name : null;
            const entity_type = p.entity_type !== undefined ? p.entity_type : null;
            const status = p.status !== undefined ? p.status : null;
            const description = p.description !== undefined ? p.description : null;
            const properties = p.properties !== undefined ? p.properties : null;
            try {
              const result = await this.pool.query(
                `UPDATE graph_entities SET
         name = COALESCE($3, name),
         entity_type = COALESCE($4, entity_type),
         status = COALESCE($5, status),
         description = COALESCE($6, description),
         properties = COALESCE($7, properties),
         updated_at = now()
       WHERE section = $1 AND entity_id = $2
       RETURNING id, section, entity_id, name, entity_type, status, description, properties, updated_at`,
                [section, entity_id, name, entity_type, status, description, properties]
              );
              const row = result.rows[0] ?? null;
              if (!row) throw notFound(`Entity not found: ${section}/${entity_id}`);
              return row;
            } catch (err: unknown) {
              if (err instanceof Errors.MoleculerError) throw err;
              throw dbFailure("Failed to update entity", err);
            }
          },
        },

        // DELETE /knowledge/entities/:section/:entity_id (cascades edges)
        "entities.delete": {
          async handler(ctx: Context<any>) {
            const { section, entity_id } = (ctx.params ?? {}) as any;
            try {
              const result = await this.pool.query(
                `DELETE FROM graph_entities WHERE section = $1 AND entity_id = $2
       RETURNING id, section, entity_id`,
                [section, entity_id]
              );
              const row = result.rows[0] ?? null;
              if (!row) throw notFound(`Entity not found: ${section}/${entity_id}`);
              return { deleted: true, ...row };
            } catch (err: unknown) {
              if (err instanceof Errors.MoleculerError) throw err;
              throw dbFailure("Failed to delete entity", err);
            }
          },
        },

        // DELETE /knowledge/entities?section=... — purge a whole section
        "entities.purge": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string | undefined>;
            const section = strParam(q.section);
            if (!section) {
              throw badRequest("section query param is required for purge");
            }
            try {
              const result = await this.pool.query(
                `DELETE FROM graph_entities WHERE section = $1 RETURNING id`,
                [section]
              );
              return { purged: true, section, deleted: result.rows.length };
            } catch (err: unknown) {
              throw dbFailure("Failed to purge section", err);
            }
          },
        },

        // GET /knowledge/entities/:section/:entity_id/relations
        "entities.relations": {
          async handler(ctx: Context<any>) {
            const { section, entity_id: entityId } = (ctx.params ?? {}) as any;
            try {
              const [outbound, inbound] = await Promise.all([
                this.pool.query(
                  `SELECT e.id, e.relation_type, e.target_section, e.target_id, e.properties, tgt.name AS target_name
         FROM graph_edges e
         LEFT JOIN graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
         WHERE e.source_section = $1 AND e.source_id = $2
         ORDER BY e.relation_type`,
                  [section, entityId]
                ),
                this.pool.query(
                  `SELECT e.id, e.relation_type, e.source_section, e.source_id, e.properties, src.name AS source_name
         FROM graph_edges e
         LEFT JOIN graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
         WHERE e.target_section = $1 AND e.target_id = $2
         ORDER BY e.relation_type`,
                  [section, entityId]
                ),
              ]);

              return {
                entity: { section, entity_id: entityId },
                outbound: { count: outbound.rows.length, edges: outbound.rows },
                inbound: { count: inbound.rows.length, edges: inbound.rows },
              };
            } catch (err: unknown) {
              throw dbFailure("Failed to list relations", err);
            }
          },
        },

        // ── graph_edges ─────────────────────────────────────────────

        // GET /knowledge/edges?source_section=&source_id=&target_section=&target_id=&relation_type=&limit=&offset=
        "edges.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string | undefined>;
            const sourceSection = q["source_section"];
            const sourceId = q["source_id"];
            const targetSection = q["target_section"];
            const targetId = q["target_id"];
            const relationType = q["relation_type"];
            const limit = intParam(q.limit, 100, 1, 500);
            const offset = intParam(q.offset, 0, 0);

            const conditions: string[] = [];
            const params: any[] = [];
            let i = 1;
            if (sourceSection) { conditions.push(`e.source_section = $${i++}`); params.push(sourceSection); }
            if (sourceId) { conditions.push(`e.source_id = $${i++}`); params.push(sourceId); }
            if (targetSection) { conditions.push(`e.target_section = $${i++}`); params.push(targetSection); }
            if (targetId) { conditions.push(`e.target_id = $${i++}`); params.push(targetId); }
            if (relationType) { conditions.push(`e.relation_type = $${i++}`); params.push(relationType); }

            const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
            params.push(limit); params.push(offset);

            const sql = `
      SELECT e.id, e.source_section, e.source_id, e.relation_type,
             e.target_section, e.target_id, e.properties, e.created_at,
             src.name AS source_name, tgt.name AS target_name
      FROM graph_edges e
      LEFT JOIN graph_entities src ON src.section = e.source_section AND src.entity_id = e.source_id
      LEFT JOIN graph_entities tgt ON tgt.section = e.target_section AND tgt.entity_id = e.target_id
      ${where}
      ORDER BY e.source_section, e.source_id, e.relation_type
      LIMIT $${i++} OFFSET $${i}
    `;
            const countParams = params.slice(0, params.length - 2);
            const countSql = `SELECT COUNT(*)::int AS count FROM graph_edges e ${where}`;
            try {
              const [rows, countResult] = await Promise.all([
                this.pool.query({ text: sql, values: params }),
                this.pool.query({ text: countSql, values: countParams }),
              ]);
              return { edges: rows.rows, count: countResult.rows[0]?.count ?? 0, limit, offset };
            } catch (err: unknown) {
              throw dbFailure("Failed to list edges", err);
            }
          },
        },

        // POST /knowledge/edges — upsert on the natural key
        "edges.create": {
          async handler(ctx: Context<any>) {
            const body = (ctx.params ?? {}) as any;
            const source_section = strParam(body.source_section);
            const source_id = strParam(body.source_id);
            const relation_type = strParam(body.relation_type);
            const target_section = body.target_section ?? null;
            const target_id = strParam(body.target_id);
            if (!source_section || !source_id || !relation_type || !target_id) {
              throw badRequest("source_section, source_id, relation_type, target_id are required");
            }
            const properties = body.properties ?? {};
            const resolution = body.resolution ?? "resolved";
            try {
              const result = await this.pool.query(
                `INSERT INTO graph_edges (source_section, source_id, relation_type, target_section, target_id, properties, resolution)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (source_section, source_id, relation_type, target_section, target_id)
       DO UPDATE SET properties = EXCLUDED.properties, resolution = EXCLUDED.resolution
       RETURNING id, source_section, source_id, relation_type, target_section, target_id, resolution`,
                [source_section, source_id, relation_type, target_section, target_id, properties, resolution]
              );
              return result.rows[0];
            } catch (err: unknown) {
              throw dbFailure("Failed to create edge", err);
            }
          },
        },

        // DELETE /knowledge/edges/:id — by UUID
        "edges.delete": {
          async handler(ctx: Context<any>) {
            const { id } = (ctx.params ?? {}) as any;
            try {
              const result = await this.pool.query(
                `DELETE FROM graph_edges WHERE id = $1 RETURNING id`,
                [id]
              );
              const row = result.rows[0] ?? null;
              if (!row) throw notFound(`Edge not found: ${id}`);
              return { deleted: true, id: row.id };
            } catch (err: unknown) {
              if (err instanceof Errors.MoleculerError) throw err;
              throw dbFailure("Failed to delete edge", err);
            }
          },
        },

        // ── graph_cross_references ──────────────────────────────────

        // POST /knowledge/cross-references
        "xrefs.create": {
          async handler(ctx: Context<any>) {
            const body = (ctx.params ?? {}) as any;
            const map_name = strParam(body.map_name);
            const source_section = body.source_section ?? null;
            const source_id = body.source_id ?? null;
            const target_section = body.target_section ?? null;
            const target_id = strParam(body.target_id);
            const weight = body.weight ?? 1.0;
            if (!map_name || !target_id) {
              throw badRequest("map_name and target_id are required");
            }
            try {
              const result = await this.pool.query(
                `INSERT INTO graph_cross_references (map_name, source_section, source_id, target_section, target_id, weight)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, map_name, source_section, source_id, target_section, target_id, weight`,
                [map_name, source_section, source_id, target_section, target_id, weight]
              );
              return result.rows[0];
            } catch (err: unknown) {
              throw dbFailure("Failed to create cross-reference", err);
            }
          },
        },

        // DELETE /knowledge/cross-references/:id — by UUID
        "xrefs.delete": {
          async handler(ctx: Context<any>) {
            const { id } = (ctx.params ?? {}) as any;
            try {
              const result = await this.pool.query(
                `DELETE FROM graph_cross_references WHERE id = $1 RETURNING id`,
                [id]
              );
              const row = result.rows[0] ?? null;
              if (!row) throw notFound(`Cross-reference not found: ${id}`);
              return { deleted: true, id: row.id };
            } catch (err: unknown) {
              if (err instanceof Errors.MoleculerError) throw err;
              throw dbFailure("Failed to delete cross-reference", err);
            }
          },
        },

        // GET /knowledge/cross-references?map_name=&source_section=&target_id=&limit=&offset=
        "xrefs.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string | undefined>;
            const mapName = q["map_name"];
            const sourceSection = q["source_section"];
            const targetId = q["target_id"];
            const limit = intParam(q.limit, 100, 1, 500);
            const offset = intParam(q.offset, 0, 0);

            const conditions: string[] = [];
            const params: any[] = [];
            let i = 1;
            if (mapName) { conditions.push(`xr.map_name = $${i++}`); params.push(mapName); }
            if (sourceSection) { conditions.push(`xr.source_section = $${i++}`); params.push(sourceSection); }
            if (targetId) { conditions.push(`xr.target_id = $${i++}`); params.push(targetId); }

            const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
            params.push(limit); params.push(offset);

            const sql = `
      SELECT xr.id, xr.map_name, xr.source_section, xr.source_id,
             xr.target_section, xr.target_id, xr.weight, xr.created_at
      FROM graph_cross_references xr
      ${where}
      ORDER BY xr.map_name, xr.target_id
      LIMIT $${i++} OFFSET $${i}
    `;
            const countParams = params.slice(0, params.length - 2);
            const countSql = `SELECT COUNT(*)::int AS count FROM graph_cross_references xr ${where}`;
            try {
              const [rows, countResult] = await Promise.all([
                this.pool.query({ text: sql, values: params }),
                this.pool.query({ text: countSql, values: countParams }),
              ]);
              return { crossReferences: rows.rows, count: countResult.rows[0]?.count ?? 0, limit, offset };
            } catch (err: unknown) {
              throw dbFailure("Failed to list cross-references", err);
            }
          },
        },

        // ── graph_migrations ────────────────────────────────────────

        // GET /knowledge/migrations?limit=
        "migrations.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string | undefined>;
            const limit = intParam(q.limit, 20, 1, 100);
            try {
              const result = await this.pool.query(
                `SELECT id, source_file, file_checksum, entity_count, edge_count,
              cross_ref_count, version, migrated_at
       FROM graph_migrations
       ORDER BY migrated_at DESC
       LIMIT $1`,
                [limit]
              );
              return { migrations: result.rows, count: result.rows.length, limit };
            } catch (err: unknown) {
              throw dbFailure("Failed to list migrations", err);
            }
          },
        },

        // ── summary ─────────────────────────────────────────────────

        // GET /knowledge/summary
        "summary": {
          async handler() {
            try {
              const [entityCount, edgeCount, xrefCount, migrationCount, sections, relationTypes] =
                await Promise.all([
                  this.pool.query("SELECT COUNT(*)::int AS count FROM graph_entities"),
                  this.pool.query("SELECT COUNT(*)::int AS count FROM graph_edges"),
                  this.pool.query("SELECT COUNT(*)::int AS count FROM graph_cross_references"),
                  this.pool.query("SELECT COUNT(*)::int AS count FROM graph_migrations"),
                  this.pool.query(
                    "SELECT section, COUNT(*)::int AS count FROM graph_entities GROUP BY section ORDER BY count DESC"
                  ),
                  this.pool.query(
                    "SELECT relation_type, COUNT(*)::int AS count FROM graph_edges GROUP BY relation_type ORDER BY count DESC"
                  ),
                ]);
              return {
                entityCount: entityCount.rows[0]?.count ?? 0,
                edgeCount: edgeCount.rows[0]?.count ?? 0,
                crossReferenceCount: xrefCount.rows[0]?.count ?? 0,
                migrationCount: migrationCount.rows[0]?.count ?? 0,
                bySection: sections.rows,
                byRelationType: relationTypes.rows,
              };
            } catch (err: unknown) {
              throw dbFailure("Failed to compute summary", err);
            }
          },
        },

        // ── health + root index (incumbent index.ts) ───────────────

        "health": {
          async handler() {
            try {
              const r = await this.pool.query("SELECT 1 AS ok");
              // Incumbent: 200 with db:"unknown" if the row is not 1 (still 200!).
              return {
                status: "healthy",
                port: INCUMBENT_PORT_ECHO,
                db: r.rows[0]?.ok === 1 ? "up" : "unknown",
              };
            } catch (err: unknown) {
              // Incumbent 503: {status:"unhealthy", error}. Non-200 statuses
              // must go through the throw/onError path (moleculer-web has no
              // per-response status mechanism for plain action returns).
              const message = err instanceof Error ? err.message : String(err);
              throw new Errors.MoleculerError(message, 503, "KNOWLEDGE_UNHEALTHY", {
                envelope: { status: "unhealthy", error: message },
              });
            }
          },
        },

        "root": {
          async handler() {
            return {
              name: "knowledge-srv",
              version: "1.0.0",
              port: INCUMBENT_PORT_ECHO,
              source: "knowledge.postgres (graph_entities, graph_edges, graph_cross_references, graph_migrations)",
              endpoints: [
                "GET  /knowledge/entities",
                "POST /knowledge/entities",
                "GET  /knowledge/entities/:section/:entity_id",
                "PUT  /knowledge/entities/:section/:entity_id",
                "DELETE /knowledge/entities/:section/:entity_id",
                "DELETE /knowledge/entities?section=...  (purge section)",
                "GET  /knowledge/entities/:section/:entity_id/relations",
                "GET  /knowledge/edges",
                "POST /knowledge/edges",
                "DELETE /knowledge/edges/:id",
                "GET  /knowledge/cross-references",
                "POST /knowledge/cross-references",
                "DELETE /knowledge/cross-references/:id",
                "GET  /knowledge/migrations",
                "GET  /knowledge/summary",
                "GET  /health",
              ],
            };
          },
        },
      },
    });
  }
}
