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

/**
 * Limiter for the governed requirement-compilation API
 * (POST /v1/capabilities/requirement-compile/compile and
 *  GET  /v1/capabilities/requirement-compile/receipts/:receiptId).
 *
 * Same posture and ceiling as attestationsLimiter — 300 req/min/IP. These
 * endpoints are the expensive ones in this surface: compile performs requirement
 * selection + evaluation and mints a durable receipt, and the receipts read
 * backs a receipt lookup. Both are the paths a flood would hit first.
 *
 * CodeQL js/missing-rate-limiting remediation for #749. This is a REPEAT of the
 * identical rule fixed six days ago in assembly-srv (`63aa4a1ae` / #694, the
 * bridges router); express-rate-limit ^7.5.1 was already a nebula-srv dependency,
 * so no new dependency was needed.
 */
export const requirementCompileLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 300,
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: { error: "nebula-srv requirement-compile rate limit exceeded" },
});
