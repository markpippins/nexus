import rateLimit from "express-rate-limit";

/**
 * Read limiter for the attestation lookup endpoint (GET /api/attestations).
 * Scoped to this single route (mounted as route middleware in routes.ts):
 * nebula-srv is the fleet's busiest store and pre-existing routes predate
 * the scanner, so the limiter is added narrowly where new traffic lands.
 * Same posture as resolution-srv's limiter.ts: 300 req/min/IP keeps normal
 * operator/agent polling (merge-gate gate 3) well clear of the ceiling
 * while capping resource-exhaustion floods. CodeQL js/missing-rate-limiting
 * remediation — tester finding 59bdd08f / alert #711.
 */
export const attestationsLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 300,
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: { error: "nebula-srv attestation lookup rate limit exceeded" },
});
