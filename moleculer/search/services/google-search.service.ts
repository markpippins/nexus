import { Service, ServiceBroker, Context, Errors } from "moleculer";
import axios from "axios";
import { MongoClient } from "mongodb";
import Redis from "ioredis";
// NOTE: type-only import (erased at compile): native type-stripping has no
// type phase, so named type imports from the CJS "mongodb" package fail at
// runtime. MongoClient (a value) imports normally.
import type { Collection, Document } from "mongodb";

interface GoogleSearchParams {
  query: string;
  token?: string;
}

interface SearchResultItem {
  title: string;
  link: string;
  snippet: string;
  // Canonical item contract (slice-1, Option C): displayLink is required.
  // Legacy already carries it and IdeaStream renders it as `source`.
  displayLink: string;
}

interface GoogleSearchResponse {
  items: SearchResultItem[];
  searchInformation?: {
    totalResults: string;
    searchTime: number;
  };
  // Echo of the caller's token when supplied (D4). Reserved for slice-3
  // rate-limit accounting; echoing the caller's own correlation key back
  // to the same caller is not a secret leak. Absent when not supplied.
  token?: string;
}

// Typed search errors (D3: non-2xx is the permanent moleculer convention).
// The {code, retryable} pair lives in `data` so it survives moleculer-web
// JSON serialization; `retryable` is also set on the instance for broker
// retry-policy consumers.
export const SearchErrorCode = {
  CREDENTIALS: "SEARCH_CREDENTIALS",
  PROVIDER: "SEARCH_PROVIDER",
} as const;

function searchError(
  code: keyof typeof SearchErrorCode,
  message: string,
  retryable: boolean,
  extra: Record<string, unknown> = {}
): Errors.MoleculerError {
  const codeValue = SearchErrorCode[code];
  const err = new Errors.MoleculerError(message, 500, codeValue, {
    code: codeValue,
    retryable,
    provider: "google",
    ...extra,
  });
  err.retryable = retryable;
  return err;
}

// displayLink fallback: Google returns it per item; if absent, derive the
// registrable hostname from the link so every item still satisfies the
// canonical contract. Final fallback is the link itself (never undefined).
function displayLinkOf(item: any): string {
  if (item.displayLink) return item.displayLink;
  try {
    return new URL(item.link).hostname;
  } catch {
    return item.link;
  }
}

// ── Shared-cache reader (slice-2, phase 1: READS ONLY) ─────────────────
// Reads legacy `nexus.search_results_cache` rows written by the broker.
// Inlined here (not a separate module): prod runs .ts source directly
// under Node native type-stripping, which cannot resolve relative TS
// imports — see the #210 incident. Package imports ("mongodb") are fine.
//
// Key + TTL rules mirror the broker exactly:
//   normalize() === SearchRateLimiter.normalize (lowercase/trim/collapse);
//   a row is valid iff expiresAt is absent OR in the future (mirrors
//   legacy isExpired()). Lookup is normalized-first with raw-query
//   fallback (lazy migration era: old rows were written under raw keys).
// Fail-open: ANY cache error (down/unreachable/malformed) yields null and
// the caller proceeds to live search — same posture as the broker's
// fail-open rate limiter. Phase 1 never writes: no upsert, no delete of
// expired rows, no rewrite-normalized (all phase 2, post-DBA).

const SEARCH_CACHE_COLLECTION = "search_results_cache";

export function normalizeSearchQuery(query: string): string {
  return (query ?? "").toLowerCase().trim().replace(/\s+/g, " ");
}

export interface CachedSearchRow extends Document {
  query?: string;
  items?: any[] | null;
  timestamp?: Date | string;
  expiresAt?: Date | string | null;
}

export function isCacheRowValid(row: CachedSearchRow | null | undefined): boolean {
  if (!row) return false;
  if (row.expiresAt == null) return true;
  return new Date(row.expiresAt).getTime() > Date.now();
}

