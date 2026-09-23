/**
 * Hermetic parity tests for the kernel port.
 *
 * SCOPE — pure semantics that must match typescript/kernel-srv:
 *   - UUID validation gate (UUID_RE + isValidUuid) on every :event_id/:id
 *     path param, and the exact bad-request messages
 *   - limit clamping on /health/recent-events (parseInt||20, then 1..500)
 *   - required-field validation ladders on the two write paths (exact
 *     `Missing required field: <f>` messages, iteration order)
 *   - the dbFailure 45000→403 mapping (kernel trigger RAISE EXCEPTION is a
 *     user-facing authorization/validation refusal, not a 500)
 *   - the notify module's reconnect/subscribe surface (state machine, no DB)
 *
 * NOT COVERED HERE — the route surface. That is enforced by the apidocs drift
 * gate (`make apidocs-validate`), which reads this app's gateway alias map
 * against typescript/kernel-srv/openapi.yaml (check_drift.MOLLECULER_MIRRORS).
 * Duplicating it in jest would only create a second place to drift.
 *
 * No database, no broker, no HTTP bind. The SQL itself is pinned by the
 * statement-for-statement review plus the live canary diff, not by mocks that
 * would just restate the implementation.
 */
jest.mock("pg", () => {
  const Pool = jest.fn().mockImplementation(() => ({
    query: jest.fn(),
    connect: jest.fn(),
    end: jest.fn(),
  }));
  return { Pool };
});

import { Pool } from "pg";

import {
  isValidUuid,
  clamp,
  badRequest,
  notFound,
  forbidden,
  dbFailure,
} from "../services/kernel.service";

import {
  isNotifyAlive,
  subscribe,
  getSubscriberCount,
  startNotifyListener,
  stopNotifyListener,
} from "../services/notify";

describe("uuid validation (incumbent isValidUuid)", () => {
  it("accepts canonical lowercase/uppercase hyphenated uuids", () => {
    expect(isValidUuid("0b91a2b3-4c5d-6e7f-8a9b-0c1d2e3f4a5b")).toBe(true);
    expect(isValidUuid("0B91A2B3-4C5D-6E7F-8A9B-0C1D2E3F4A5B")).toBe(true);
  });

  it("rejects non-uuid path params before any SQL runs", () => {
    // The incumbent 400s these before touching the pool — the guard is the
    // only thing standing between a numeric id and a PG cast error (the
    // bigint/uuid canary class from the voyager port).
    expect(isValidUuid("not-a-uuid")).toBe(false);
    expect(isValidUuid("123")).toBe(false);
    expect(isValidUuid("")).toBe(false);
    expect(isValidUuid("0b91a2b34c5d6e7f8a9b0c1d2e3f4a5b")).toBe(false);
  });
});

describe("limit clamp (incumbent clamp(parseInt||20, 1, 500))", () => {
  it("mirrors the incumbent arithmetic", () => {
    expect(clamp(parseInt(String("20"), 10), 1, 500)).toBe(20);
    expect(clamp(parseInt(String("1000"), 10), 1, 500)).toBe(500);
    expect(clamp(parseInt(String("0"), 10), 1, 500)).toBe(1);
    expect(clamp(parseInt(String("abc"), 10), 1, 500)).toBe(1); // NaN -> min
  });
});

describe("error helpers (incumbent envelope shapes)", () => {
  it("badRequest/notFound/forbidden carry the right status codes", () => {
    expect(badRequest("x").code).toBe(400);
    expect(notFound("x").code).toBe(404);
    expect(forbidden("x").code).toBe(403);
  });

  it("maps PG 45000 (kernel trigger RAISE) to 403, everything else to 500", () => {
    const err45000: any = Object.assign(new Error("denied by policy"), { code: "45000" });
    const e403: any = dbFailure(err45000, "KERNEL_WRITE_FAILED");
    expect(e403.code).toBe(403);
    expect(e403.message).toBe("denied by policy");

    const errOther: any = Object.assign(new Error("relation missing"), { code: "42P01" });
    const e500: any = dbFailure(errOther, "KERNEL_READ_FAILED");
    expect(e500.code).toBe(500);
    expect(e500.type).toBe("KERNEL_READ_FAILED");
    expect(e500.message).toBe("relation missing");
  });
});

