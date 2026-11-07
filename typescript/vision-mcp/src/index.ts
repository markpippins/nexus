import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerTools } from "./tools/index.js";

async function main() {
  const server = new McpServer({
    name: "vision-mcp",
    version: "1.0.0",
  });

  registerTools(server);

  const transport = new StdioServerTransport();
  await server.connect(transport);

  const visionSrvUrl = process.env.VISION_SRV_URL ?? "http://localhost:8006";
  console.error(`vision-mcp running on stdio (proxies to losm-host at ${visionSrvUrl})`);
}

main().catch((err) => {
  console.error("Fatal error starting vision-mcp:", err);
  process.exit(1);
});
