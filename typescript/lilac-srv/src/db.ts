import { Pool, types } from "pg";
import type { Db, Queryable, QueryResultLike } from "./repository";

// Keep timestamps as ISO strings, consistent with the rest of the fleet.
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

const DSN =
  process.env.LILAC_PG_DSN ||
  process.env.RESOLUTION_PG_DSN ||
  process.env.NEXUS_PG_DSN ||
  "postgresql://pguser:pgpass@localhost:5432/nexus";

const pool = new Pool({
  connectionString: DSN,
  options: "-c search_path=resolution",
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on("error", (err) => {
  console.error("[lilac-srv] idle client error:", err.message);
});

/** Pool-backed Db with explicit transaction ownership. A mutation runs inside
 *  one transaction so a provenance row and its entity row commit together or
 *  not at all — a half-recorded mutation would defeat the envelope. */
export function poolDb(): Db {
  return {
    async query(sql: string, params?: unknown[]): Promise<QueryResultLike> {
      const res = await pool.query(sql, params as unknown[]);
      return { rows: res.rows };
    },
    async transaction<T>(fn: (tx: Queryable) => Promise<T>): Promise<T> {
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        const tx: Queryable = {
          async query(sql: string, params?: unknown[]): Promise<QueryResultLike> {
            const res = await client.query(sql, params as unknown[]);
            return { rows: res.rows };
          },
        };
        const out = await fn(tx);
        await client.query("COMMIT");
        return out;
      } catch (err) {
        try {
          await client.query("ROLLBACK");
        } catch {
          /* connection already gone — nothing left to roll back */
        }
        throw err;
      } finally {
        client.release();
      }
    },
  };
}

export async function assertConnected(db: Db): Promise<void> {
  await db.query("SELECT 1");
}

export function getPool(): Pool {
  return pool;
}