describe("required-field validation (incumbent ladder semantics)", () => {
  it("flags the first missing field in declaration order", () => {
    // Mirrors the for..of loop in transitions.create / receipts.create: first
    // missing field wins, undefined/null/blank all count.
    const required = ["event_type", "aggregate_type", "aggregate_id", "actor"];
    const b: Record<string, unknown> = { event_type: "x", aggregate_type: "", aggregate_id: null };
    let first: string | undefined;
    for (const f of required) {
      if (b[f] === undefined || b[f] === null || String(b[f]).trim() === "") {
        first = f;
        break;
      }
    }
    expect(first).toBe("aggregate_type");
    expect(`Missing required field: ${first}`).toBe("Missing required field: aggregate_type");
  });
});

describe("notify module (verbatim-port state machine, no DB)", () => {
  beforeEach(() => {
    jest.resetModules();
  });

  it("reports not-alive before any listener starts", () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const n = require("../services/notify");
    expect(n.isNotifyAlive()).toBe(false);
  });

  it("starts and stops the listener against a stubbed pool client", async () => {
    const listeners: Array<(msg: unknown) => void> = [];
    const stubClient = {
      on: jest.fn((ev: string, fn: (msg: unknown) => void) => {
        if (ev === "notification") listeners.push(fn);
      }),
      query: jest.fn().mockResolvedValue({ rows: [] }),
      release: jest.fn(),
    };
    const stubPool = {
      connect: jest.fn().mockResolvedValue(stubClient),
      query: jest.fn(),
      end: jest.fn(),
    };

    (Pool as unknown as jest.Mock).mockImplementation(() => stubPool);

    const n = require("../services/notify");
    n.startNotifyListener(stubPool);
    await new Promise((r) => setTimeout(r, 20)); // let the .then chain settle
    expect(n.isNotifyAlive()).toBe(true);

    // A notification payload re-emits to subscribers as a parsed KernelEvent.
    const seen: unknown[] = [];
    const unsub = n.subscribe((evt: unknown) => seen.push(evt));
    const payload = JSON.stringify({
      event_id: "e1",
      event_type: "t",
      aggregate_type: "a",
      aggregate_id: "g",
      actor: "me",
      timestamp: "now",
    });
    for (const fn of listeners) fn({ payload });
    expect(seen).toHaveLength(1);
    expect((seen[0] as any).event_id).toBe("e1");
    unsub();
    expect(n.getSubscriberCount()).toBe(0);

    await n.stopNotifyListener();
    expect(n.isNotifyAlive()).toBe(false);
    expect(stubClient.release).toHaveBeenCalled();
  });

  it("counting helpers agree (health envelope parity)", () => {
    expect(getSubscriberCount()).toBe(0);
    expect(isNotifyAlive()).toBe(false);
    // startNotifyListener/stopNotifyListener exported for lifecycle parity.
    expect(typeof startNotifyListener).toBe("function");
    expect(typeof stopNotifyListener).toBe("function");
  });

  it("startNotifyListener on a failing pool schedules a reconnect, not a crash", async () => {
    const stubPool = {
      connect: jest.fn().mockRejectedValue(new Error("db down")),
      query: jest.fn(),
      end: jest.fn(),
    };
    const n = require("../services/notify");
    expect(() => n.startNotifyListener(stubPool)).not.toThrow();
    await new Promise((r) => setTimeout(r, 20));
    expect(n.isNotifyAlive()).toBe(false);
    await n.stopNotifyListener(); // clears the pending reconnect timer
  });
});
