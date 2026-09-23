import { Pool } from 'pg';
import { ConnSpec, DatabaseDiscovery, DbDriver, EngineCapabilities, QueryResult, SchemaDiscovery } from './types';

/** Map pg column type OID to a readable type name for the results grid. */
const PG_TYPES: Record<number, string> = {
  16: 'BOOLEAN', 17: 'BYTEA', 20: 'BIGINT', 21: 'SMALLINT', 23: 'INTEGER',
  25: 'TEXT', 700: 'REAL', 701: 'DOUBLE PRECISION', 1042: 'CHAR',
  1043: 'VARCHAR', 1082: 'DATE', 1083: 'TIME', 1114: 'TIMESTAMP',
  1184: 'TIMESTAMPTZ', 1186: 'INTERVAL', 1700: 'NUMERIC', 2950: 'UUID',
  3802: 'JSONB', 114: 'JSON', 26: 'OID', 600: 'POINT', 1140: 'MONEY',
};

/**
 * PostgreSQL driver. Credentials arrive per request (user-entered in the
 * workbench UI). Pools are cached per unique ConnSpec (max:1 connection each)
 * so repeat calls skip TCP+auth setup — significant for remote hosts like
 * barium (~2s connect). Pools idle out and are fully closed after
 * POOL_IDLE_TTL_MS without use, and the cache is size-capped; nothing is
 * persisted to disk. This trades the previous strict zero-retention posture
 * for a bounded, time-limited in-memory cache.
 */
const POOL_IDLE_TTL_MS = 120_000;
const POOL_CACHE_MAX = 8;

interface CachedPool {
  pool: Pool;
  lastUsed: number;
}

export class PostgresDriver implements DbDriver {
  readonly capabilities: EngineCapabilities = {
    id: 'postgres',
    label: 'PostgreSQL',
    defaultPort: 5432,
    available: true,
    missingDeps: [],
    supportsDdl: true,
    supportsSchemas: true,
  };

  private poolCache = new Map<string, CachedPool>();
  private sweeper: NodeJS.Timeout | null = null;

  /**
   * TLS verification policy (Audit II D3): certificate verification is ON by
   * default. Escape hatch for self-signed LAN certs: set
   * NEXUS_PG_TLS_INSECURE=1 (documented risk — MITM possible on that path).
   */
  private tlsConfig(): { rejectUnauthorized: boolean } {
    return { rejectUnauthorized: process.env.NEXUS_PG_TLS_INSECURE !== '1' };
  }

  private specKey(spec: ConnSpec): string {
    return JSON.stringify([
      spec.host || 'localhost',
      String(spec.port || this.capabilities.defaultPort),
      spec.database || 'postgres',
      spec.username || 'postgres',
      spec.password || '',
      !!spec.ssl,
    ]);
  }

  /** Get (or create) the cached max:1 pool for this exact ConnSpec. */
  private pooled(spec: ConnSpec): Pool {
    const key = this.specKey(spec);
    const hit = this.poolCache.get(key);
    if (hit) {
      hit.lastUsed = Date.now();
      // Move to end for FIFO eviction ordering.
      this.poolCache.delete(key);
      this.poolCache.set(key, hit);
      return hit.pool;
    }
    const pool = new Pool({
      host: spec.host || 'localhost',
      port: parseInt(String(spec.port || this.capabilities.defaultPort), 10),
      database: spec.database || 'postgres',
      user: spec.username || 'postgres',
      password: spec.password || '',
      ssl: spec.ssl ? this.tlsConfig() : undefined,
      max: 1,
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 5000,
      statement_timeout: 15000,
    });
    this.poolCache.set(key, { pool, lastUsed: Date.now() });
    // Size cap: evict oldest-inserted when over budget.
    while (this.poolCache.size > POOL_CACHE_MAX) {
      const oldestKey = this.poolCache.keys().next().value as string | undefined;
      if (!oldestKey) break;
      const evicted = this.poolCache.get(oldestKey);
      this.poolCache.delete(oldestKey);
      void evicted?.pool.end().catch(() => {});
    }
    this.ensureSweeper();
    return pool;
  }

