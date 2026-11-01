/**
 * Regression tests for the plan payload builder (finding bf7e8f49, option B).
 *
 * The upsert merges: `payload = blueprints_history.payload || EXCLUDED.payload`.
 * Every key the builder emits therefore OVERWRITES whatever the canonical
 * payload already held, and a merge cannot tell "caller didn't supply this"
 * from "caller wants it cleared". Before this fix the builder emitted
 * `tags: []`, `spec_ref: null`, `requirement_ref: null` and
 * `project: plan.project ?? null` unconditionally — none of which the input
 * shape can even supply — so the first `update_plan` call on a plan emptied its
 * tag list and nulled a real `requirement_ref` (4 plans held one).
 *
 * Pinned invariants:
 *   PP1 — substance keys are always present (goal/content/arrays), so a normal
 *         edit still writes a complete payload.
 *   PP2 — placeholder keys are NEVER emitted: tags, spec_ref, requirement_ref.
 *   PP3 — project is emitted only when supplied; omitted otherwise.
 *   PP4 — array fields accept JSON-array strings and comma-separated text.
 *   PP5 — the merge simulation: a goal-only update leaves project,
 *         requirement_ref and tags exactly as they were.
 *   PP6 — an explicit empty project is honored (supplied ≠ omitted), while
 *         null counts as an omission.
 *
 * Pure and DB-free.
 *
 * Usage:
 *   cd typescript/conduit-mcp
 *   npx vitest run src/plan-payload.test.ts
 */
import { describe, test, expect } from "vitest";

import { buildPlanPayload, UpsertPlanInput } from "./db";

function input(overrides: Partial<UpsertPlanInput> = {}): UpsertPlanInput {
  return {
    id: "8261699",
    file_name: "",
    title: "Test plan",
    goal: "do the thing",
    content: "body",
    files_affected: '["a.ts"]',
    acceptance_criteria: '["works"]',
    dependencies: "[]",
    prompt_ref: "",
    notes: "",
    priority: 0,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  } as UpsertPlanInput;
}

describe("buildPlanPayload", () => {
  test("PP1 — substance keys are always present", () => {
    const p = buildPlanPayload(input());
    expect(p.goal).toBe("do the thing");
    expect(p.content).toBe("body");
    expect(p.files_affected).toEqual(["a.ts"]);
    expect(p.acceptance_criteria).toEqual(["works"]);
    expect(p.dependencies).toEqual([]);
  });

  test("PP2 — the three placeholder keys are never emitted", () => {
    const p = buildPlanPayload(input());
    // Regression: these used to be emitted on every write, clobbering real
    // values in the canonical payload on the next merge.
    expect(p).not.toHaveProperty("tags");
    expect(p).not.toHaveProperty("spec_ref");
    expect(p).not.toHaveProperty("requirement_ref");
  });

  test("PP3 — project is emitted only when supplied", () => {
    expect(buildPlanPayload(input())).not.toHaveProperty("project");
    expect(buildPlanPayload(input({ project: "nexus" })).project).toBe("nexus");
  });

  test("PP4 — array fields accept JSON arrays and comma-separated text", () => {
    const p = buildPlanPayload(
      input({
        files_affected: "src/a.ts,src/b.ts",
        acceptance_criteria: '["one","two"]',
        dependencies: "",
      }),
    );
    expect(p.files_affected).toEqual(["src/a.ts", "src/b.ts"]);
    expect(p.acceptance_criteria).toEqual(["one", "two"]);
    expect(p.dependencies).toEqual([]);
  });

  test("PP5 — a goal-only update preserves project, requirement_ref and tags", () => {
    // The canonical payload as stored today.
    const stored = {
      goal: "old goal",
      content: "body",
      files_affected: ["a.ts"],
      acceptance_criteria: ["works"],
      dependencies: [],
      project: "nexus",
      requirement_ref: "0b9c5f4e-1111-2222-3333-444455556666",
      tags: ["infra", "urgent"],
    };

    // What update_plan sends when only the goal changes.
    const patch = buildPlanPayload(input({ goal: "new goal" }));

    // The upsert's merge, verbatim: payload || EXCLUDED.payload.
    const merged = { ...stored, ...patch };

    expect(merged.goal).toBe("new goal");
    expect(merged.project).toBe("nexus");
    expect(merged.requirement_ref).toBe("0b9c5f4e-1111-2222-3333-444455556666");
    expect(merged.tags).toEqual(["infra", "urgent"]);
  });

  test("PP6 — an explicit empty project clears; null counts as an omission", () => {
    expect(buildPlanPayload(input({ project: "" })).project).toBe("");
    expect(buildPlanPayload(input({ project: null }))).not.toHaveProperty("project");
  });

  test("PP7 — optional fidelity fields ride along only when truthy", () => {
    const bare = buildPlanPayload(input());
    expect(bare).not.toHaveProperty("prompt_ref");
    expect(bare).not.toHaveProperty("notes");
    expect(bare).not.toHaveProperty("priority");

    const full = buildPlanPayload(
      input({ prompt_ref: "p-1", notes: "n", priority: 2 }),
    );
    expect(full.prompt_ref).toBe("p-1");
    expect(full.notes).toBe("n");
    expect(full.priority).toBe(2);
  });
});
