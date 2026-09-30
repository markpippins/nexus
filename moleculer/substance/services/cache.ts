// ── cache.ts ─────────────────────────────────────────────────────────────────
// Port of python/substance/cache.py.
//
// Cache strategy (unchanged from the Python service): every mutation invalidates
// the relevant Redis key rather than writing through. A resolved segment set
// can be referenced by multiple domain objects at once, so deleting and letting
// the next GET lazily rebuild it avoids two write paths racing to keep a cached
// blob in sync. The TTL is purely a safety net in case an invalidation is ever
// missed.

import Redis from "ioredis";

import { getSettings } from "./config";

let _client: Redis | null = null;

/**
 * Test seam. Overrides client construction so the invalidation paths can be
 * exercised without a Redis anywhere — including failure paths, which are the
 * interesting ones. A factory (rather than a client instance) is stored so that
 * every call site, including the ones inside this module, resolves through it;
 * `vi.spyOn` on the exported getter would not intercept a module-local call.
 */
type ClientFactory = () => Redis;
let _factory: ClientFactory | null = null;

/** Install a client factory. Pass null to restore the real one. */
export function setClientFactory(factory: ClientFactory | null): void {
  _factory = factory;
}

/**
 * Redis client singleton. `lazyConnect` keeps construction side-effect free —
 * the Python client was likewise lazy, and the pure key helpers below must be
 * importable (and testable) with no Redis anywhere in sight.
 */
export function getClient(): Redis {
  if (_factory !== null) {
    return _factory();
  }
  if (_client === null) {
    _client = new Redis(getSettings().redisUrl, {
      lazyConnect: true,
      maxRetriesPerRequest: 2,
    });
    // Without a listener, an emitted `error` event would crash the process.
    // Cache degradation must never take the service down; the TTL safety net
    // covers us and Postgres remains the source of truth.
    _client.on("error", (err: Error) => {
      console.warn(`[substance cache] redis error (non-fatal): ${err.message}`);
    });
  }
  return _client;
}

/** Shutdown hook: drop the client singleton. */
export function closeClient(): void {
  if (_client !== null) {
    _client.disconnect();
    _client = null;
  }
}

// ── Key helpers ──────────────────────────────────────────────────────────────

/** Resolved segment set JSON. */
export function segsetKey(segmentSetId: string): string {
  return `nexus:segset:${segmentSetId}`;
}

/**
 * Reverse index for "all evidence for this domain object". Reserved: the
 * invalidation calls are wired into the link/unlink routes, so a read-through
 * list cache can be added in links.ts without touching the writers.
 */
export function domainIndexKey(domainType: string, domainId: string): string {
  return `nexus:${domainType}:${domainId}:segsets`;
}

// ── Serialisation ────────────────────────────────────────────────────────────

/**
 * JSON.stringify replacer mirroring Python's `_JSONEncoder`: UUIDs become
 * strings, datetimes/dates become ISO-8601. node-postgres hands back
 * timestamptz columns as JS `Date`, so this is load-bearing, not defensive.
 */
export function jsonReplacer(_key: string, value: unknown): unknown {
  if (value instanceof Date) {
    return value.toISOString();
  }
  return value;
}

/** Serialise a cache payload the way `_JSONEncoder` did. */
export function encodeCachePayload(payload: unknown): string {
  return JSON.stringify(payload, jsonReplacer);
}

/** Parse a cache payload. Returns null for absent or unparseable values. */
export function decodeCachePayload(raw: string | null): Record<string, unknown> | null {
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    // A truncated or foreign blob is a miss, not a 500. Postgres rebuilds it.
    return null;
  }
}

// ── Operations ───────────────────────────────────────────────────────────────

/** Read-through: fetch a resolved segment set, or null on miss. */
export async function getSegset(segmentSetId: string): Promise<Record<string, unknown> | null> {
  const raw = await getClient().get(segsetKey(segmentSetId));
  return decodeCachePayload(raw);
}

/** Populate the cache with the safety-net TTL. */
export async function setSegset(segmentSetId: string, payload: unknown): Promise<void> {
  await getClient().set(
    segsetKey(segmentSetId),
    encodeCachePayload(payload),
    "EX",
    getSettings().redisTtlSeconds,
  );
}

/** Drop a resolved segment set so the next GET lazily rebuilds it. */
export async function invalidateSegset(segmentSetId: string): Promise<void> {
  await getClient().del(segsetKey(segmentSetId));
}

/** Drop the reserved per-domain reverse index. */
export async function invalidateDomainIndex(domainType: string, domainId: string): Promise<void> {
  await getClient().del(domainIndexKey(domainType, domainId));
}

/**
 * Best-effort invalidation, for use *after* a transaction has committed.
 *
 * Cache hygiene is not data integrity: by the time an invalidation runs, the
 * write it accompanies is already durable. If Redis is unreachable, raising
 * would turn a successful write into a 500 — and a client that retries on a
 * 500 would duplicate the row. The TTL is the safety net for exactly this case,
 * so a failed invalidation is logged and swallowed.
 *
 * The strict `invalidateSegset` / `invalidateDomainIndex` above stay available
 * for callers that genuinely need the deletion to have happened.
 */
export async function invalidateSegsetBestEffort(segmentSetId: string): Promise<void> {
  try {
    await invalidateSegset(segmentSetId);
  } catch (err) {
    console.warn(
      `[substance cache] failed to invalidate segset ${segmentSetId} (TTL will cover): ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
}

/** Best-effort form of {@link invalidateDomainIndex}. */
export async function invalidateDomainIndexBestEffort(
  domainType: string,
  domainId: string,
): Promise<void> {
  try {
    await invalidateDomainIndex(domainType, domainId);
  } catch (err) {
    console.warn(
      `[substance cache] failed to invalidate ${domainType}/${domainId} index (TTL will cover): ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
}
