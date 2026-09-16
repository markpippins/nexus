import {
  InMemoryDoctrineLookup,
  type DoctrineLookup,
  type DoctrineLookupRequest,
  type DoctrineRecord,
} from "./doctrineLookup.js";
import {
  DoctrineLookupRegistry,
  resolveDivergence,
} from "./doctrineLookup.registry.js";

function equal(actual: unknown, expected: unknown, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`);
  }
}

function ok(condition: boolean, message: string): void {
  if (!condition) throw new Error(`FAILED: ${message}`);
}

function record(id: string, version: number): DoctrineRecord {
  return {
    kind: "doctrine",
    id,
    version,
    digest: `sha256:${id}-v${version}`,
    effectiveFrom: "2026-01-01T00:00:00.000Z",
    supersededAt: null,
    sourceDecisionId: `decision-${id}-v${version}`,
  };
}

function staticLookup(
  status: DoctrineLookupResultStatus,
  reason?: string,
): DoctrineLookup {
  return {
    async lookup() {
      return { status, consulted: true, latencyMs: 1, reason };
    },
  };
}

type DoctrineLookupResultStatus = "resolved" | "unknown" | "refusal" | "stale";

const REQ: DoctrineLookupRequest = {
  kind: "doctrine",
  stableId: "doctrine-1",
  asOf: "2026-06-01T00:00:00.000Z",
};

let tick = 0;
const fixedNow = (): string => {
  tick += 1;
  return `2026-08-30T00:00:00.${String(tick).padStart(3, "0")}Z`;
};

function makeRegistry(minObs = 3) {
  const primary = {
    id: "pg-adapter",
    adapter: new InMemoryDoctrineLookup([record("doctrine-1", 1)]),
  };
  const fallback = {
    id: "memory-adapter",
    adapter: new InMemoryDoctrineLookup([record("doctrine-1", 1)]),
  };
  return new DoctrineLookupRegistry({
    primary,
    fallback,
    minObservationsForRetirement: minObs,
    now: fixedNow,
  });
}

export async function runRegistryConformance(): Promise<void> {
  // ── 1. Active registry routes to primary and records observations ──
  {
    const registry = makeRegistry();
    const result = await registry.lookup(REQ);
    equal(result.status, "resolved", "active registry resolves via primary");
    equal(registry.activeAdapterId, "pg-adapter", "active adapter is primary");
    equal(registry.getObservations().length, 1, "observation recorded");
  }

  // ── 2. Constructor validation (fail-closed) ──
  {
    let threw = false;
    try {
      new DoctrineLookupRegistry({
        primary: { id: "a", adapter: staticLookup("resolved") },
        fallback: { id: "a", adapter: staticLookup("resolved") },
      });
    } catch (e) {
      threw = (e as Error).message === "registry_adapter_ids_must_differ";
    }
    ok(threw, "duplicate adapter ids rejected");
    threw = false;
    try {
      new DoctrineLookupRegistry({
        primary: { id: "a", adapter: staticLookup("resolved") },
        fallback: { id: "b" },
      } as never);
    } catch {
      threw = true;
    }
    ok(threw, "missing adapter lookup rejected");
  }

  // ── 3. Primary failure → fallback authoritative + divergence recorded ──
  {
    const registry = new DoctrineLookupRegistry({
      primary: { id: "pg", adapter: staticLookup("unknown", "db_down") },
      fallback: { id: "memory", adapter: new InMemoryDoctrineLookup([record("doctrine-1", 1)]) },
      minObservationsForRetirement: 1,
      now: fixedNow,
    });
    const result = await registry.lookup(REQ);
    equal(result.status, "resolved", "fallback authoritative on primary failure");
    equal(registry.getDivergences().length, 1, "divergence recorded");
    equal(registry.evaluateRetirementGate().passed, false, "open divergence blocks gate");
  }

  // ── 4. Retirement gate: fail-closed until observations + zero open divergences ──
  {
    const registry = makeRegistry(3);
    equal(registry.evaluateRetirementGate().passed, false, "gate closed with no observations");
    await registry.lookup(REQ);
    await registry.lookup(REQ);
    const gate = registry.evaluateRetirementGate();
    equal(gate.observations, 2, "two observations counted");
    equal(gate.passed, false, "gate still closed below threshold");
    await registry.lookup(REQ);
    ok(registry.evaluateRetirementGate().passed, "gate passes at threshold with zero divergences");
  }

  // ── 5. requestRetirement without gate → not requested, state unchanged ──
  {
    const registry = makeRegistry(5);
    await registry.lookup(REQ);
    const decision = registry.requestRetirement();
    equal(decision.requested, false, "retirement refused below threshold");
    equal(registry.primaryAdapterState, "active", "state unchanged on refused request");
    equal(registry.getLifecycleEvents().length, 0, "no lifecycle event on refusal");
  }

  // ── 6. Full retirement flow: request → confirm (Architect-owned) ──
  {
    const registry = makeRegistry(1);
    await registry.lookup(REQ);
    const decision = registry.requestRetirement("gate green");
    equal(decision.requested, true, "retirement requested");
    equal(decision.pendingArchitectDecision, true, "awaiting architect decision");
    equal(registry.primaryAdapterState, "retiring", "state retiring after request");
    const state = registry.confirmRetirement("architect approved");
    equal(state, "retired", "retired after confirmation");
    equal(registry.activeAdapterId, "memory-adapter", "fallback now authoritative");
    const result = await registry.lookup(REQ);
    equal(result.status, "resolved", "retired registry still resolves via fallback");
    equal(registry.getLifecycleEvents().length, 2, "request + confirm events recorded");
    equal(registry.getLifecycleEvents()[0]!.type, "retirement_requested", "event 1 type");
    equal(registry.getLifecycleEvents()[1]!.type, "retirement_confirmed", "event 2 type");
  }

  // ── 7. Retirement rejection returns to active ──
  {
    const registry = makeRegistry(1);
    await registry.lookup(REQ);
    registry.requestRetirement();
    equal(registry.rejectRetirement("not yet"), "active", "rejection restores active");
  }

  // ── 8. confirmRetirement/rejectRetirement fail-closed without pending request ──
  {
    const registry = makeRegistry(1);
    let threw = false;
    try {
      registry.confirmRetirement();
    } catch (e) {
      threw = (e as Error).message === "retirement_not_pending";
    }
    ok(threw, "confirm without request rejected");
    threw = false;
    try {
      registry.rejectRetirement();
    } catch (e) {
      threw = (e as Error).message === "retirement_not_pending";
    }
    ok(threw, "reject without request rejected");
  }

  // ── 9. Rollback: immediate fallback, immutable append-only history ──
  {
    // Retired durumdan rollback no-op'tur (event eklemez).
    const registry = makeRegistry(1);
    await registry.lookup(REQ);
    registry.requestRetirement();
    registry.confirmRetirement();
    equal(registry.rollback("primary regression"), "retired", "rollback on retired is no-op state");
    equal(registry.getLifecycleEvents().length, 2, "no extra event on retired rollback");

    // Retiring durumdan rollback acil fallback'e geçer + event ekler.
    const registry2 = makeRegistry(1);
    await registry2.lookup(REQ);
    registry2.requestRetirement();
    equal(registry2.rollback("regression drill"), "retired", "rollback from retiring goes retired");
    const events2 = registry2.getLifecycleEvents();
    equal(events2.length, 2, "rollback appends event");
    equal(events2[events2.length - 1]!.type, "rollback", "rollback event type");
    equal(events2[0]!.type, "retirement_requested", "history preserved (append-only)");
  }


  // ── 10. Divergence resolution unblocks the gate; double-resolve rejected ──
  {
    const registry = new DoctrineLookupRegistry({
      primary: { id: "pg", adapter: staticLookup("unknown", "db_down") },
      fallback: { id: "memory", adapter: new InMemoryDoctrineLookup([record("doctrine-1", 1)]) },
      minObservationsForRetirement: 1,
      now: fixedNow,
    });
    await registry.lookup(REQ);
    equal(registry.evaluateRetirementGate().passed, false, "gate blocked by open divergence");
    resolveDivergence(registry, 0, "architect");
    equal(registry.evaluateRetirementGate().passed, true, "gate unblocked after resolution");
    let threw = false;
    try {
      resolveDivergence(registry, 0, "architect");
    } catch (e) {
      threw = (e as Error).message === "divergence_already_resolved";
    }
    ok(threw, "double resolve rejected");
  }

  // ── 11. Historical replay evidence immutability: observations never rewritten ──
  {
    const registry = makeRegistry(2);
    await registry.lookup(REQ);
    await registry.lookup(REQ);
    const before = registry.getObservations().map((o) => ({ at: o.at, status: o.result.status }));
    registry.requestRetirement();
    registry.confirmRetirement();
    registry.rollback("drill");
    const after = registry.getObservations();
    equal(after.length, 2, "observations preserved across lifecycle events");
    equal(after[0]!.at, before[0]!.at, "first observation timestamp unchanged");
    equal(after[0]!.result.status, before[0]!.status, "first observation status unchanged");
    equal(after[1]!.at, before[1]!.at, "second observation timestamp unchanged");
  }

  console.log(`registry conformance: 11 scenario groups passed`);
}

// Self-run when executed directly via tsx.
declare const process: { argv: string[]; exit: (code: number) => void };
const isMain =
  process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop() ?? "@@");
if (isMain) {
  runRegistryConformance().then(
    () => process.exit(0),
    (e) => {
      console.error(e);
      process.exit(1);
    },
  );
}
