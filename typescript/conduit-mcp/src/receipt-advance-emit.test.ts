/**
 * Receipt-advance emission tests (ADR-016 closure hole, 2026-09-25).
 *
 * Hermetic: exercises the pure mapping helpers that drive the kernel
 * transition emission in advanceTicketsOnReceipt — receipt kind →
 * (role, terminal status) → event type. The SQL path itself (UPDATE +
 * recordTransition in one transaction) mirrors releaseSessionTickets /
 * cancelTicket, which are verified live on deploy; the emission is
 * additionally attested fleet-wide by the series:conduit-attestation
 * nightly check (bin/check_transition_attestation.py).
 */
import { describe, expect, it } from "vitest";
import { receiptClosureEventType, receiptToCompletingRole } from "./db";

describe("receiptClosureEventType", () => {
  it("maps success closures to transition.committed", () => {
    expect(receiptClosureEventType("completed")).toBe("transition.committed");
  });

  it("maps failure closures to transition.rejected", () => {
    expect(receiptClosureEventType("failed")).toBe("transition.rejected");
  });
});

describe("receiptToCompletingRole (closure surface)", () => {
  it("completes the builder on IMPLEMENTATION", () => {
    expect(receiptToCompletingRole("IMPLEMENTATION")).toEqual({
      role: "builder",
      status: "completed",
    });
  });

  it("completes the reviewer on REVIEW_PASS", () => {
    expect(receiptToCompletingRole("REVIEW_PASS")).toEqual({
      role: "reviewer",
      status: "completed",
    });
  });

  it("fails the reviewer on REVIEW_REJECT", () => {
    expect(receiptToCompletingRole("REVIEW_REJECT")).toEqual({
      role: "reviewer",
      status: "failed",
    });
  });

  it("completes the critic on both critique verdicts", () => {
    expect(receiptToCompletingRole("CRITIQUE_PASS")).toEqual({
      role: "critic",
      status: "completed",
    });
    expect(receiptToCompletingRole("CRITIQUE_REJECT")).toEqual({
      role: "critic",
      status: "completed",
    });
  });

  it("returns null for non-closure receipts (PLAN_CREATE creates, never completes)", () => {
    expect(receiptToCompletingRole("PLAN_CREATE")).toBeNull();
    expect(receiptToCompletingRole("BLOCK")).toBeNull();
    expect(receiptToCompletingRole("SOMETHING_NEW")).toBeNull();
  });
});

describe("closure → event-type consistency (the emission contract)", () => {
  const RECEIPTS = [
    "IMPLEMENTATION",
    "CRITIQUE_PASS",
    "CRITIQUE_REJECT",
    "REVIEW_PASS",
    "REVIEW_REJECT",
  ] as const;

  it("every closable receipt yields a kernel event type, derived from its terminal status", () => {
    for (const r of RECEIPTS) {
      const m = receiptToCompletingRole(r);
      expect(m).not.toBeNull();
      const eventType = receiptClosureEventType(m!.status);
      expect(["transition.committed", "transition.rejected"]).toContain(eventType);
      // Reviewer reject is the one failure closure in the family.
      if (r === "REVIEW_REJECT") {
        expect(eventType).toBe("transition.rejected");
      } else {
        expect(eventType).toBe("transition.committed");
      }
    }
  });
});
