import { describe, expect, it } from "vitest";
import {
  classifyReplay,
  ContractRefusal,
  fingerprint,
  parseEnvelope,
  SUPPORTED_CONTRACT_VERSION,
} from "../src/contract";

const envelope = (over: Record<string, unknown> = {}) => ({
  source_implementation: "typescript/lilac-srv/src/routes.ts",
  runtime_route: "POST /v1/receipts",
  contract_version: SUPPORTED_CONTRACT_VERSION,
  source_identity: "agent-record:c71d7901",
  idempotency_key: "idem-receipt-0001",
  ...over,
});

const refusal = (fn: () => unknown): ContractRefusal => {
  try {
    fn();
  } catch (err) {
    if (err instanceof ContractRefusal) return err;
    throw err;
  }
  throw new Error("expected a ContractRefusal, got a value");
};

describe("provenance envelope", () => {
  it("accepts a complete envelope on the route it claims", () => {
    const env = parseEnvelope(envelope(), "POST /v1/receipts");
    expect(env.idempotency_key).toBe("idem-receipt-0001");
    expect(env.contract_version).toBe(SUPPORTED_CONTRACT_VERSION);
  });

  it("refuses a missing envelope with PROVENANCE_MISSING", () => {
    const err = refusal(() => parseEnvelope(undefined, "POST /v1/receipts"));
    expect(err.code).toBe("PROVENANCE_MISSING");
    expect(err.status).toBe(422);
    expect(err.detail.required).toContain("idempotency_key");
  });

  it("refuses a non-object envelope rather than coercing it", () => {
    for (const bad of ["a-string", 42, ["x"], true]) {
      const err = refusal(() => parseEnvelope(bad, "POST /v1/receipts"));
      expect(err.code).toBe("PROVENANCE_MISSING");
    }
  });

  it("names every missing field, not just the first", () => {
    const err = refusal(() =>
      parseEnvelope(
        { source_implementation: "typescript/lilac-srv/src/routes.ts" },
        "POST /v1/receipts",
      ),
    );
    expect(err.code).toBe("PROVENANCE_INCOMPLETE");
    expect(err.detail.missing).toEqual([
      "runtime_route",
      "contract_version",
      "source_identity",
      "idempotency_key",
    ]);
  });

  it("treats whitespace-only fields as missing", () => {
    const err = refusal(() => parseEnvelope(envelope({ source_identity: "   " }), "POST /v1/receipts"));
    expect(err.code).toBe("PROVENANCE_INCOMPLETE");
    expect(err.detail.missing).toContain("source_identity");
  });

  it("refuses an envelope claiming a different route as AMBIGUOUS", () => {
    const err = refusal(() =>
      parseEnvelope(envelope({ runtime_route: "POST /v1/tickets" }), "POST /v1/receipts"),
    );
    expect(err.code).toBe("PROVENANCE_AMBIGUOUS");
    expect(err.detail).toMatchObject({
      expected_route: "POST /v1/receipts",
      claimed_route: "POST /v1/tickets",
    });
  });

  it("refuses an unsupported contract version instead of guessing its shape", () => {
    const err = refusal(() =>
      parseEnvelope(envelope({ contract_version: 2 }), "POST /v1/receipts"),
    );
    expect(err.code).toBe("CONTRACT_VERSION_UNSUPPORTED");
    expect(err.detail).toMatchObject({ received: 2, supported: SUPPORTED_CONTRACT_VERSION });
  });

  it("refuses a non-integer contract version", () => {
    const err = refusal(() =>
      parseEnvelope(envelope({ contract_version: "1" }), "POST /v1/receipts"),
    );
    expect(err.code).toBe("PROVENANCE_INCOMPLETE");
  });

  it("refuses an idempotency key that is just the source identity", () => {
    const err = refusal(() =>
      parseEnvelope(
        envelope({ source_identity: "same-identity-value", idempotency_key: "same-identity-value" }),
        "POST /v1/receipts",
      ),
    );
    expect(err.code).toBe("PROVENANCE_AMBIGUOUS");
  });

  it("refuses a non-resolvable source_implementation", () => {
    const err = refusal(() =>
      parseEnvelope(envelope({ source_implementation: "not a path!" }), "POST /v1/receipts"),
    );
    expect(err.code).toBe("PROVENANCE_AMBIGUOUS");
  });

  it("refuses an empty idempotency key as incomplete", () => {
    const err = refusal(() => parseEnvelope(envelope({ idempotency_key: "short" }), "POST /v1/receipts"));
    expect(err.code).toBe("PROVENANCE_INCOMPLETE");
    expect(err.detail.length).toBe(5);
  });
});

describe("fingerprints", () => {
  it("is stable across key order", () => {
    expect(fingerprint({ a: 1, b: 2 })).toBe(fingerprint({ b: 2, a: 1 }));
  });

  it("changes when any value changes", () => {
    expect(fingerprint({ a: 1 })).not.toBe(fingerprint({ a: 2 }));
  });

  it("distinguishes nested key order and array order", () => {
    expect(fingerprint({ x: { a: 1, b: 2 } })).toBe(fingerprint({ x: { b: 2, a: 1 } }));
    expect(fingerprint({ x: [1, 2] })).not.toBe(fingerprint({ x: [2, 1] }));
  });
});

describe("replay classification", () => {
  it("classifies an identical replay as duplicate-equivalent", () => {
    expect(classifyReplay("receipt", "abc", "abc")).toBe("duplicate-equivalent");
  });

  it("refuses a conflicting replay and records BOTH fingerprints", () => {
    const err = refusal(() => classifyReplay("receipt", "existing-fp", "incoming-fp"));
    expect(err.code).toBe("CONFLICT");
    expect(err.status).toBe(409);
    expect(err.detail).toMatchObject({
      existing_fingerprint: "existing-fp",
      incoming_fingerprint: "incoming-fp",
    });
    expect(err.message).toContain("existing-fp");
    expect(err.message).toContain("incoming-fp");
  });

  it("refuses rather than accepting when no fingerprint is stored", () => {
    const err = refusal(() => classifyReplay("ticket", null, "incoming-fp"));
    expect(err.code).toBe("CONFLICT");
    expect(err.detail.existing_fingerprint).toBe("(none)");
  });
});
