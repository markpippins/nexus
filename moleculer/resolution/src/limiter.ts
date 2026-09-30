import rateLimit from "express-rate-limit";

/**
 * App-level read limiter — resolution.* is the canonical governance store.
 * Mounted in index.ts BEFORE every route (including /health) so the whole
 * surface is throttled uniformly (same pattern as wind-srv's limiters).
 * 300 req/min/IP leaves normal operator/agent reads well clear of the
 * ceiling while capping resource-exhaustion floods.
 */
export const globalLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 300,
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: { error: "resolution-srv read rate limit exceeded" },
});
