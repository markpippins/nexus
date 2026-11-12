import { beforeEach, describe, expect, it, vi } from "vitest";
import { registerTools } from "../tools/index.js";
import { NebulaClient } from "../api/nebulaClient.js";

vi.mock("../api/nebulaClient.js", () => ({
  NebulaClient: {
    createAgentRecord: vi.fn(async (body) => ({ id: "record-1", ...body })),
  },
}));

type Handler = (args: Record<string, unknown>) => Promise<unknown>;

function getCreateRecordHandler(): Handler {
  let found: Handler | undefined;
  const stub = {
    tool(name: string, _description: string, _schema: Record<string, unknown>, handler: Handler) {
      if (name === "nebula_create_agent_record") found = handler;
      return stub;
    },
  };
  registerTools(stub as never);
  if (!found) throw new Error("nebula_create_agent_record was not registered");
  return found;
}

describe("nebula_create_agent_record analysis body guard", () => {
  beforeEach(() => vi.mocked(NebulaClient.createAgentRecord).mockClear());

  it.each([undefined, "", " \n\t "])("rejects bodyless analysis content=%s before REST", async (content) => {
    const result = await getCreateRecordHandler()({
      recordType: "analysis",
      role: "analyst",
      title: "Reconstruction request",
      content,
    }) as { isError?: boolean; content?: Array<{ text?: string }> };

    expect(result.isError).toBe(true);
    expect(result.content?.[0]?.text).toContain("analysis records require non-blank content");
    expect(NebulaClient.createAgentRecord).not.toHaveBeenCalled();
  });

  it("forwards analysis with a non-blank body", async () => {
    await getCreateRecordHandler()({
      recordType: "analysis",
      role: "analyst",
      title: "Reconstructed analysis",
      content: "## Findings\nActual content.",
    });

    expect(NebulaClient.createAgentRecord).toHaveBeenCalledWith(expect.objectContaining({
      recordType: "analysis",
      content: "## Findings\nActual content.",
    }));
  });

  it("keeps title-only reports valid", async () => {
    await getCreateRecordHandler()({
      recordType: "report",
      role: "engineer",
      title: "Status projection",
    });

    expect(NebulaClient.createAgentRecord).toHaveBeenCalledWith(expect.objectContaining({
      recordType: "report",
      title: "Status projection",
      content: undefined,
    }));
  });
});
