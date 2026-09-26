# harness-srv — Generic Execution Harness

> **Port:** 3420
> **Base URL:** `http://localhost:3420`
> **Health:** `GET http://localhost:3420/health`
> **Docs:** [`API.md`](./API.md) (endpoint inventory) · [`openapi.yaml`](./openapi.yaml) (OpenAPI 3.0)

Generic execution harness. Merges **Tackle role context** (prompt + tool ACL +
procedure cards) with **Wind task context** (inputs + acceptance criteria) and
invokes an agent via the configured harness.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/run` | Resolve context + execute agent |
| POST | `/resolve-context` | Resolve context only (dry run) |
| GET | `/health` | Health check |

Full inventory: [`API.md`](./API.md) · machine-readable: [`openapi.yaml`](./openapi.yaml).

## Rate limiting

An app-level limiter caps requests at **300 req/min/IP** and advertises the
draft-7 `RateLimit` / `RateLimit-Policy` headers on every response. Exceeding
the ceiling returns `429` with a JSON body. This is a CodeQL
`js/missing-rate-limiting` remediation — the ceiling is well above normal
operator/agent polling, so it only bites on resource-exhaustion floods.

## `timeout_ms` clamping

A caller-supplied `timeout_ms` is **clamped into `[1s, 2h]`** before it reaches
any timer, on both the `/run` and `/exec` routes and again inside the executor
(defense in depth, so a future call site cannot forget the handler clamp).

| Input | Effective |
|---|---|
| `0`, `null`, `NaN`, non-numeric, absent | route default (300s on `/run`, 600s on `/exec`) — `Number(x) \|\| default` treats these as "not supplied" |
| `1` … `999` | `1000` (1s floor) |
| `1000` … `7200000` | passed through unchanged |
| `> 7200000` | `7200000` (2h ceiling) |

The 1s floor matters: run supervision uses a fixed 1s `setInterval` that compares
`Date.now()` against a deadline, so a near-zero duration would fire the
supervisor on the very first tick and kill a run that never had a chance to
start. A fixed 1s tick also means no timer is ever handed a user-controlled
duration.

## `work_dir` containment

A caller-supplied `work_dir` becomes the spawn cwd and must resolve under an
allowed root. By default that is the service work root (`HARNESS_WORK_DIR`).
Operators can add more roots with a colon-separated
`HARNESS_EXTRA_WORK_ROOTS` — e.g. `HARNESS_EXTRA_WORK_ROOTS=/srv/fleet:/mnt/jobs`.
It is wired in `systemd-user/harness-srv.service` and
`docker/legacy-tier/docker-compose.yml`; leave it empty to keep the default
single-root containment. This is a CodeQL `js/path-injection` remediation.

## Tests

```bash
npm test        # all suites (tsx-driven, dependency-free)
npm run typecheck
```

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json \
  && python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json
```
