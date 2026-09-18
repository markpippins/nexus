# nexus/tools/api-docs — *-srv REST-API + OpenAPI tooling

> **Runtime reflection (Wave 4 label):** this tooling observes live source —
> route registrations in the code — and projects them into committed
> `openapi.yaml` + `API.md` artifacts. It is a projection pipeline, not an
> authority: the routes in source are canonical, the docs are derived.

Keeps the REST-API documentation of every `*-srv` project under
`nexus/typescript/` and `nexus/python/` current with the actual route
registrations in source.

## What it produces

For each `*-srv` (in the service's own directory):

| Artifact | Content |
|----------|---------|
| `openapi.yaml` | OpenAPI 3.0.3 spec — every mounted path + method, path params, tags, generic `JsonBody`/`Error` schemas. `info.version` is a hash of the route inventory, so regeneration is deterministic. |
| `API.md` | Markdown REST-API reference — endpoint inventory table (Method \| Path \| Description) + OpenAPI link. |

### Special cases

- **vision-srv** (FastAPI): fetches the service's live `/openapi.json` and saves
  it (converted to YAML) — far richer than the generic spec. Falls back to the
  generic route-level spec if the service is down.
- **semantics-srv**: excluded — it already carries a registry-derived spec
  (`typescript/semantics-srv/scripts/generate-openapi.ts` + `openapi.yaml`).
- **terrain-srv**: excluded (retired). **pty-srv**: excluded (RETIRED M3) — no REST routes
  (WebSocket-only) — documented in its README only; superseded by `worker.pty-transport` :3130.

## Tools

| Script | Purpose |
|--------|---------|
| `extract_routes.py` | Parse route registrations from source → `/tmp/api_inventory.json`. |
| `gen_openapi.py` | Emit `openapi.yaml` + `API.md` per service from the inventory (`--only <svc>` regenerates a subset). |
| `check_drift.py` | **CI gate** — fail (exit 1) if any committed `openapi.yaml` no longer matches the source routes; `--update` regenerates only the drifted specs. |
| `serve_docs.py` | Single-port browsable index (`http://localhost:3180`): Swagger UI + ReDoc over all specs; optional HTTPS listener on `0.0.0.0:8443` via `--tls-cert`. |
| `pre-push` hook | Range-aware git hook — see `.githooks/pre-push` below. |

## Usage

```bash
cd nexus
# 1. extract the route inventory from source
python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
# 2. generate openapi.yaml + API.md for every service
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json
# 3. verify nothing drifted (exit 1 on drift; use --update to fix)
python3 tools/api-docs/check_drift.py [--update] [--quiet] [--json]
# 4. browse all specs on one port (Swagger UI + ReDoc)
python3 tools/api-docs/serve_docs.py --port 3180
```

## Systemd service (`apidocs-srv.service`)

The docs index runs as a persistent user-level systemd unit so it's always up
(including across reboots).

- **Tracked unit:** `tools/api-docs/apidocs-srv.service` (mirrors the sibling
  `*-srv.service` convention: `WorkingDirectory` = `nexus/`, `ExecStart` =
  `python3 tools/api-docs/serve_docs.py --port 3180`, `Restart=on-failure`).
- **Installed copy:** `~/.config/systemd/user/apidocs-srv.service` (installed
  with `cp` + `systemctl --user daemon-reload`).
- **Registered in** `bin/start-nexus-services.sh` (`ALL_SERVICES` + port 3180
  in `SERVICE_PORTS`), so `start|status|health|stop|restart` all cover it.

```bash
# manual management
systemctl --user status apidocs-srv.service
systemctl --user restart apidocs-srv.service
journalctl --user -u apidocs-srv.service -f
```

## HTTPS exposure (self-signed TLS on 8443)

The unit starts a second listener that serves the same index over TLS on
`0.0.0.0:8443` — reachable from any LAN client as `https://<titanium-ip>:8443`
— while plain HTTP stays on `127.0.0.1:3180` for localhost tooling (drift CI,
sysadmin health checks, local browsing).

- **Cert:** `tools/api-docs/certs/apidocs-selfsigned.{crt,key}` — auto-generated
  on startup by `serve_docs.py` (openssl, 10-year validity, SANs for
  `localhost`, the hostname, and the host's non-loopback IPv4 addresses). The
  directory is gitignored; delete the files and restart the unit to regenerate.
- **Trust:** the cert is self-signed, so browsers show a warning until you add
  it to the OS/browser trust store (`openssl x509 -in
  tools/api-docs/certs/apidocs-selfsigned.crt -outform PEM` and import it, or
  curl with `-k`).
- **CLI:** `serve_docs.py --tls-cert <cert> --tls-key <key> [--tls-host 0.0.0.0]
  [--tls-port 8443]`; TLS is opt-in (plain `--port 3180` runs stay HTTP-only).
- **Security note:** the index exposes every service's OpenAPI spec. TLS
  protects confidentiality in transit but adds no auth — anyone on the LAN can
  read the specs. The service-broker auth/token tooling is the route to gating
  it if specs must not be readable on the LAN.

## Git pre-push hook (`.githooks/pre-push`)

The repo's `pre-push` hook (tracked in `.githooks/`, active via
`git config core.hooksPath .githooks`) has two range-aware gates:

1. **Inline shared-utils sync check** — blocks pushes when the inlined
   `angular/*/src/utils` copies drift from `@nexus/shared-react`.
2. **API-docs regeneration gate** — when the pushed commits touch route-bearing
   service sources, `tools/api-docs/`, or any committed `openapi.yaml`/`API.md`:
   - regenerates the specs in your worktree (`check_drift.py --update`), and
   - **blocks the push** if that refresh produced changes, so the refreshed
     docs get committed and ship with the next push.

Only runs when the pushed range touches relevant paths (milliseconds
otherwise); unresolvable ranges fall back to running conservatively.

## How it works

- `extract_routes.py` parses Express route registrations
  (`app`/`router.get|post|put|patch|delete`) and follows `app.use('/prefix',
  target)` mounts through module imports: default-export routers
  (`export default router`), named router exports
  (`export const X = Router()`), and factory mounts
  (`app.use('/api', createRoutes(pool))` with `const router = Router()`
  inside the factory). FastAPI `@app.get(...)` decorators are also handled.
  `//` comments above a route become the endpoint description.
- `gen_openapi.py` turns the inventory into per-service OpenAPI + markdown.
  Request/response bodies are intentionally generic — field-level contracts
  live in the service code and its README.
- `check_drift.py` compares extractor (method, path) sets against each
  committed spec (both normalized to `{param}` form). `--update` regenerates
  only drifted services, then re-verifies.
