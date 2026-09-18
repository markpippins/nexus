# pty-srv — WebSocket PTY Gateway

> **RETIRED (M3, 2026-09-18)** — superseded by `worker.pty-transport` (WS on
> :3130 via nexus-broker :4080). This source tree is **retained until the M3
> close-out review** as the wire-compat reference and protocol doc. Do not
> restart `pty-srv.service`; the unit and `:3120`/`:3121` listeners are gone.

> **Port:** 3120 (historical)
> **Endpoint:** `ws://localhost:3120` (historical — use `ws://localhost:3130`)
> **Shell:** `$SHELL` (default `/bin/bash`)

`pty-srv` is a **WebSocket-only** service: it exposes an interactive PTY
terminal gateway using `ws` + `node-pty`. There are **no REST routes** — the
API is the WebSocket upgrade surface. It does serve a minimal HTTP health
endpoint alongside the WS server.

## WebSocket protocol

Each connection spawns a fresh shell (cwd = `$HOME`, `TERM=xterm-256color`).

**Client → server** (JSON messages):

```json
{ "type": "input", "data": "<keystrokes>" }
{ "type": "resize", "cols": 120, "rows": 40 }
```

Raw (non-JSON) text messages are written to the shell as-is.

**Server → client:**

- raw terminal output (VT/xterm escape sequences) for the spawned shell
- the socket closes when the shell exits

## Health

The bundled HTTP server answers `GET /health` on the same port (HTTP, not WS)
with a JSON status payload.

## Documentation note

Because pty-srv exposes no REST routes, no `openapi.yaml`/`API.md` are
generated for it (the `nexus/tools/api-docs` generator skips it).