  /** Periodically close pools that have not been used within the TTL. */
  private ensureSweeper(): void {
    if (this.sweeper) return;
    this.sweeper = setInterval(() => {
      const now = Date.now();
      for (const [key, entry] of this.poolCache) {
        if (now - entry.lastUsed > POOL_IDLE_TTL_MS) {
          this.poolCache.delete(key);
          void entry.pool.end().catch(() => {});
        }
      }
      if (this.poolCache.size === 0 && this.sweeper) {
        clearInterval(this.sweeper);
        this.sweeper = null;
      }
    }, 30_000);
    this.sweeper.unref();
  }

  async testConnection(spec: ConnSpec) {
    const started = Date.now();
    // Fresh short-lived pool on purpose: a credential test must prove a NEW
    // connection works, not replay a cached one.
    const pool = new Pool({
      host: spec.host || 'localhost',
      port: parseInt(String(spec.port || this.capabilities.defaultPort), 10),
      database: spec.database || 'postgres',
      user: spec.username || 'postgres',
      password: spec.password || '',
      ssl: spec.ssl ? this.tlsConfig() : undefined,
      max: 1,
      connectionTimeoutMillis: 5000,
      statement_timeout: 15000,
    });
    try {
      const { rows } = await pool.query('SELECT version() AS version, current_database() AS db');
      return {
        success: true,
        message: `Successfully connected to PostgreSQL at ${spec.host || 'localhost'}:${spec.port || 5432}/${spec.database || 'postgres'}`,
        latencyMs: Date.now() - started,
        version: rows[0]?.version || 'PostgreSQL',
        database: rows[0]?.db || spec.database,
      };
    } catch (err: any) {
      return {
        success: false,
        message: `Connection failed: ${err?.message || 'unable to reach server'}`,
        latencyMs: Date.now() - started,
      };
    } finally {
      await pool.end();
    }
  }

  async discoverDatabases(spec: ConnSpec): Promise<{ databases: DatabaseDiscovery[] }> {
    const pool = this.pooled({ ...spec, database: spec.database || 'postgres' });
    const { rows } = await pool.query(`
      SELECT datname AS name,
             datallowconn AS allow_connections,
             datistemplate AS is_template
      FROM pg_database
      WHERE datallowconn = true
      ORDER BY datistemplate, datname`);
    return {
      databases: rows.map((row) => ({
        name: row.name,
        allowConnections: Boolean(row.allow_connections),
        isTemplate: Boolean(row.is_template),
      })),
    };
  }

