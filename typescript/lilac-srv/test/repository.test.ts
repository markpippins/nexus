import { beforeEach, describe, expect, it } from "vitest";
import { FakeDb } from "./fake-db";
import { LilacRepository } from "../src/repository";
import { ContractRefusal, fingerprint, SUPPORTED_CONTRACT_VERSION } from "../src/contract";

const env = (key: string, over: Record<string, unknown> = {}) => ({
  source_implementation: "typescript/lilac-srv/src/routes.ts",
  runtime_route: "POST /v1/receipts",
  contract_version: SUPPORTED_CONTRACT_VERSION,
  source_identity: "agent-record:c71d7901",
  idempotency_key: key,
  ...over,
});

const captures = async (fn: () => Promise<unknown>): Promise<ContractRefusal> => {
  try {
    await fn();
  } catch (err) {
    if (err instanceof ContractRefusal) return err;
    throw err;
  }
  throw new Error("expected a ContractRefusal");
};

let db: FakeDb;
let repo: LilacRepository;

beforeEach(() => {
  db = new FakeDb();
  repo = new LilacRepository(db);
});

describe("receipt idempotency (AC2)", () => {
  const payload = { plan: "8261669", outcome: "REVIEW_PASS" };

  it("accepts, then classifies an identical replay as duplicate-equivalent", async () => {
    const first = await repo.issueReceipt({
      envelope: env("idem-r-1"),
      producer_id: "lilac-srv",
      kind: "implementation",
      source_system: "conduit",
      source_receipt_id: "src-1",
      payload,
    });
    expect(first.outcome).toBe("accepted");

    const replay = await repo.issueReceipt({
      envelope: env("idem-r-2"),
      producer_id: "lilac-srv",
      kind: "implementation",
      source_system: "conduit",
      source_receipt_id: "src-1",
      payload,
    });
    expect(replay.outcome).toBe("duplicate-equivalent");
    expect(replay.target_id).toBe(first.target_id);
    expect(db.tables.receipt).toHaveLength(1);
  });

  it("refuses a conflicting replay with both fingerprints and writes nothing", async () => {
    await repo.issueReceipt({
      envelope: env("idem-r-3"),
      producer_id: "lilac-srv",
      kind: "implementation",
      source_system: "conduit",
      source_receipt_id: "src-2",
      payload,
    });
    const before = db.tables.receipt[0].payload;

    const err = await captures(() =>
      repo.issueReceipt({
        envelope: env("idem-r-4"),
        producer_id: "lilac-srv",
        kind: "implementation",
        source_system: "conduit",
        source_receipt_id: "src-2",
        payload: { plan: "8261669", outcome: "REVIEW_REJECT" },
      }),
    );

    expect(err.code).toBe("CONFLICT");
    expect(err.detail.existing_fingerprint).toBe(fingerprint(payload));
    expect(err.detail.incoming_fingerprint).toBe(
      fingerprint({ plan: "8261669", outcome: "REVIEW_REJECT" }),
    );
    // The stored row is untouched and the refused attempt left no trace.
    expect(db.tables.receipt[0].payload).toEqual(before);
    expect(db.tables.mutation_provenance).toHaveLength(1);
  });

  it("refuses to spend one idempotency key on a second distinct receipt", async () => {
    await repo.issueReceipt({
      envelope: env("idem-r-5"),
      producer_id: "lilac-srv",
      kind: "implementation",
      source_system: "conduit",
      source_receipt_id: "src-3",
      payload,
    });

    const err = await captures(() =>
      repo.issueReceipt({
        envelope: env("idem-r-5"),
        producer_id: "lilac-srv",
        kind: "implementation",
        source_system: "conduit",
        source_receipt_id: "src-4",
        payload,
      }),
    );
    // Key already spent, and no receipt exists for src-4 → the key cannot be
    // reused for a different target; refuse rather than create a second row.
    expect(err.code).toBe("PROVENANCE_ORPHAN");
    expect(db.tables.receipt).toHaveLength(1);
  });
});

describe("ticket idempotency (AC2)", () => {
  it("accepts, duplicates, and refuses a conflicting objective", async () => {
    const base = {
      workflow_ref: "wr-8261669",
      role: "engineer",
      position: 1,
      generation: 0,
    };
    const first = await repo.issueTicket({
      envelope: env("idem-t-1", { runtime_route: "POST /v1/tickets" }),
      ...base,
      objective: "build the surface",
    });
    expect(first.outcome).toBe("accepted");

    const dup = await repo.issueTicket({
      envelope: env("idem-t-2", { runtime_route: "POST /v1/tickets" }),
      ...base,
      objective: "build the surface",
    });
    expect(dup.outcome).toBe("duplicate-equivalent");
    expect(dup.target_id).toBe(first.target_id);

    const err = await captures(() =>
      repo.issueTicket({
        envelope: env("idem-t-3", { runtime_route: "POST /v1/tickets" }),
        ...base,
        objective: "something else entirely",
      }),
    );
    expect(err.code).toBe("CONFLICT");
    expect(err.detail.surface).toBe("ticket");
    expect(db.tables.ticket).toHaveLength(1);
  });

  it("treats a different generation as a different ticket", async () => {
    const base = { workflow_ref: "wr-8261669", role: "engineer", position: 1 };
    await repo.issueTicket({
      envelope: env("idem-t-4", { runtime_route: "POST /v1/tickets" }),
      ...base,
      generation: 0,
      objective: "gen 0",
    });
    const second = await repo.issueTicket({
      envelope: env("idem-t-5", { runtime_route: "POST /v1/tickets" }),
      ...base,
      generation: 1,
      objective: "gen 1",
    });
    expect(second.outcome).toBe("accepted");
    expect(db.tables.ticket).toHaveLength(2);
  });
});

