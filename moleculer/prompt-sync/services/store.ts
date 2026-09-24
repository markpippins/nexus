import Redis from "ioredis";
import { Pool, types } from "pg";

/**
 * Shared cache + store access for the prompt-sync twin (canary :4501).
 *
 * VERBATIM PORT of typescript/tackle-prompt-sync-srv/src/redis.ts + db.ts —
 * the twin must read the SAME Redis keys (prompt:proc:{role}::{slug} /
 * prompt:idx:{role} / prompt:meta:last_updated / task:idx:{role}) and the
 * SAME PG tables (tackle.prompts, tackle.tasks) as the incumbent, because
 * parity here is against shared live state (role-memory precedent: the
 * database and the cache are the arbiters). All the incumbent's resilience
 * comments are load-bearing doctrine — kept.
 */

// ── Redis (verbatim from redis.ts) ────────────────────────────────

// Redis key namespace for the Prompt Registry. Lives under `prompt:`
// (NOT `mem:` — that namespace is owned by role-memory-srv for the
// Role Memory Procedure Registry. Prompts and procedures are
// different registries with different access patterns: prompts are
// ASSEMBLED at agent launch; procedure cards are CONSULTED on demand.)
//
// Key schemas:
//   prompt:proc:{role}::{slug}   String(JSON) — full PromptCard (latest version)
//   prompt:idx:{role}            String(JSON) — PromptIndexEntry[] for the role
//   prompt:meta:last_updated     String(ISO)  — global last-sync timestamp
//   task:idx:{role}              String(JSON) — TaskIndexEntry[] for the role
export const KEY_PREFIX = "prompt:";
export const PROC_KEY = (role: string, slug: string) => `${KEY_PREFIX}proc:${role}::${slug}`;
export const IDX_KEY = (role: string) => `${KEY_PREFIX}idx:${role}`;
export const META_UPDATED_KEY = `${KEY_PREFIX}meta:last_updated`;
export const TASK_IDX_KEY = (role: string) => `task:idx:${role}`;

let redis: Redis;

export function initRedis(): Redis {
  const url = process.env.PROMPT_REDIS_URL || process.env.MEMORY_REDIS_URL || "redis://localhost:6379";
  redis = new Redis(url, {
    maxRetriesPerRequest: 3,
    // Always retry with capped exponential backoff. Returning null would
    // permanently close the client (ioredis semantics) and prevent recovery
    // after a transient Redis outage — mirroring role-memory-srv's fix.
    retryStrategy(times) {
      return Math.min(times * 200, 2000);
    },
  });

  redis.on("error", (err) => {
    console.error("[prompt-redis] error:", err.message);
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

// ── PG (verbatim from db.ts) ──────────────────────────────────────

// Keep timestamps as ISO strings so TypeScript interfaces (which type
// timestamps as string) match runtime behavior. Mirrors role-memory-srv.
types.setTypeParser(types.builtins.TIMESTAMPTZ, (val: string) => val);
types.setTypeParser(types.builtins.TIMESTAMP, (val: string) => val);

export interface PromptRow {
  id: string;
  role: string;
  slug: string;
  version: number;
  title: string;
  body_md: string;
  parameter_schema: Record<string, any>;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface TaskRow {
  id: string;
  role: string;
  task_slug: string;
  scope: string;
  acceptance_criteria: string[];
  prompt_id: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

let pool: Pool;

export function initDb(): Pool {
  const dsn =
    process.env.PROMPT_PG_DSN ||
    process.env.TACKLE_PG_DSN ||
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
 * Fetch the LATEST version of each prompt template per role.
 *
 * tackle.prompts is versioned: (role, slug, version) is UNIQUE. Launching
 * agents want the newest revision of each (role, slug), which is the row
 * with MAX(version) grouped by (role, slug). We resolve that with a
 * DISTINCT ON window in Postgres — cleaner than a self-join and faster than
 * fetching all versions and post-filtering in JS.
 *
 * Returns a flat array of the latest PromptRow per (role, slug), ordered
 * by role then slug for deterministic Redis writes.
 */
export async function fetchLatestPrompts(): Promise<PromptRow[]> {
  const db = getDb();
  const result = await db.query<PromptRow>(
    `SELECT DISTINCT ON (role, slug)
            id, role, slug, version, title, body_md,
            parameter_schema, tags, created_at, updated_at
     FROM tackle.prompts
     ORDER BY role, slug, version DESC`
  );
  return result.rows;
}

/**
 * Fetch all active tasks. The `active` column encodes default-allowlist
 * semantics — only active rows are picked up at launch, so we sync only
 * those to Redis. Retired/superseded rows stay in PG for audit but never
 * reach the cache.
 */
export async function fetchActiveTasks(): Promise<TaskRow[]> {
  const db = getDb();
  const result = await db.query<TaskRow>(
    `SELECT id, role, task_slug, scope, acceptance_criteria,
            prompt_id, active, created_at, updated_at
     FROM tackle.tasks
     WHERE active = TRUE
     ORDER BY role, task_slug`
  );
  return result.rows;
}