function searchCacheMongoUrl(): string {
  return (
    process.env.MONGO_URL ||
    process.env.SPRING_DATA_MONGODB_URI ||
    "mongodb://localhost:27017"
  );
}

function searchCacheDbName(url: string): string {
  try {
    const path = new URL(url).pathname.replace(/^\/+/, "");
    return path.length > 0 ? path : "nexus";
  } catch {
    return "nexus";
  }
}

let liveClientPromise: Promise<MongoClient> | null = null;
// Test seam: when set (including explicit null), live connection is skipped.
let testCollection: Collection<Document> | null | undefined = undefined;

/** Test-only hook: inject a fake collection (or null) for cache tests. */
export function __useTestCacheCollection(coll: Collection<Document> | null): void {
  testCollection = coll;
}

/** Test-only hook: restore the live connection path. */
export function __resetCacheState(): void {
  testCollection = undefined;
  liveClientPromise = null;
}

async function searchCacheCollection(): Promise<Collection<Document> | null> {
  if (testCollection !== undefined) return testCollection;
  try {
    if (!liveClientPromise) {
      // Fail fast: a cache must never stall a search past a few seconds.
      // (Live Google is the fallback, not the casualty.)
      liveClientPromise = new MongoClient(searchCacheMongoUrl(), {
        serverSelectionTimeoutMS: 5000,
      }).connect();
    }
    const client = await liveClientPromise;
    const url = searchCacheMongoUrl();
    return client.db(searchCacheDbName(url)).collection(SEARCH_CACHE_COLLECTION);
  } catch {
    // Connect failure: drop the cached promise so the next call retries,
    // and fail open to live search this time.
    liveClientPromise = null;
    return null;
  }
}

async function findValidCacheEntry(query: string): Promise<CachedSearchRow | null> {
  let coll: Collection<Document> | null;
  try {
    coll = await searchCacheCollection();
  } catch {
    return null;
  }
  if (!coll) return null;
  try {
    const normalized = normalizeSearchQuery(query);
    let row = (await coll.findOne({ query: normalized })) as CachedSearchRow | null;
    if (!row && normalized !== query) {
      row = (await coll.findOne({ query })) as CachedSearchRow | null;
    }
    return isCacheRowValid(row) ? row : null;
  } catch {
    return null;
  }
}

// Cooldown-path lookup (slice 3): any age, never deletes — mirrors the
// broker's findAnyCacheEntry (post-#216 normalized-first + raw fallback).
// A cooldown read must not destroy data.
async function findAnyCacheEntry(query: string): Promise<CachedSearchRow | null> {
  let coll: Collection<Document> | null;
  try {
    coll = await searchCacheCollection();
  } catch {
    return null;
  }
  if (!coll) return null;
  try {
    const normalized = normalizeSearchQuery(query);
    let row = (await coll.findOne({ query: normalized })) as CachedSearchRow | null;
    if (!row && normalized !== query) {
      row = (await coll.findOne({ query })) as CachedSearchRow | null;
    }
    return row; // any age — stale-serve is the point
  } catch {
    return null;
  }
}

// ── Phase-2 write-side (GATED, default OFF) ──────────────────────────
// Canonical rows: normalized `query` key + expiresAt. Safe only after the
// DBA lands UNIQUE {query:1} + TTL {expiresAt:1} on nexus.search_results_cache
// (request 4ddaae7b): the upsert relies on the unique index for race safety
// (a concurrent identical query can win with E11000 — swallowed, a cache
// only promises that A row exists), and the TTL index owns expiry so legacy
// raw rows age out with no migration and no app-level janitor.
//
// SEARCH_CACHE_WRITE_MODE (read lazily so tests can flip it):
//   "off"       (default) — phase-1 behavior: reads only, never writes.
//   "legacy"    — reserved for the old append-a-raw-key-row shape.
//   "canonical" — upsert-by-normalized-query (the phase-2 write shape).
// Any other value degrades to "off": unknown modes must never write.
// Writes happen on live-search success only — including forceSearch
// (refreshing the cache is the point of force); fail-open throughout:
// a cache write must never fail a search (mirrors the legacy posture).
const SEARCH_CACHE_TTL_MS = 30 * 60 * 1000; // mirrors legacy CACHE_TTL_MINUTES = 30

