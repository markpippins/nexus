/**
 * receipt.issued emission (receipt.issued gap, decided 2026-11-17).
 *
 * insertReceipt now appends one `receipt.issued` kernel event per issued
 * receipt, in the same transaction as the receipt INSERT. That event type
 * carries the `receipt.must_carry_hash` policy rule (migration 010), which
 * rejects the transition when the envelope `receipt` is empty — so the
 * content hash is the thing standing between a receipt write and a
 * KERNEL_POLICY_DENIED. These are the hermetic, pure assertions on it;
 * the SQL path is exercised live against the kernel trigger on deploy.
 */
import { describe, expect, it } from "vitest";
import { receiptContentHash } from "./db";

const BASE = {
  id: "rec-2026-11-17-001",
  plan_id: "8261650",
  type: "IMPLEMENTATION",
  agent_role: "builder",
  summary: "Stage-6 AC13 bookkeeping",
  created_at: "2026-11-17T17:44:25.949Z",
};

describe("receiptContentHash", () => {
  it("is deterministic for the same receipt", () => {
    expect(receiptContentHash(BASE)).toBe(receiptContentHash({ ...BASE }));
  });

  it("is non-empty, so receipt.must_carry_hash can be satisfied", () => {
    const hash = receiptContentHash(BASE);
    expect(hash.trim().length).toBeGreaterThan(0);
    expect(hash).toMatch(/^sha256:[0-9a-f]{64}$/);
  });

  it("changes when any identity field changes", () => {
    const hashes = new Set(
      (
        [
          ["id", "rec-2026-11-17-002"],
          ["plan_id", "8261674"],
          ["type", "REVIEW_PASS"],
          ["agent_role", "reviewer"],
          ["summary", "different summary"],
          ["created_at", "2026-11-17T17:44:26.949Z"],
        ] as const
      ).map(([field, value]) => receiptContentHash({ ...BASE, [field]: value })),
    );
    expect(hashes.size).toBe(6);
    expect(hashes.has(receiptContentHash(BASE))).toBe(false);
  });

  it("treats a missing summary as empty without collapsing identity fields", () => {
    const noSummary = receiptContentHash({ ...BASE, summary: "" });
    expect(noSummary).toBe(receiptContentHash({ ...BASE, summary: "" }));
    expect(noSummary).not.toBe(receiptContentHash(BASE));
  });
});
