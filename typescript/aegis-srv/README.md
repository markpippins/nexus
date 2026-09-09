# aegis-srv

Express REST API server for the `aegis` PostgreSQL schema — a **TLA+ state-machine
registry** that bridges formal methods to the `resolution` schema.

## Contract-first baseline

The HTTP contract is defined in TypeSpec at
`typespec/v1/aegis-srv/typescript/` (namespace `org.nexus.aegissrv`). This is the
**single source of truth** for the route surface and is the baseline to migrate
this server to **Moleculer**. The generated OpenAPI document lives at
`typespec/v1/aegis-srv/generated/schema/openapi.yaml`.

To compile / emit the contract:

```bash
cd typespec/v1
npx tsp compile aegis-srv/typescript/main.tsp --emit @typespec/openapi3 --output-dir ./aegis-srv/generated
```

## Schema

The `aegis` schema (see `schemas/aegis.sql`, domain models in `python/aegis/aegis.py`)
models formal state machines:

- **`registry`** — the root of a TLA+ module definition (name, version, source, tags, active flag).
- **TLA+ components**: `constant`, `variable`, `state`, `transition`, `invariant`,
  `property` (safety/liveness/fairness), `temporal_property`.
- **Resolution bridge**: `concept_mapping`, `attribute_mapping`, `relationship_mapping`.
- **Outcomes / audit**: `model_check_result`, `validation_result`, `execution_log`.

All child tables cascade from `registry.id` via `registry_id`.

## Routes

All routes are mounted under `/api`. Auth posture: **LAN-bound, no authentication**
(repo convention, per audit 8bfe6519).

- `GET/POST /api/registries`, `GET/PATCH/DELETE /api/registries/{id}`, `GET /api/registries/name/{name}`
- Child resources (CRUD): `/api/registries/{id}/constants|variables|states|transitions|invariants|properties|temporal-properties|concept-mappings|attribute-mappings|relationship-mappings`
- Actions: `POST /api/registries/{id}/validate`, `POST /api/registries/{id}/model-check`, `GET/POST /api/registries/{id}/wind-compilations`, `GET /api/registries/{id}/wind-compilations/{cid}`
- Read-only listings: `GET /api/registries/{id}/validation-results`, `.../model-check-results`
- `GET/POST /api/registries/{id}/execution-log`
- `GET /health`

`DELETE /api/registries/{id}` is a **soft delete** (`is_active = false`); the schema's
partial unique index on `(name) WHERE is_active = true` permits re-using an active name.

## Wind compilation

`POST /api/registries/{id}/wind-compilations` compiles a pinned immutable registry revision into a new Wind workflow version. The target workflow and revision-scoped task/outcome mappings must already exist. The compiler validates all mappings before creating any Wind or Aegis rows, then writes deterministic nodes, edges, graph digest, and immutable lineage. It never activates the version, runs a task, mutates Resolution/PEB, or treats a compilation as admission authority.

## Model-checking

`POST /api/registries/{id}/model-check` runs the **authoritative** model check:

- **Real TLC** (the TLA+ model checker, `tla2tools.jar` vendored at
  `tla/tla2tools.jar`) when the registry has a `tla_plus_source`. The module is
  staged to a temp dir with a generated `.cfg` (registry invariants →
  `INVARIANT`, properties → `PROPERTY`, plus `INIT`/`NEXT`), TLC runs with a
  30s timeout, and its `-tool` output (exit codes + `@!@!@STARTMSG` protocol) is
  parsed into a status and counterexample trace. Status: `success` (exit 0),
  `failure` (exit 11 deadlock / 12 invariant-or-property violation, with the
  counterexample trace), `error` (parse error 150, timeout, or missing jar).
  TLC failures are isolated — they never fail the HTTP request.
- **Structural fallback** when there is no `tla_plus_source`: a deterministic
  state-space check over the structured aegis graph (reachability, deadlock,
  invariant/property/temporal verdicts) in `src/model-checker.ts`.

The result row's `checked_properties` records `engine:tlc` or `engine:structural`
so callers can distinguish the engine. Requires `java` on the PATH.

Wind workflow versions, nodes, and edges referenced by `aegis.wind_compilation` are protected by V150 from update/delete mutation. Uncompiled Wind design-time artifacts retain their existing lifecycle.

## Develop

```bash
npm install
npm run dev      # tsx watch src/index.ts
npm run build    # tsc -> dist/
npm start        # node dist/index.js
```

Port: `3116` (override with `AEGIS_SRV_PORT`). Database connection uses the repo
`nexus` database with the same env defaults as nebula-srv
(`PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB_NAME`), search_path `aegis`.