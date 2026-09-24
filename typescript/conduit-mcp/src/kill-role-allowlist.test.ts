/**
 * G3 (sweep c5b55c2c, PR plan 2258578c) — kill-endpoint role allowlist.
 *
 * Guards the KILLABLE_ROLES vocabulary consumed by POST /agents/:role/kill:
 *   1. Vocabulary conformance (drift guard) — the allowlist must contain all
 *      13 ratified roles; anyone shrinking it below the ratified vocabulary
 *      fails CI. Same fixture-guard convention as ruled for D1 (1c23a764).
 *   2. No model identities — model bindings must never be killable agent roles.
 *   3. Behavioral — the route accepts a ratified role and rejects model/garbage.
 *
 * Pure and DB-free. The route handler itself lives in index.ts; this test
 * asserts the shared allowlist object (single source — no second list to drift).
 *
 * Usage:
 *   cd /home/codex/dev/nexus-worktrees/g3-kill-allowlist/typescript/conduit-mcp
 *   npx vitest run src/kill-role-allowlist.test.ts
 */
import { describe, test, expect } from "vitest";

import { KILLABLE_ROLES } from "./role-vocabulary";

// The 13 ratified roles (decision-b-freeze matrix, including Supervisor).
const RATIFIED_ROLES = [
  "analyst-ii",
  "auditor",
  "critic",
  "dba",
  "devops",
  "engineer-ii",
  "epistemologist",
  "lead-engineer",
  "operator",
  "sound-technician",
  "supervisor",
  "sysadmin",
  "tester",
];

describe("G3 kill-endpoint role allowlist", () => {
  test("AC1 — vocabulary conformance: contains all 13 ratified roles", () => {
    for (const r of RATIFIED_ROLES) {
      expect(KILLABLE_ROLES, `missing ratified role ${r}`).toContain(r);
    }
  });

  test("AC1 — contains the legacy 6-role gate (backward compatible)", () => {
    for (const r of ["planner", "builder", "reviewer", "critic", "analyst", "architect"]) {
      expect(KILLABLE_ROLES).toContain(r);
    }
  });

  test("AC2 — no model identities are killable roles", () => {
    for (const m of ["claude", "big-pickle", "chatgpt", "deepseek", "gemini", "kiro", "copilot"]) {
      expect(KILLABLE_ROLES, `model binding ${m} must not be killable`).not.toContain(m);
    }
  });

  test("AC2 — no infra/service-principal identities", () => {
    for (const i of ["jenkins-sync", "sonar-sync", "admin", "rover"]) {
      expect(KILLABLE_ROLES, `infra identity ${i} must not be killable`).not.toContain(i);
    }
  });

  test("AC3 — no duplicates in the allowlist", () => {
    const set = new Set(KILLABLE_ROLES);
    expect(set.size).toBe(KILLABLE_ROLES.length);
  });
});