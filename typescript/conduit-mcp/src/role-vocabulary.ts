/**
 * Role vocabulary for conduit-mcp's agent-facing surfaces.
 *
 * The authoritative source is the ratified roles disposition matrix
 * (schemas/decision-b-freeze/roles-disposition-matrix.json, decision-b-freeze
 * ruling). This module exports the KILLABLE_ROLES allowlist consumed by the
 * POST /agents/:role/kill endpoint so the route and its tests share a single
 * object (no second list to drift).
 *
 * Derivation rule (from PR plan 2258578c / sweep c5b55c2c):
 *   rows of roles-disposition-matrix.json whose `class` is `role`.
 *   EXCLUDED:
 *     - model bindings (claude, chatgpt, copilot, deepseek, gemini, kiro,
 *       big-pickle, ...) — they live in tackle.role_leases per the roles-matrix
 *       ruling and are not killable agent roles.
 *     - service principals (admin, jenkins-sync, sonar-sync, rover, ...)
 *     - config residue (builder-fallback, leased-builder, test, wr-conf-016-*)
 *
 * The matrix lists 23 role-class identities. The 6 legacy allowlist entries
 * (planner, builder, reviewer, critic, analyst, architect) are a strict subset
 * — widening is backward compatible.
 */
export const KILLABLE_ROLES: readonly string[] = [
  "analyst",
  "analyst-ii",
  "architect",
  "auditor",
  "builder",
  "critic",
  "dba",
  "design-synthesist",
  "devops",
  "engineer",
  "engineer-ii",
  "epistemologist",
  "inspector",
  "layout-mechanic",
  "lead-engineer",
  "ontologist",
  "operator",
  "planner",
  "reviewer",
  "sound-technician",
  "sysadmin",
  "tester",
  "topologist",
] as const;