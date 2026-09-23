import Redis from "ioredis";
import { Pool, types } from "pg";

/**
 * Shared cache + store access for the role-memory twin (canary :4150).
 *
 * VERBATIM PORT of typescript/role-memory-srv/src/redis.ts + db.ts — the
 * twin must read the SAME Redis keys (mem:proc:* / mem:idx:* /
 * mem:meta:last_updated) and the SAME PG tables (tackle.memory,
 * tackle.role_memory) as the incumbent, because parity here is against
 * shared live state (write-canary ruling: the database is the arbiter).
 * All the incumbent's resilience comments are load-bearing doctrine — kept.
 */

// ── Redis (verbatim from redis.ts) ────────────────────────────────

export const KEY_PREFIX = "mem:";
export const PROC_KEY = (slug: string) => `${KEY_PREFIX}proc:${slug}`;
export const IDX_KEY = (role: string) => `${KEY_PREFIX}idx:${role}`;
export const META_UPDATED_KEY = `${KEY_PREFIX}meta:last_updated`;

let redis: Redis;

export function initRedis(): Redis {
  const url = process.env.MEMORY_REDIS_URL || "redis://localhost:6379";
  redis = new Redis(url, {
    maxRetriesPerRequest: 3,
    retryStrategy(times) {
      // Always retry with capped exponential backoff. Returning null would
      // permanently close the client (ioredis semantics) and prevent any
      // recovery after a transient Redis outage — which is what caused the
      // Role Memory Procedure Registry to silently stop syncing.
      return Math.min(times * 200, 2000);
    },
  });

  redis.on("error", (err) => {
    console.error("[redis] error:", err.message);
  });

  return redis;
}

export function getRedis(): Redis {
  if (!redis) throw new Error("Redis not initialized. Call initRedis() first.");
  return redis;
}

export async function closeRedis(): Promise<void> {
  if (redis) {
    await redis.quit();
  }
}

/** Count keys matching a glob via SCAN (non-blocking, safe on big sets). */
export async function countKeys(pattern: string): Promise<number> {
  const r = getRedis();
  let cursor = "0";
  let count = 0;
  do {
    const [next, keys] = await r.scan(cursor, "MATCH", pattern, "COUNT", 200);
    cursor = next;
    count += keys.length;
  } while (cursor !== "0");
  return count;
}

// ── PG (verbatim from db.ts) ──────────────────────────────────────

// Keep timestamps as ISO strings — pg parses TIMESTAMPTZ into Date objects
// by default. Override to keep strings so the card JSON (typed as string)
// matches the incumbent's runtime behavior byte-for-byte.
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

export interface MemoryRow {
  id: string;
  slug: string;
  title: string;
  summary: string;
  body_md: string;
  tags: string[];
  triggers: string[];
  mcp_tools: string[];
  created_at: string;
  updated_at: string;
}

export interface RoleMemoryRow {
  id: string;
  memory_id: string;
  role: string;
  as_of_dt: string;
  expiration_dt: string | null;
}

let pool: Pool;

export function initDb(): Pool {
  const dsn =
    process.env.MEMORY_PG_DSN ||
    process.env.CONDUIT_PG_DSN ||
    "postgresql://pguser:pgpass@localhost:5432/nexus";

  pool = new Pool({
    connectionString: dsn,
    max: 5,
    idleTimeoutMillis: 30000,
  });

  return pool;
}

export function getDb(): Pool {
  if (!pool) throw new Error("DB not initialized. Call initDb() first.");
  return pool;
}

/**
 * Fetch all memory procedures with their active role assignments.
 * Returns a map: slug → { procedure, roles[] }
 */
export async function fetchAllActiveMemory(): Promise<
  Map<string, { procedure: MemoryRow; roles: string[] }>
> {
  const db = getDb();

  const procedures = await db.query<MemoryRow>(
    `SELECT * FROM tackle.memory ORDER BY slug`
  );

  const roleAssignments = await db.query<RoleMemoryRow>(
    `SELECT * FROM tackle.role_memory
     WHERE expiration_dt IS NULL
     ORDER BY role, as_of_dt DESC`
  );

  // Build role lookup: memory_id → [role, ...]
  const roleMap = new Map<string, string[]>();
  for (const row of roleAssignments.rows) {
    const roles = roleMap.get(row.memory_id) || [];
    roles.push(row.role);
    roleMap.set(row.memory_id, roles);
  }

  // Build result map
  const result = new Map<string, { procedure: MemoryRow; roles: string[] }>();
  for (const proc of procedures.rows) {
    result.set(proc.slug, {
      procedure: proc,
      roles: roleMap.get(proc.id) || [],
    });
  }

  return result;
}
