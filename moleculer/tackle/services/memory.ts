/**
 * memory.ts — Redis reader for the Role Memory Procedure Registry.
 *
 * Reads from the same Redis cache that role-memory-srv (port 3500) keeps warm.
 * No writes to Redis — purely read-only MCP tool support.
 *
 * Redis key scheme (shared with role-memory-srv):
 *   mem:proc:{slug}  → ProcedureCard JSON
 *   mem:idx:{role}    → ProcedureIndexEntry[] JSON
 *   mem:meta:last_updated → ISO timestamp
 */

import Redis from "ioredis";
import { Pool } from "pg";

// ── Redis key helpers (must match role-memory-srv/src/redis.ts) ────

const KEY_PREFIX = "mem:";
const PROC_KEY = (slug: string) => `${KEY_PREFIX}proc:${slug}`;
const IDX_KEY = (role: string) => `${KEY_PREFIX}idx:${role}`;
const META_UPDATED_KEY = `${KEY_PREFIX}meta:last_updated`;

// ── Types (must match role-memory-srv/src/sync.ts) ─────────────────

export interface ProcedureIndexEntry {
  slug: string;
  summary: string;
  tags: string[];
}

export interface ProcedureCard {
  slug: string;
  title: string;
  summary: string;
  body_md: string;
  tags: string[];
  triggers: string[];
  mcp_tools: string[];
  roles: string[];
  updated_at: string;
}

// ── Redis connection ───────────────────────────────────────────────

let redis: Redis | null = null;

export function initRedis(): Redis {
  const url = process.env.MEMORY_REDIS_URL || "redis://localhost:6379";
  redis = new Redis(url, {
    maxRetriesPerRequest: 3,
    retryStrategy(times) {
      // Always retry with capped exponential backoff. Returning null would
      // permanently close the client (ioredis semantics) and prevent any
      // recovery after a transient Redis outage — same bug that left the
      // Role Memory Procedure Registry reader stuck on "Connection is closed."
      return Math.min(times * 200, 2000);
    },
    lazyConnect: true, // don't fail startup if Redis is down
  });

  redis.on("error", (err) => {
    console.error("[memory-mcp] redis error:", err.message);
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
    redis = null;
  }
}

// ── Reader functions ───────────────────────────────────────────────

/**
 * Return the procedure index for a given role (list of summaries).
 */
export async function getProceduresForRole(
  role: string
): Promise<ProcedureIndexEntry[]> {
  const r = getRedis();
  const data = await r.get(IDX_KEY(role));
  if (!data) return [];
  return JSON.parse(data) as ProcedureIndexEntry[];
}

/**
 * Return the full procedure card for a given slug.
 */
export async function getProcedureBySlug(
  slug: string
): Promise<ProcedureCard | null> {
  const r = getRedis();
  const data = await r.get(PROC_KEY(slug));
  if (!data) return null;
  return JSON.parse(data) as ProcedureCard;
}

/**
 * Return the global last_updated timestamp from Redis.
 */
export async function getLastUpdated(): Promise<string | null> {
  const r = getRedis();
  return await r.get(META_UPDATED_KEY);
}

// ── PG change-check (since Redis has no temporal query) ────────────

/**
 * Check whether any role_memory rows have been modified since a given
 * timestamp for the specified role. Queries PG directly via the
 * tackle-mcp pool.
 */
export async function hasRoleMemoryChangedSince(
  pool: Pool,
  role: string,
  since: string
): Promise<boolean> {
  const result = await pool.query(
    `SELECT 1 FROM tackle.role_memory
     WHERE role = $1
       AND (as_of_dt > $2 OR (expiration_dt IS NOT NULL AND expiration_dt > $2))
     LIMIT 1`,
    [role, since]
  );
  return result.rowCount !== null && result.rowCount > 0;
}

// ── Refresh proxy ──────────────────────────────────────────────────

const MEMORY_SRV_URL = process.env.MEMORY_SRV_URL || "http://localhost:3500";

/**
 * Trigger a full PG→Redis sync by calling POST /refresh on the
 * role-memory-srv. Returns the sync result.
 */
export async function triggerRefresh(): Promise<{
  success: boolean;
  result?: any;
  error?: string;
}> {
  try {
    const resp = await fetch(`${MEMORY_SRV_URL}/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!resp.ok) {
      const body = await resp.text();
      return { success: false, error: `HTTP ${resp.status}: ${body}` };
    }
    const result = await resp.json();
    return { success: true, result };
  } catch (err: any) {
    return { success: false, error: err.message };
  }
}
