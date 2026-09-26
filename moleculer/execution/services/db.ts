// execution twin — pg Pool matching the incumbent's index.ts configuration.
//
// The execution schema lives in the same `nexus` database as the rest of
// the system. We pin search_path to execution as the default namespace so
// unqualified table names resolve there, but cross-schema joins to
// vision.receipts (nebula.receipts_unified, resolution.*) still work
// because they are qualified explicitly.
import { Pool } from "pg";

export const pool = new Pool({
  host: process.env.PGHOST || "localhost",
  port: process.env.PGPORT ? parseInt(process.env.PGPORT) : 5432,
  user: process.env.PGUSER || "pguser",
  password: process.env.PGPASSWORD || "pgpass",
  database: process.env.PGDATABASE || "nexus",
  options: "-c search_path=execution",
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

export async function closePool(): Promise<void> {
  await pool.end();
}

export default pool;