describe("transition idempotency (AC2)", () => {
  const base = {
    ticket_id: "11111111-1111-1111-1111-111111111111",
    input_receipt_id: "22222222-2222-2222-2222-222222222222",
    from_status: "pending",
    to_status: "claimed",
  };
  const route = "POST /v1/tickets/{ticket_id}/transitions";

  it("accepts, duplicates, and refuses a differing payload", async () => {
    const first = await repo.recordTransition({
      envelope: env("idem-x-1", { runtime_route: route }),
      ...base,
      payload: { actor: "engineer" },
    });
    expect(first.outcome).toBe("accepted");

    const dup = await repo.recordTransition({
      envelope: env("idem-x-2", { runtime_route: route }),
      ...base,
      payload: { actor: "engineer" },
    });
    expect(dup.outcome).toBe("duplicate-equivalent");

    const err = await captures(() =>
      repo.recordTransition({
        envelope: env("idem-x-3", { runtime_route: route }),
        ...base,
        payload: { actor: "reviewer" },
      }),
    );
    expect(err.code).toBe("CONFLICT");
    expect(err.detail).toMatchObject({ surface: "ticket_transition" });
    expect(db.tables.ticket_transition).toHaveLength(1);
  });
});

describe("fan-out idempotency (AC2)", () => {
  const base = {
    input_receipt_id: "33333333-3333-3333-3333-333333333333",
    kind: "implementation",
    produced: [{ ticket_id: "a", action: "spawned" }],
  };
  const route = "POST /v1/fanouts";

  it("accepts, duplicates, and persists both fingerprints on a conflict", async () => {
    const first = await repo.recordFanout({
      envelope: env("idem-f-1", { runtime_route: route }),
      ...base,
    });
    expect(first.outcome).toBe("accepted");

    const dup = await repo.recordFanout({
      envelope: env("idem-f-2", { runtime_route: route }),
      ...base,
    });
    expect(dup.outcome).toBe("duplicate-equivalent");

    const err = await captures(() =>
      repo.recordFanout({
        envelope: env("idem-f-3", { runtime_route: route }),
        ...base,
        produced: [{ ticket_id: "b", action: "spawned" }],
      }),
    );
    expect(err.code).toBe("CONFLICT");
    // fanout has a durable home for the pair, and it must be used.
    const stored = db.tables.fanout_transition[0].conflict_fingerprints;
    expect(stored).toEqual({
      existing: err.detail.existing_fingerprint,
      incoming: err.detail.incoming_fingerprint,
    });
    expect(db.tables.fanout_transition).toHaveLength(1);
  });

  it("keeps the persisted pair after the refusal (the record must survive)", async () => {
    await repo.recordFanout({ envelope: env("idem-f-4", { runtime_route: route }), ...base });
    const err = await captures(() =>
      repo.recordFanout({
        envelope: env("idem-f-5", { runtime_route: route }),
        ...base,
        produced: [{ ticket_id: "z", action: "spawned" }],
      }),
    );
    // Committed before the throw: a refusal that deletes its own evidence
    // would make the conflict unprovable after the caller drops the response.
    expect(db.tables.fanout_transition[0].conflict_fingerprints).toEqual({
      existing: err.detail.existing_fingerprint,
      incoming: err.detail.incoming_fingerprint,
    });
    expect(db.tables.fanout_transition).toHaveLength(1);
  });
});

describe("idempotency keys are surface-scoped (AC3)", () => {
  it("refuses a key already spent on a different surface", async () => {
    await repo.issueTicket({
      envelope: env("shared-key-1", { runtime_route: "POST /v1/tickets" }),
      workflow_ref: "wr-8261669",
      role: "engineer",
      position: 2,
      objective: "x",
    });
    const err = await captures(() =>
      repo.recordFanout({
        envelope: env("shared-key-1", { runtime_route: "POST /v1/fanouts" }),
        input_receipt_id: "44444444-4444-4444-4444-444444444444",
        kind: "implementation",
        produced: [],
      }),
    );
    expect(err.code).toBe("IDEMPOTENCY_KEY_REUSED");
    expect(err.detail).toMatchObject({
      recorded_surface: "ticket",
      incoming_surface: "fanout_transition",
    });
  });
});

describe("reads", () => {
  it("refuses an unknown ticket timeline with 404 rather than an empty body", async () => {
    const err = await captures(() => repo.ticketTimeline("does-not-exist"));
    expect(err.status).toBe(404);
  });

  it("reports parity as unrecorded when no evidence exists", async () => {
    const out = (await repo.parityDiagnostics()) as Record<string, unknown>;
    expect(out.recorded).toBe(false);
    expect(out.evidence).toEqual([]);
    expect(out.provenance).toEqual([]);
  });

  it("counts provenance per surface once mutations exist", async () => {
    await repo.issueTicket({
      envelope: env("idem-t-9", { runtime_route: "POST /v1/tickets" }),
      workflow_ref: "wr-8261669",
      role: "engineer",
      position: 3,
      objective: "y",
    });
    const out = (await repo.parityDiagnostics()) as Record<string, any>;
    expect(out.provenance).toEqual([{ surface: "ticket", mutations: 1 }]);
  });
});
