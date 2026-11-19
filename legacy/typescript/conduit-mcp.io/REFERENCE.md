# Conduit MCP — Reference Guide

## Configuration

| Property | Default | Description |
|----------|---------|-------------|
| `PORT` | 3100 | HTTP server port |
| `PIPELINE_DIR` | ../../nexus/.conduit-data | Root of conduit data directory |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 3100 | Server port |
| `PIPELINE_DIR` | ../../nexus/.conduit-data | Data directory root |
| `MCP_BASE_URL` | http://localhost:3100 | Server URL for self-referencing |

## Commands

| Command | Description |
|---------|-------------|
| `npx tsx src/index.ts` | Start the MCP server |
| `npx tsx --watch src/index.ts` | Start with live reload |
| `curl http://localhost:3100/health` | Health check + orphan scan |
| `curl http://localhost:3100/state` | Pipeline state JSON |
| `curl -X POST -d '{"name":"create_proposed_plan","arguments":{"title":"Test"}}' http://localhost:3100/tools/call` | Create a proposed plan |

## Troubleshooting

- **Plan not appearing in UI**: Check that a receipt was issued — plans with NULL derived_status are invisible. Always use MCP tools, never write files directly.
- **Database migration errors**: The MCP server owns schema migrations. Ensure it has started at least once before the Python conduit runs.
- **SSE not connecting**: Check that the client is connecting to port 3100 and that CORS is configured correctly
- **Circuit breaker tripped**: Check /health for breaker state — the breaker auto-resets after the configured timeout
- **Directory not found**: Verify PIPELINE_DIR in .env points to the correct absolute or relative path
