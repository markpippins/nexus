/**
 * Regression guard for issue #75 and the defect class it belongs to.
 *
 * Issue #75: `check-inbox.sh` sent `tags` (plural) where the tool schema
 * declared `tag` (singular). Zod strips unknown keys silently, so the
 * `to:<role>` filter never reached the DB and the query fell back to the
 * author `role` field — returning records *authored by* the role instead of
 * records *addressed to* it. PR #76 fixed that one key.
 *
 * PR #76 fixed the instance, not the class. The same tool still declared
 * `subsystemId`, `featureId`, and `search` in its schema while never
 * forwarding them to `NebulaClient.listAgentRecords` — the client supported
 * all three and built query params for them, so they were silently dropped one
 * layer up. A caller passing `search:` got a successful, unfiltered response.
 *
 * The first test below is the durable fix: it asserts that EVERY key declared
 * in the schema is actually forwarded to the client, so the next dropped
 * filter fails here instead of in someone's inbox.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { registerTools } from "../tools/index.js";
import { NebulaClient } from "../api/nebulaClient.js";

vi.mock("../api/nebulaClient.js", () => ({
  NebulaClient: {
    listAgentRecords: vi.fn(async () => ({ records: [], count: 0 })),
  },
}));

type Handler = (args: Record<string, unknown>) => Promise<unknown>;

/** Register every tool against a stub server and index them by name. */
function captureTools() {
  const captured = new Map<string, { schema: Record<string, unknown>; handler: Handler }>();
  const stub = {
    tool(name: string, _desc: string, schema: Record<string, unknown>, handler: Handler) {
      captured.set(name, { schema, handler });
      return stub;
    },
  };
  // The stub only needs to satisfy the McpServer surface registerTools touches.
  registerTools(stub as never);
  return captured;
}

const listArgs = (mock: unknown) =>
  (mock as { mock: { calls: [Record<string, unknown>][] } }).mock.calls[0][0];

describe("nebula_list_agent_records filter forwarding", () => {
  beforeEach(() => {
    vi.mocked(NebulaClient.listAgentRecords).mockClear();
  });

  it("registers the tool", () => {
    expect(captureTools().has("nebula_list_agent_records")).toBe(true);
  });

  /**
   * THE STRUCTURAL GUARD.
   *
   * Every schema-declared key must reach the client. `tags` is the one
   * deliberate exception: it is a documented alias that the handler collapses
   * into `tag` (issue #75), so it is legitimately absent from the forwarded
   * object while its *value* is still delivered.
   */
  it("forwards every declared schema key to NebulaClient (aliases excepted)", async () => {
    const tool = captureTools().get("nebula_list_agent_records")!;
    const declared = Object.keys(tool.schema);
    const aliases = new Set(["tags"]);

    // A distinct sentinel per key, so a key forwarded as `undefined` is
    // distinguishable from one forwarded with the wrong value.
    const args: Record<string, unknown> = {};
    for (const key of declared) args[key] = `SENTINEL_${key}`;

    await tool.handler(args);

    const forwarded = listArgs(NebulaClient.listAgentRecords);
    const missing = declared.filter((k) => !aliases.has(k) && forwarded[k] === undefined);
    expect(missing, `declared in schema but never forwarded: ${missing.join(", ")}`).toEqual([]);
  });

  it("forwards subsystemId, featureId, and search (the three dropped filters)", async () => {
    const tool = captureTools().get("nebula_list_agent_records")!;

    await tool.handler({ subsystemId: "sub-1", featureId: "feat-1", search: "hello world" });

    const forwarded = listArgs(NebulaClient.listAgentRecords);
    expect(forwarded.subsystemId).toBe("sub-1");
    expect(forwarded.featureId).toBe("feat-1");
    expect(forwarded.search).toBe("hello world");
  });

  it("keeps the issue #75 alias working: tags collapses into tag", async () => {
    const tool = captureTools().get("nebula_list_agent_records")!;

    await tool.handler({ tags: ["to:engineer"] });

    const forwarded = listArgs(NebulaClient.listAgentRecords);
    expect(forwarded.tag).toEqual(["to:engineer"]);
  });

  it("prefers singular tag when both are supplied", async () => {
    const tool = captureTools().get("nebula_list_agent_records")!;

    await tool.handler({ tag: ["to:architect"], tags: ["to:engineer"] });

    const forwarded = listArgs(NebulaClient.listAgentRecords);
    expect(forwarded.tag).toEqual(["to:architect"]);
  });

  it("does not send role together with tag (they would intersect to zero)", async () => {
    // check-inbox.sh deliberately omits `role` on the tagged path: records
    // addressed to a role are authored by OTHER roles, so role+tag yields
    // nothing. This locks that contract so a future edit cannot silently
    // re-add `role` and zero out every inbox.
    const tool = captureTools().get("nebula_list_agent_records")!;

    await tool.handler({ tag: ["to:engineer"] });

    const forwarded = listArgs(NebulaClient.listAgentRecords);
    expect(forwarded.role).toBeUndefined();
  });
});