export type SearchCacheWriteMode = "off" | "legacy" | "canonical";

export function searchCacheWriteMode(): SearchCacheWriteMode {
  const raw = (process.env.SEARCH_CACHE_WRITE_MODE ?? "off").trim().toLowerCase();
  if (raw === "canonical") return "canonical";
  if (raw === "legacy") return "legacy";
  return "off";
}

// ── Slice-3: Redis rate limiter — bug-compatible mirror (Option A POC) ──
// Operator-authorized POC (record 23f3e002) of the architect proposal
// SLICE-3-RATELIMIT-PROPOSAL.md: mirror the broker's SearchRateLimiter
// exactly, quirks included, against the SHARED Redis instance so cooldown
// buckets are continuous across the cutover. Architect reviews results.
//
// Broker reference semantics (SearchRateLimiter.java, verified 2026-09-12):
//   key    = "search:ratelimit:google:" + normalize(query)
//   value  = ISO-8601 instant of the last live search
//   TTL    = cooldown window (keys auto-expire — no janitor)
//   check  = limited iff elapsed.toHours() < cooldown  (hour truncation
//            quirk preserved on purpose — see proposal G5)
//   posture= fail-open reads, fail-silent writes, enabled kill-switch.
// Inlined here per the #210 constraint (prod runs .ts under Node type-
// stripping; relative imports cannot be resolved at runtime).

const RATELIMIT_KEY_PREFIX = "search:ratelimit:google:";
const RATELIMIT_DEFAULT_COOLDOWN_HOURS = 4;

export function ratelimitEnabled(): boolean {
  const raw = (process.env.SEARCH_RATELIMIT_ENABLED ?? "true").trim().toLowerCase();
  return raw !== "false" && raw !== "0" && raw !== "off";
}

export function ratelimitCooldownHours(): number {
  const parsed = Number(process.env.SEARCH_RATELIMIT_COOLDOWN_HOURS ?? RATELIMIT_DEFAULT_COOLDOWN_HOURS);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : RATELIMIT_DEFAULT_COOLDOWN_HOURS;
}

function ratelimitRedisUrl(): string {
  return process.env.REDIS_URL || "redis://localhost:6379";
}

let liveRedis: Redis | null = null;
// Test seam: when set (including explicit null), live connection is skipped.
let testRedis: Redis | null | undefined = undefined;

/** Test-only hook: inject a fake Redis (or null to force fail-open). */
export function __useTestRedis(client: Redis | null): void {
  testRedis = client;
}

/** Test-only hook: restore the live connection path. */
export function __resetRedisState(): void {
  testRedis = undefined;
  if (liveRedis) {
    liveRedis.disconnect();
    liveRedis = null;
  }
}

function rateLimitRedis(): Redis | null {
  if (!ratelimitEnabled()) return null;
  if (testRedis !== undefined) return testRedis;
  if (!liveRedis) {
    // Lazy, retry-less connection: ioredis retries in the background, but
    // every command on a down connection rejects — the callers fail open.
    liveRedis = new Redis(ratelimitRedisUrl(), {
      lazyConnect: false,
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
    });
    liveRedis.on("error", () => {
      // ioredis emits 'error' on connection loss; swallow here — the
      // command-level try/catch owns fail-open behavior.
    });
  }
  return liveRedis;
}

