/**
 * Regression guard: inbox addressing must be case-insensitive on the QUERY.
 *
 * The defect: `to:<role>` tags are matched EXACTLY (nebula-srv filters with
 * `= ANY(tags)`), and the inbox pointer is a raw Redis key
 * `inbox:pointer:<role>`. Neither errors on a case variant — each silently
 * addresses a different, usually empty, mailbox.
 *
 * That is not hypothetical. Measured live on 2026-09-29:
 *   - 694 agent records tagged `to:dba`, 7 tagged `to:DBA`
 *   - two live Redis keys: `inbox:pointer:dba` (2026-09-29T00:26:26Z) and
 *     `inbox:pointer:DBA` (2026-09-23T15:03:28Z) — six days apart
 * so a session booted as `--role DBA` read the 7-record mailbox and never saw
 * the 694, and the two pointers advanced independently.
 *
 * This test pins the QUERY normalization. No stored record is rewritten, and
 * the uppercase display surfaces (`display_name`, Assembly `alias`) are
 * deliberately untouched.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { registerTools } from "../tools/index.js";
import { NebulaClient } from "../api/nebulaClient.js";

vi.mock("../api/nebulaClient.js", () => ({
  NebulaClient: {
    listAgentRecords: vi.fn(async () => ({ items: [], total: 0 })),
    getInboxPointer: vi.fn(async () => null),
    setInboxPointer: vi.fn(async () => ({ ok: true })),
  },
}));

type Handler = (args: Record<string, unknown>) => Promise<unknown>;

function captureTools() {
  const captured = new Map<string, Handler>();
  const stub = {
    tool(_name: string, _desc: string, _schema: unknown, handler: Handler) {
      return stub;
    },
  };
  // Re-walk with a real map so we can invoke handlers by name.
  const byName = new Map<string, Handler>();
  const server = {
    tool(name: string, _d: string, _s: unknown, handler: Handler) {
      byName.set(name, handler);
      return server;
    },
  };
  void captured; void stub;
  registerTools(server as never);
  return byName;
}

const listMock = () => NebulaClient.listAgentRecords as unknown as ReturnType<typeof vi.fn>;
const getPtrMock = () => NebulaClient.getInboxPointer as unknown as ReturnType<typeof vi.fn>;
const setPtrMock = () => NebulaClient.setInboxPointer as unknown as ReturnType<typeof vi.fn>;

describe("inbox role normalization", () => {
  beforeEach(() => vi.clearAllMocks());

  it("nebula_get_inbox queries the lowercase tag for an uppercase role", async () => {
    const tools = captureTools();
    await tools.get("nebula_get_inbox")!({ role: "DBA", limit: 5 });

    expect(listMock().mock.calls[0][0].tag).toEqual(["to:dba"]);
  });

  it("nebula_get_inbox reads the lowercase pointer key for an uppercase role", async () => {
    const tools = captureTools();
    await tools.get("nebula_get_inbox")!({ role: "DBA" });

    expect(getPtrMock().mock.calls[0][0]).toBe("dba");
  });

  it("pointer read and write agree, so the watermark actually advances", async () => {
    const tools = captureTools();
    await tools.get("nebula_get_inbox")!({ role: "DBA" });
    await tools.get("nebula_set_inbox_pointer")!({ role: "DBA", timestamp: "2026-09-29T00:00:00Z" });

    // A read/write mismatch would advance one key while the other is consulted,
    // so the inbox would replay forever.
    expect(getPtrMock().mock.calls[0][0]).toBe(setPtrMock().mock.calls[0][0]);
  });

  it("leaves an already-lowercase role untouched", async () => {
    const tools = captureTools();
    await tools.get("nebula_get_inbox")!({ role: "dba" });

    expect(listMock().mock.calls[0][0].tag).toEqual(["to:dba"]);
    expect(getPtrMock().mock.calls[0][0]).toBe("dba");
  });

  it("tolerates surrounding whitespace rather than inventing a third mailbox", async () => {
    const tools = captureTools();
    await tools.get("nebula_get_inbox")!({ role: " DBA " });

    expect(listMock().mock.calls[0][0].tag).toEqual(["to:dba"]);
  });

  it("does not alter the caller-visible role echoed back", async () => {
    const tools = captureTools();
    const res: any = await tools.get("nebula_get_inbox")!({ role: "DBA" });

    // The label stays as the operator typed it; only the query is normalized.
    expect(JSON.parse(res.content[0].text).role).toBe("DBA");
  });
});
