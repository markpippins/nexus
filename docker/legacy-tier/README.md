# Legacy Tier — dockerized replaced servers

**Decision D-2026-08-15:** the Express servers replaced by the consolidated
AdonisJS + Moleculer system are **dockerized, not decommissioned**. They
remain the live in-place services on the current machine and can be
re-deployed to another machine as a survivable legacy tier.

## What is here

Each replaced `typescript/*-srv` project now ships its own `Dockerfile`
(+ `.dockerignore`). This directory holds the orchestration:

| Unit | Image context | Port | Replaced by (wave) |
|------|---------------|------|---------------------|
| wind-srv | `../../typescript/wind-srv` | 3300 | nexus-control-edge (3.4) |
| tackle-srv | `../../typescript/tackle-srv` | 3410 | nexus-control-edge (3.5) |
| kernel-srv | `../../typescript/kernel-srv` | 8100 | nexus-control-edge (3.6) |
| peb-srv | `../../typescript/peb-srv` | 3111 | nexus-control-edge (3.7) |
| cascade-srv | `../../typescript/cascade-srv` | 3106 | nexus-control-edge (3.8) |
| harness-srv | `../../typescript/harness-srv` | 3420 | worker.harness (4) |
| ~~pty-srv~~ | `../../typescript/pty-srv` | ~~3120~~ | ~~worker.pty (4)~~ — **RETIRED (M3)**; superseded by `worker.pty-transport` :3130 via nexus-broker |
| execution-srv | `../../typescript/execution-srv` | 3110 | worker.execution (4) |
| tackle-prompt-sync-srv | `../../typescript/tackle-prompt-sync-srv` | 3501 | control-edge prompt-sync (1.1) |
| role-memory-srv | `../../typescript/role-memory-srv` | 3500 | control-edge role-memory (1.2) |

## Deploying to another machine

The legacy units connect to an existing PostgreSQL and Redis — they reuse the
schemas that already exist on those instances (they do not create or migrate
them). Point `.env` at your target PG/Redis.

```bash
cd docker/legacy-tier
cp .env.example .env      # edit PG/Redis endpoints + port mappings
docker compose up -d --build
```

Per-service build without compose:

```bash
docker build -t nexus-legacy/tackle ../.. -f ../../typescript/tackle-srv/Dockerfile
```

## Notes

- **TS units** build in a multi-stage image (`node:20-bookworm` builder →
  slim runtime). **JS units** (wind-srv, peb-srv) run directly with no build.
- ~~**pty-srv** keeps the full bookworm runtime because `node-pty` is a native
  module compiled at install time.~~ — **RETIRED (M3)**; the pty service block
  was removed from the compose; node-pty now runs in-process in `worker.pty`.
- **harness-srv** spawns agent subprocesses — mount `HARNESS_WORK_DIR`
  (volume `./work:/nexus` by default) and make `OPENCODE_BIN` available on
  the container. It also calls out to nebula (3101) and conduit-mcp (3100);
  in the legacy tier those endpoints are expected to be reachable by the
  names in `.env`.
- Services that point at DSNs assume the same `nexus` database and the same
  schemas (wind, tackle, conduit, peb, cascade, kernel, execution, memory)
  are present on the target PG. Replicate the schemas with the project's
  existing Flyway migrations before pointing the tier at a fresh instance.