  async discoverSchemas(spec: ConnSpec): Promise<{ schemas: SchemaDiscovery[] }> {
    const pool = this.pooled(spec);
    try {
      // All catalog probes run in parallel — they are independent reads against
      // different catalogs; sequentially they dominated schema-discovery latency.
      const [
        { rows: schemaRows },
        { rows: tableRows },
        { rows: columnRows },
        { rows: pkRows },
        { rows: fkRows },
        { rows: indexRows },
        { rows: viewRows },
        { rows: triggerRows },
        { rows: procRows },
      ] = await Promise.all([
        await pool.query(`
        SELECT n.nspname AS schema_name
        FROM pg_namespace n
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY n.nspname`),
        await pool.query(`
        SELECT n.nspname AS schema_name,
               c.relname AS table_name,
               c.reltuples::bigint AS row_count,
               obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND n.nspname NOT LIKE 'pg_temp%'
        ORDER BY n.nspname, c.relname`),
        await pool.query(`
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
               c.is_nullable = 'YES' AS is_nullable,
               COALESCE(c.column_default, '') AS column_default
        FROM information_schema.columns c
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND c.table_schema NOT LIKE 'pg_temp%'
        ORDER BY c.table_schema, c.table_name, c.ordinal_position`),
        // PK columns straight from pg_catalog — the per-column correlated
        // EXISTS over information_schema views this replaces cost seconds on
        // schema-heavy databases (5,822 columns × 2 subqueries).
        await pool.query(`
        SELECT ns.nspname AS table_schema,
               cls.relname AS table_name,
               att.attname AS column_name
        FROM pg_constraint pk
        JOIN pg_class cls ON cls.oid = pk.conrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        CROSS JOIN LATERAL unnest(pk.conkey) AS k(attnum)
        JOIN pg_attribute att ON att.attrelid = pk.conrelid AND att.attnum = k.attnum
        WHERE pk.contype = 'p'
          AND ns.nspname NOT IN ('pg_catalog', 'information_schema')`),
        // FK edges via pg_constraint — information_schema.constraint_column_usage
        // is an expensive expansion and alone took ~7.6s on the local nexus DB.
        await pool.query(`
        SELECT ns.nspname AS table_schema,
               cls.relname AS table_name,
               att.attname AS column_name,
               fns.nspname AS ref_schema,
               fcls.relname AS references_table,
               fatt.attname AS references_column
        FROM pg_constraint con
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cls.relnamespace
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ck(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ck.attnum
        JOIN pg_class fcls ON fcls.oid = con.confrelid
        JOIN pg_namespace fns ON fns.oid = fcls.relnamespace
        CROSS JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord)
        JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = cfk.attnum
        WHERE con.contype = 'f' AND ck.ord = cfk.ord`),
        await pool.query(`
        SELECT schemaname, tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
          AND schemaname NOT LIKE 'pg_temp%'
        ORDER BY schemaname, tablename, indexname`),
        await pool.query(`
        SELECT table_schema, table_name, view_definition
        FROM information_schema.views
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND table_schema NOT LIKE 'pg_temp%'
        ORDER BY table_schema, table_name`),
        await pool.query(`
        SELECT n.nspname AS schema_name,
               c.relname AS table_name,
               t.tgname AS trigger_name,
               CASE WHEN (t.tgtype & 2) <> 0 THEN 'BEFORE'
                    WHEN (t.tgtype & 64) <> 0 THEN 'INSTEAD OF'
                    ELSE 'AFTER' END AS timing,
               trim(array_to_string(ARRAY[
                 CASE WHEN (t.tgtype & 4) <> 0 THEN 'INSERT' END,
                 CASE WHEN (t.tgtype & 8) <> 0 THEN 'DELETE' END,
                 CASE WHEN (t.tgtype & 16) <> 0 THEN 'UPDATE' END,
                 CASE WHEN (t.tgtype & 32) <> 0 THEN 'TRUNCATE' END
               ], ', ')) AS event,
               p.proname AS function_name,
               pg_get_triggerdef(t.oid) AS definition
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')`),
        await pool.query(`
        SELECT n.nspname AS schema_name,
               p.proname AS procedure_name,
               pg_get_function_arguments(p.oid) AS arguments,
               pg_get_function_result(p.oid) AS result_type,
               pg_get_functiondef(p.oid) AS definition,
               d.description AS comment
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        LEFT JOIN pg_description d ON d.objoid = p.oid
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND p.prokind IN ('f', 'p')
          AND p.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')`),
      ]);

      const schemaMap: Record<string, SchemaDiscovery> = {};
      const ensure = (name: string): SchemaDiscovery => {
        if (!schemaMap[name]) {
          schemaMap[name] = { name, tables: [], views: [], triggers: [], procedures: [] };
        }
        return schemaMap[name];
      };

      for (const r of schemaRows) ensure(r.schema_name);
      for (const r of tableRows) {
        ensure(r.schema_name).tables.push({
          name: r.table_name,
          schema: r.schema_name,
          rowCount: Number(r.row_count) || 0,
          columns: [],
          indexes: [],
          comment: r.comment ?? undefined,
        });
      }
      // Set-based PK membership — O(1) per column instead of a correlated
      // information_schema probe.
      const pkSet = new Set(pkRows.map((p) => `${p.table_schema}.${p.table_name}.${p.column_name}`));
      for (const r of columnRows) {
        const tbl = schemaMap[r.table_schema]?.tables.find((t) => t.name === r.table_name);
        if (!tbl) continue;
        const fk = fkRows.find(
          (f) => f.table_schema === r.table_schema && f.table_name === r.table_name && f.column_name === r.column_name
        );
        tbl.columns.push({
          name: r.column_name,
          type: String(r.data_type).toUpperCase(),
          isPrimaryKey: pkSet.has(`${r.table_schema}.${r.table_name}.${r.column_name}`),
          isNullable: r.is_nullable,
          isForeignKey: !!fk,
          referencesTable: fk?.references_table,
          referencesColumn: fk?.references_column,
          defaultValue: r.column_default || undefined,
        });
      }
      for (const r of indexRows) {
        const tbl = schemaMap[r.schemaname]?.tables.find((t) => t.name === r.tablename);
        if (!tbl || !tbl.indexes) continue;
        const colMatch = r.indexdef.match(/\(([^)]+)\)/);
        tbl.indexes.push({
          name: r.indexname,
          columns: colMatch ? colMatch[1].split(',').map((c: string) => c.trim()) : [],
          isUnique: /UNIQUE/i.test(r.indexdef),
        });
      }
      for (const r of viewRows) {
        ensure(r.table_schema).views.push({
          name: r.table_name,
          schema: r.table_schema,
          definition: r.view_definition,
        });
      }
      for (const r of triggerRows) {
        ensure(r.schema_name).triggers.push({
          name: r.trigger_name,
          schema: r.schema_name,
          tableName: r.table_name,
          timing: r.timing,
          event: r.event || 'UPDATE',
          functionName: r.function_name || '',
          definition: r.definition,
        });
      }
      for (const r of procRows) {
        ensure(r.schema_name).procedures.push({
          name: r.procedure_name,
          schema: r.schema_name,
          returnType: r.result_type || 'void',
          parameters: String(r.arguments || '')
            .split(',')
            .map((a: string) => a.trim())
            .filter(Boolean)
            .map((a: string) => {
              const parts = a.split(/\s+/);
              return { name: parts[0] || 'arg', type: parts.slice(1).join(' ') || a };
            }),
          definition: r.definition || '',
          comment: r.comment ?? undefined,
        });
      }

      return { schemas: Object.values(schemaMap) };
      // NOTE: no pool.end() — this pool is cached for reuse (see `pooled`).
    } catch (err: any) {
      // A bad ConnSpec poisons its cached pool — evict it so the next attempt
      // starts fresh instead of retrying the same broken connection.
      const key = this.specKey(spec);
      const entry = this.poolCache.get(key);
      if (entry) {
        this.poolCache.delete(key);
        void entry.pool.end().catch(() => {});
      }
      throw err;
    }
  }

  async execute(spec: ConnSpec, sql: string): Promise<QueryResult> {
    const started = Date.now();
    const timestamp = new Date().toLocaleTimeString();
    const pool = this.pooled(spec);
    try {
      const result = await pool.query(String(sql));
      const columns = result.fields?.map((f) => f.name) || [];
      const columnTypes = (result.fields || []).reduce((acc: Record<string, string>, f) => {
        acc[f.name] = PG_TYPES[f.dataTypeID] || `type_${f.dataTypeID}`;
        return acc;
      }, {});
      const rows = (result.rows || []).map((r: any) => {
        const out: Record<string, any> = {};
        for (const k of Object.keys(r)) out[k] = r[k];
        return out;
      });
      return {
        columns,
        columnTypes,
        rows,
        rowCount: rows.length,
        affectedRows: result.rowCount ?? null,
        executionTimeMs: Date.now() - started,
        status: 'success',
        timestamp,
      };
    } catch (err: any) {
      // Errors are data here: the UI renders them as a visible error result.
      // Connection-class failures poison the cached pool — evict it so the
      // next attempt starts fresh; SQL-level errors keep the pool.
      if (err && ['ECONNREFUSED', 'ETIMEDOUT', '28P01', '3D000', '42501', '57P03'].includes(String(err.code))) {
        const key = this.specKey(spec);
        const entry = this.poolCache.get(key);
        if (entry) {
          this.poolCache.delete(key);
          void entry.pool.end().catch(() => {});
        }
      }
      return {
        columns: [],
        rows: [],
        rowCount: 0,
        executionTimeMs: Date.now() - started,
        status: 'error',
        error: err?.message || 'Query failed',
        timestamp,
      };
    }
    // NOTE: no pool.end() — this pool is cached for reuse (see `pooled`).
  }
}
