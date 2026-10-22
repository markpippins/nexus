/**
 * unblock_plan is APPEND-ONLY (plan 8261653 follow-up, 2026-10-22).
 *
 * The defect this locks: `unblock_plan` used to DELETE the unblock-family
 * receipts and then append PLAN_CREATE. For a plan-backed plan the BLOCK row
 * lives in `execution.receipts`, which is immutable (trg_receipts_immutable,
 * architect ruling fcec95a2 #2) — the DELETE targeted `vision.receipts` and
 * removed 0 rows, and the transition map then refused PLAN_CREATE from BLOCK
 * (allowed: IMPLEMENTATION, HOLD). Net effect: the plan went pending, the
 * ticket was rebuilt, and NO receipt was ever written (plan 8261653).
 *
 * The reachable path is `BLOCK|PLAN_BLOCK|CANCELLED|ABANDONED → REQUEUED`.
 *
 * Pure and DB-free. Two layers:
 *   1. Vocabulary — the transition map actually admits REQUEUED from each
 *      unblock state, and REQUEUED re-enters the pipeline.
 *   2. Source contract — the handler no longer deletes, and validates the
 *      transition before creating tickets.
 *
 * Usage:
 *   cd typescript/conduit-mcp && npx vitest run src/unblock-requeued.test.ts
 */
import { readFileSync } from "fs";
import { join } from "path";

import { describe, expect, it } from "vitest";

import {
  UNBLOCK_RECEIPT_TYPES,
  allowedTransitionsFrom,
  unblockReceiptTypeFor,
} from "./receipts";

const FAMILY: string[] = [...UNBLOCK_RECEIPT_TYPES];

function readSrc(file: string): string {
  return readFileSync(join(__dirname, file), "utf8");
}

/** The unblock_plan handler body, bounded at the next `name: async` key. */
function unblockHandlerSource(): string {
  const src = readSrc("tools.ts");
  const start = src.indexOf("unblock_plan: async");
  expect(start).toBeGreaterThan(-1);
  const rest = src.slice(start + 1);
  const nextKey = rest.search(/\n {4}[a-z_]+: async/);
  return nextKey === -1 ? src.slice(start) : src.slice(start, start + 1 + nextKey);
}

describe("unblock transition vocabulary", () => {
  it.each(FAMILY)("%s admits REQUEUED", (type) => {
    expect(allowedTransitionsFrom(type)).toContain("REQUEUED");
  });

  it("REQUEUED re-enters the pipeline", () => {
    const to = allowedTransitionsFrom("REQUEUED");
    expect(to).toContain("IMPLEMENTATION");
    expect(to).toContain("PLAN_CREATE");
  });

  it("preserves the legacy BLOCK → IMPLEMENTATION edge", () => {
    expect(allowedTransitionsFrom("BLOCK")).toContain("IMPLEMENTATION");
  });
});

describe("unblockReceiptTypeFor", () => {
  it.each(FAMILY)("latest %s → REQUEUED", (type) => {
    expect(unblockReceiptTypeFor(type)).toBe("REQUEUED");
  });

  it.each(["", "PLAN_CREATE", "IMPLEMENTATION", "PLANNING", "REVIEW_PASS"])(
    "latest %s → PLAN_CREATE (not in the unblock family)",
    (type) => {
      expect(unblockReceiptTypeFor(type)).toBe("PLAN_CREATE");
    },
  );

  it("treats null/undefined latest as PLAN_CREATE (no receipts yet)", () => {
    expect(unblockReceiptTypeFor(null)).toBe("PLAN_CREATE");
    expect(unblockReceiptTypeFor(undefined)).toBe("PLAN_CREATE");
  });
});

describe("unblock_plan source contract", () => {
  it("does not delete receipts (append-only)", () => {
    expect(unblockHandlerSource()).not.toContain("deleteReceiptsByPlanAndType");
  });

  it("appends the resolved type instead of a hardcoded PLAN_CREATE", () => {
    const body = unblockHandlerSource();
    expect(body).toContain("chooseUnblockReceiptType");
    expect(body).toContain("type: receiptType");
    expect(body).not.toContain('type: "PLAN_CREATE"');
  });

  it("validates the transition before creating a ticket (no orphan on refusal)", () => {
    const body = unblockHandlerSource();
    const validateAt = body.indexOf("validateReceipt");
    const ticketAt = body.indexOf("createTicketIfMissing");
    expect(validateAt).toBeGreaterThan(-1);
    expect(ticketAt).toBeGreaterThan(-1);
    expect(validateAt).toBeLessThan(ticketAt);
  });

  it("references the fresh ticket from the receipt", () => {
    expect(unblockHandlerSource()).toContain("ticket_id: ticketId");
  });
});

describe("cross-language unblock family", () => {
  // The family exists twice: here (transition vocabulary) and in the Python
  // DELETE allowlist. Drift between them would silently re-open the 8261653
  // hole, so the two sets are asserted equal.
  it("matches UNBLOCK_RECEIPT_TYPES in routes_receipts.py", () => {
    const py = readFileSync(
      join(__dirname, "..", "..", "..", "python", "conduit", "app", "api", "routes_receipts.py"),
      "utf8",
    );
    const decl = py.match(/UNBLOCK_RECEIPT_TYPES = \{([^}]*)\}/);
    expect(decl).toBeTruthy();
    const pyTypes = [...(decl as RegExpMatchArray)[1].matchAll(/"([A-Z_]+)"/g)].map(
      (m) => m[1],
    );
    expect(new Set(pyTypes)).toEqual(new Set(UNBLOCK_RECEIPT_TYPES));
  });
});

describe("session_id: null deploy drift", () => {
  // The field means "no interactive session". Current main persists NULL; the
  // older deployed API build types session_id as a non-nullable string and
  // 422s on explicit null. Omitting the key works against both.
  it.each(["tools.ts", "watcher.ts"])("%s never sends an explicit null", (file) => {
    expect(readSrc(file)).not.toContain("session_id: null");
  });
});
