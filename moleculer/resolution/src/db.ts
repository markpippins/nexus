import { Pool, types } from "pg";

// ── Keep timestamps as ISO strings (consistent with the rest of the fleet) ──
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

// ── Connection (nexus DB; resolution.* referenced fully-qualified) ──
const pool = new Pool({
  connectionString:
    process.env.RESOLUTION_PG_DSN ||
    process.env.NEXUS_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus",
  options: "-c search_path=resolution",
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on("error", (err) => {
  console.error("[resolution-srv] idle client error:", err.message);
});

export function getDb(): Pool {
  return pool;
}

// TWIN ADDITION (moleculer): the incumbent's index.ts ends the pool in its
// process-level shutdown; the twin's broker stopped() hook uses this export
// instead (same semantics).
export async function closePool(): Promise<void> {
  await pool.end();
}
