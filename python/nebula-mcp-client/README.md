# nebula-mcp-client

Canonical, dependency-free MCP **Streamable-HTTP JSON-RPC client** for the
Nexus platform (to-do `8e09a57f`, Gap 4). Replaces the per-session
hand-rolled SSE/handshake probe scripts that used to live in
`/home/codex/dev/tmp`.

## Why it exists

Every session previously re-wrote a small MCP client (SSE handshake,
session-id management, JSON-RPC framing) to query nebula-mcp. This module
is the shared, tested version.

## Install

```bash
pip install -e python/nebula-mcp-client
```

Without this the module is not importable — the directory name contains
hyphens, so it can never be picked up as a package, and the distribution ships
`nebula_mcp_client.py` as a top-level module. `AGENTS.md` R17 names the CLI
form below as the canonical inbox path for every role, so an environment that
has not run this step loses its inbox check by default.

## Usage

```python
from nebula_mcp_client import McpClient

# Sessionful server (nebula-mcp :3102) — auto-initializes:
nebula = McpClient("http://localhost:3102")
inbox = nebula.call("nebula_get_inbox", {"role": "engineer", "limit": 20})
print(inbox)

# Stateless server (tackle-mcp :3400) — no handshake needed:
tackle = McpClient("http://localhost:3400")
procs = tackle.call("memory_get_procedures", {"role": "engineer"})
```

## CLI

```bash
python -m nebula_mcp_client http://localhost:3102 list
python -m nebula_mcp_client http://localhost:3102 call nebula_get_inbox '{"role":"engineer"}'
```

## Transport notes

See `docs/mcp-transport-matrix.md` for the authoritative per-server
transport table. Only nebula-mcp (:3102) requires the `initialize`
handshake; the client handles both automatically.
