// ── db.ts ────────────────────────────────────────────────────────────────────
// Port of python/substance/db.py.
//
// asyncpg's `set_type_codec` for json/jsonb has no direct analogue because
// node-postgres already parses json/jsonb columns into JS values on the way
// out and accepts objects on the way in. The Python init hook is therefore
// deliberately not reproduced — its only effect is already the default here.

import { Pool, type PoolClient, type QueryResult, type QueryResultRow } from "pg";

import { getSettings } from "./config";

let _pool: Pool | null = null;

/** Create the pool if it does not exist yet. Idempotent. */
export function initPool(): Pool {
  if (_pool === null) {
    _pool = new Pool({
      connectionString: getSettings().postgresDsn,
      min: 2,
      max: 10,
    });
  }
  return _pool;
}

/** Close the pool and clear the singleton. */
export async function closePool(): Promise<void> {
  if (_pool !== null) {
    await _pool.end();
    _pool = null;
  }
}

/** Get the pool. Throws if the service has not initialised it at startup. */
export function getPool(): Pool {
  if (_pool === null) {
    throw new Error("DB pool not initialized — call initPool() during app startup");
  }
  return _pool;
}

/** Run a parameterised query and return every row. */
export async function query<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params: readonly unknown[] = [],
): Promise<T[]> {
  const result: QueryResult<T> = await getPool().query<T>(text, params as unknown[]);
  return result.rows;
}

/** Run a parameterised query and return the first row, or null. */
export async function queryOne<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params: readonly unknown[] = [],
): Promise<T | null> {
  const rows = await query<T>(text, params);
  return rows.length > 0 ? rows[0]! : null;
}

/** Run a statement for its side effect. Returns the affected row count. */
export async function execute(
  text: string,
  params: readonly unknown[] = [],
): Promise<number> {
  const result = await getPool().query(text, params as unknown[]);
  return result.rowCount ?? 0;
}

/**
 * Run `fn` inside a single transaction, committing on resolve and rolling back
 * on throw. Mirrors Python's `async with conn.transaction():` block.
 */
export async function withTransaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await getPool().connect();
  try {
    await client.query("BEGIN");
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (err) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // The connection is already broken; the pool will discard it. The
      // original error is the one worth surfacing.
    }
    throw err;
  } finally {
    client.release();
  }
}