/** Mirrors SearchRateLimiter.isRateLimited: true = within cooldown. */
export async function isRateLimited(query: string): Promise<boolean> {
  if (!ratelimitEnabled()) return false;
  const redis = rateLimitRedis();
  if (!redis) return false; // disabled or test null → limiter off
  try {
    const val = await redis.get(RATELIMIT_KEY_PREFIX + normalizeSearchQuery(query));
    if (val == null) return false; // never searched
    const elapsedMs = Date.now() - new Date(val).getTime();
    // Floor-of-hours comparison, byte-faithful to the broker's
    // elapsed.toHours() < cooldown. For integer cooldowns this is
    // behaviorally identical to a millis comparison (floor(e) < n ⟺ e < n);
    // it diverges only for fractional cooldown values (which the broker's
    // long cannot express but our env allows) — pinned by test.
    const elapsedHours = Math.floor(elapsedMs / 3_600_000);
    return elapsedHours < ratelimitCooldownHours();
  } catch {
    return false; // fail open — if Redis is down, allow the search
  }
}

/** Mirrors SearchRateLimiter.markSearched: sets instant + cooldown TTL. */
export async function markSearched(query: string): Promise<void> {
  if (!ratelimitEnabled()) return;
  const redis = rateLimitRedis();
  if (!redis) return;
  try {
    const key = RATELIMIT_KEY_PREFIX + normalizeSearchQuery(query);
    const seconds = Math.max(1, Math.round(ratelimitCooldownHours() * 3600));
    await redis.set(key, new Date().toISOString(), "EX", seconds);
  } catch {
    // fail-silent, like the broker: the search already succeeded
  }
}

function mapCachedItems(items: any[] | null | undefined): SearchResultItem[] {
  return (items ?? []).map((item: any) => ({
    // Canonical contract: required strings (nulls normalize to "").
    title: item?.title ?? "",
    link: item?.link ?? "",
    snippet: item?.snippet ?? "",
    displayLink: displayLinkOf(item ?? {}),
  }));
}

export default class GoogleSearchService extends Service {
  private apiKey: string;
  private searchEngineId: string;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "google-search",

      settings: {
        apiKey: process.env.GOOGLE_API_KEY || "",
        searchEngineId: process.env.GOOGLE_SEARCH_ENGINE_ID || "",
      },

      actions: {
        simpleSearch: {
          params: {
            query: "string",
            token: { type: "string", optional: true }
          },
          async handler(ctx: Context<GoogleSearchParams>): Promise<GoogleSearchResponse> {
            return this.performSearch(ctx.params.query, ctx.params.token, false);
          }
        },

        // Slice-2: force bypasses the shared cache (and, once slice 3 lands,
        // the rate limiter). Same params/shape as simpleSearch.
        forceSearch: {
          params: {
            query: "string",
            token: { type: "string", optional: true }
          },
          async handler(ctx: Context<GoogleSearchParams>): Promise<GoogleSearchResponse> {
            return this.performSearch(ctx.params.query, ctx.params.token, true);
          }
        },

        health: {
          async handler(): Promise<{ status: string; service: string }> {
            return {
              status: "ok",
              service: "google-search"
            };
          }
        }
      },

