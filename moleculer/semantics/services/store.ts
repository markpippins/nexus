import { Pool, types } from "pg";

/**
 * Shared store access for the semantics twin (canary :4160).
 *
 * VERBATIM PORT of typescript/semantics-srv/src/db.ts — the twin must read
 * and write the SAME PostgreSQL database (nexus DB, semantics schema
 * referenced fully-qualified) as the incumbent, because parity here is
 * against shared live state (write-canary ruling: the database is the
 * arbiter; both implementations converge on the same tables/procs).
 */

// ── Keep timestamps as ISO strings (consistent with the rest of the fleet) ──
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

// ── Connection (nexus DB; schema = semantics, referenced fully-qualified) ──
const pool = new Pool({
  connectionString:
    process.env.SEMANTICS_PG_DSN ||
    process.env.NEXUS_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus",
  options: "-c search_path=semantics",
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on("error", (err) => {
  console.error("[semantics-twin] idle client error:", err.message);
});

export function getDb(): Pool {
  return pool;
}
