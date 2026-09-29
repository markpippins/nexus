/**
 * Behavioral guard for the `nebula_get_inbox` advance flag.
 *
 * Background: bash-denied roles (reviewer, analyst, critic) can READ their
 * inbox via `nebula_get_inbox` but could not advance their R17 pointer —
 * `check-inbox.sh --update-pointer` needs bash, and `nebula_set_inbox_pointer`
 * was invisible to them. This makes the advance an opt-in flag on the read
 * tool itself, mirroring check-inbox.sh --update-pointer semantics:
 *   - advance: true + records  → pointer moves to newest record's createdAt
 *   - advance: true + no records → no-op (nothing new seen, nothing to mark)
 *   - advance omitted/false    → pure read, pointer untouched
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { registerTools } from "../tools/index.js";
import { NebulaClient } from "../api/nebulaClient.js";

const record = (id: string, createdAt: number) => ({
  id,
  createdAt,
  title: `Record ${id}`,
  tags: ["to:reviewer", "type:status-update"],
});

vi.mock("../api/nebulaClient.js", () => ({
  NebulaClient: {
    getInboxPointer: vi.fn(async () => ({ role: "reviewer", pointer: "2026-01-01T00:00:00Z" })),
    listAgentRecords: vi.fn(async () => ({ items: [], count: 0 })),
    setInboxPointer: vi.fn(async () => ({ ok: true })),
  },
}));

type Handler = (args: Record<string, unknown>) => Promise<unknown>;

function captureTools() {
  const captured = new Map<string, { schema: Record<string, unknown>; handler: Handler }>();
  const stub = {
    tool(name: string, _desc: string, schema: Record<string, unknown>, handler: Handler) {
      captured.set(name, { schema, handler });
      return stub;
    },
  };
  registerTools(stub as never);
  return captured;
}

const payload = async (tool: { handler: Handler }, args: Record<string, unknown>) => {
  const result = (await tool.handler(args)) as { content: { type: string; text: string }[] };
  return JSON.parse(result.content[0].text) as {
    role: string;
    pointer: string | null;
    items: unknown[];
    count: number;
    advancedTo: string | null;
    advanceError: string | null;
  };
};

describe("nebula_get_inbox advance flag", () => {
  beforeEach(() => {
    vi.mocked(NebulaClient.getInboxPointer).mockClear();
    vi.mocked(NebulaClient.listAgentRecords).mockClear();
    vi.mocked(NebulaClient.setInboxPointer).mockClear();
  });

  it("registers the tool", () => {
    expect(captureTools().has("nebula_get_inbox")).toBe(true);
  });

  it("declares the optional advance flag in its schema", () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    expect(tool.schema.advance).toBeDefined();
  });

  it("advances the pointer to the newest returned record's createdAt (epoch ms → ISO)", async () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    // 2026-08-22T00:00:00Z = 1787356800000 (epoch ms)
    vi.mocked(NebulaClient.listAgentRecords).mockResolvedValueOnce({
      items: [record("a", 1787270400000), record("b", 1787356800000)],
      count: 2,
    });

    const out = await payload(tool, { role: "reviewer", advance: true });

    // Newest createdAt wins, converted to ISO for the pointer.
    expect(NebulaClient.setInboxPointer).toHaveBeenCalledTimes(1);
    expect(NebulaClient.setInboxPointer).toHaveBeenCalledWith("reviewer", "2026-08-22T00:00:00.000Z");
    expect(out.advancedTo).toBe("2026-08-22T00:00:00.000Z");
    expect(out.count).toBe(2);
  });

  it("is a no-op when advance is true but no records are returned", async () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    vi.mocked(NebulaClient.listAgentRecords).mockResolvedValueOnce({ items: [], count: 0 });

    const out = await payload(tool, { role: "reviewer", advance: true });

    expect(NebulaClient.setInboxPointer).not.toHaveBeenCalled();
    expect(out.advancedTo).toBeNull();
    expect(out.count).toBe(0);
  });

  it("does NOT advance the pointer when the flag is omitted (pure read)", async () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    vi.mocked(NebulaClient.listAgentRecords).mockResolvedValueOnce({
      items: [record("a", 1787270400000)],
      count: 1,
    });

    const out = await payload(tool, { role: "reviewer" });

    expect(NebulaClient.setInboxPointer).not.toHaveBeenCalled();
    expect(out.advancedTo).toBeNull();
    expect(out.count).toBe(1);
  });

  it("does NOT advance the pointer when advance is explicitly false", async () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    vi.mocked(NebulaClient.listAgentRecords).mockResolvedValueOnce({
      items: [record("a", 1787270400000)],
      count: 1,
    });

    await payload(tool, { role: "reviewer", advance: false });

    expect(NebulaClient.setInboxPointer).not.toHaveBeenCalled();
  });

  it("still returns the read results even when the pointer write fails", async () => {
    const tool = captureTools().get("nebula_get_inbox")!;
    vi.mocked(NebulaClient.listAgentRecords).mockResolvedValueOnce({
      items: [record("a", 1787270400000)],
      count: 1,
    });
    vi.mocked(NebulaClient.setInboxPointer).mockRejectedValueOnce(new Error("peer reset"));

    const out = await payload(tool, { role: "reviewer", advance: true });

    expect(out.count).toBe(1);
    expect(out.advancedTo).toBeNull();
    expect(out.advanceError).toBe("peer reset");
  });
});