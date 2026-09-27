// ── Database connection ────────────────────────────────────────────
// Standalone pg Pool for conduit-srv. Uses the same DSN and schema
// search_path as conduit-mcp/src/db.ts, but as an independent connection
// pool (no cross-service imports). This is the "No SQL in MCP Servers"
// pattern: conduit-srv owns the SQL, conduit-mcp delegates to it.
import { Pool, types } from "pg";

// Keep timestamps as ISO strings (matches conduit-mcp convention)
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

const PG_SCHEMA = process.env.CONDUIT_PG_SCHEMA || "conduit";
const VISION_SCHEMA = "vision";
const PEB_SCHEMA = "peb";
const TACKLE_SCHEMA = "tackle";

// SECURITY: validate env-var-derived schema name (same fix as conduit-mcp)
if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(PG_SCHEMA)) {
  throw new Error(
    `Invalid CONDUIT_PG_SCHEMA="${PG_SCHEMA}": must match /^[a-zA-Z_][a-zA-Z0-9_]*$/`
  );
}

const pool = new Pool({
  connectionString:
    process.env.CONDUIT_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus",
  options: `-c search_path=${PG_SCHEMA},${VISION_SCHEMA},${PEB_SCHEMA},${TACKLE_SCHEMA}`,
  max: 10,
  idleTimeoutMillis: 30000,
});

export async function query<T = any>(text: string, params?: any[]): Promise<T[]> {
  const result = await pool.query(text, params);
  return result.rows as T[];
}

export async function queryOne<T = any>(text: string, params?: any[]): Promise<T | undefined> {
  const rows = await query<T>(text, params);
  return rows[0];
}

export async function closePool(): Promise<void> {
  await pool.end();
}

export { pool, PG_SCHEMA, VISION_SCHEMA, PEB_SCHEMA, TACKLE_SCHEMA };