      started: async () => {
        this.apiKey = this.settings.apiKey;
        this.searchEngineId = this.settings.searchEngineId;

        if (!this.apiKey || !this.searchEngineId) {
          this.logger.warn("Google API credentials not configured. Set GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID environment variables.");
        } else {
          this.logger.info("Google Search Service initialized with API credentials");
        }
      }
    });

    this.apiKey = "";
    this.searchEngineId = "";
  }

  // NOTE: several existing tests call performSearch(query) directly (token
  // omitted). The token stays optional end-to-end: absent means unkeyed.
  // forceRefresh bypasses the shared-cache read (slice-2 G5).
  async performSearch(query: string, token?: string, forceRefresh = false): Promise<GoogleSearchResponse> {
    // Slice-3: call order mirrors the broker exactly.
    // ① Rate-limited (unless force) → serve ANY cache row, even expired
    //    (stale-serve is the cooldown's whole purpose); no row → fall
    //    through to live (fail-open parity with SearchRateLimiter).
    if (!forceRefresh && (await isRateLimited(query))) {
      const stale = await findAnyCacheEntry(query);
      if (stale) {
        return {
          items: mapCachedItems(stale.items),
          ...(token !== undefined ? { token } : {}),
        };
      }
    }
    // ② Fresh cache (unless force). A hit EXTENDS the cooldown via
    //    markSearched — slice-2 G6, declared intended: cache service
    //    extends provider cooldown.
    if (!forceRefresh) {
      const cached = await findValidCacheEntry(query);
      if (cached) {
        await markSearched(query);
        // NOTE: no searchInformation on cache hits (mirrors legacy
        // buildResult, which serves items without provider metadata).
        return {
          items: mapCachedItems(cached.items),
          ...(token !== undefined ? { token } : {}),
        };
      }
    }

    if (!this.apiKey || !this.searchEngineId) {
      throw searchError(
        "CREDENTIALS",
        "Google API credentials not configured",
        false
      );
    }

    const url = `https://www.googleapis.com/customsearch/v1`;

    try {
      const response = await axios.get(url, {
        params: {
          key: this.apiKey,
          cx: this.searchEngineId,
          q: query
        }
      });

      const items: SearchResultItem[] = response.data.items?.map((item: any) => ({
        title: item.title,
        link: item.link,
        snippet: item.snippet,
        displayLink: displayLinkOf(item),
      })) || [];

      // Phase-2 write-side (gated, default off): canonical normalized-key
      // upsert when SEARCH_CACHE_WRITE_MODE=canonical. Never fails the
      // search — see writeCanonicalCacheRow (fail-open).
      await this.writeCanonicalCacheRow(query, items);

      // ③ Slice-3: record the cooldown stamp on live success — the broker
      // marks on EVERY live search, force or not (GoogleSearchService L171).
      await markSearched(query);

      return {
        items,
        searchInformation: response.data.searchInformation,
        ...(token !== undefined ? { token } : {}),
      };
    } catch (error: any) {
      this.logger.error("Google Search API error:", error.message);
      if (error.response) {
        this.logger.error("Google API response status:", error.response.status);
        this.logger.error("Google API response data:", JSON.stringify(error.response.data));
      }
      // Retryable on network failure (no status), 429, or 5xx. Other 4xx
      // (e.g. 400 invalid argument / bad cx) will fail identically on retry.
      const status: number | undefined = error.response?.status;
      const retryable = status == null || status === 429 || status >= 500;
      throw searchError(
        "PROVIDER",
        `Failed to perform search: ${error.message}`,
        retryable,
        status !== undefined ? { status } : {}
      );
    }
  }

  /**
   * Upsert a canonical cache row keyed by the normalized query (phase-2
   * write shape, GATED by SEARCH_CACHE_WRITE_MODE=canonical). Requires the
   * DBA-approved UNIQUE {query:1} index: without it, concurrent identical
   * queries append duplicates (the pre-phase-2 pile-up); with it, one of
   * two racing writes may reject with E11000 — that is fine, a cache only
   * promises that A row exists. The TTL index (with expiresAt set here)
   * owns expiry, so legacy raw rows age out with no migration and no
   * app-level janitor. Fail-open: any error is logged, never thrown —
   * a cache write must never fail a live search.
   */
  async writeCanonicalCacheRow(query: string, items: SearchResultItem[]): Promise<void> {
    if (searchCacheWriteMode() !== "canonical") return;
    try {
      const coll = await searchCacheCollection();
      if (!coll) return;
      const now = new Date();
      // Filter equality fields are applied to the inserted doc on upsert,
      // so the canonical (normalized) key lands in the row automatically.
      await coll.updateOne(
        { query: normalizeSearchQuery(query) },
        {
          $set: {
            items,
            timestamp: now,
            expiresAt: new Date(now.getTime() + SEARCH_CACHE_TTL_MS),
          },
        },
        { upsert: true }
      );
      this.logger.debug(`Canonical cache row upserted for query: ${query}`);
    } catch (err: any) {
      // E11000 (unique-index race) and any other cache error: fail open —
      // the live result is already in hand and was returned regardless.
      this.logger.warn(`Canonical cache write skipped for query "${query}": ${err?.message ?? err}`);
    }
  }
}