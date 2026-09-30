// ── config.ts ────────────────────────────────────────────────────────────────
// Port of python/substance/config.py.
//
// Python reads the environment at class-definition time into class attributes
// and memoizes the Settings instance with @lru_cache. The TypeScript
// equivalent keeps the same observable contract: `settingsFromEnv` is the pure
// factory (testable with an injected env), `getSettings` is the memoized
// singleton the rest of the service uses.

export interface Settings {
  /** Postgres DSN for the nebula schema. */
  postgresDsn: string;
  /** Redis URL for the segment-set read-through cache. */
  redisUrl: string;
  /**
   * Safety-net TTL only — the cache is actively invalidated on every write
   * path; this just bounds staleness if an invalidation is ever missed.
   */
  redisTtlSeconds: number;
}

export const DEFAULT_POSTGRES_DSN =
  "postgresql://pguser:pgpass@localhost:5432/nexus";
export const DEFAULT_REDIS_URL = "redis://localhost:6379/0";
export const DEFAULT_REDIS_TTL_SECONDS = 3600;

/** Subset of process.env we read. Injectable so tests need no process mutation. */
export type Env = Record<string, string | undefined>;

/**
 * Build Settings from an environment. Throws on a non-numeric TTL, mirroring
 * Python's `int(os.environ.get("NEBULA_SEGSET_CACHE_TTL", "3600"))` which
 * raises ValueError on garbage — a misconfigured TTL must fail loudly rather
 * than silently disable the safety net.
 */
export function settingsFromEnv(env: Env = process.env): Settings {
  const ttlRaw = env.NEBULA_SEGSET_CACHE_TTL ?? String(DEFAULT_REDIS_TTL_SECONDS);
  const ttl = Number(ttlRaw);
  if (!Number.isInteger(ttl)) {
    throw new Error(
      `invalid NEBULA_SEGSET_CACHE_TTL: ${JSON.stringify(ttlRaw)} (expected an integer)`,
    );
  }
  return {
    postgresDsn: env.NEBULA_PG_DSN || DEFAULT_POSTGRES_DSN,
    redisUrl: env.NEBULA_REDIS_URL || DEFAULT_REDIS_URL,
    redisTtlSeconds: ttl,
  };
}

let _cached: Settings | null = null;

/** Memoized settings singleton (Python's @lru_cache get_settings). */
export function getSettings(): Settings {
  if (_cached === null) {
    _cached = settingsFromEnv(process.env);
  }
  return _cached;
}

/** Test hook: drop the memoized singleton. */
export function resetSettings(): void {
  _cached = null;
}
