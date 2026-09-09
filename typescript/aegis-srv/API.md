# aegis-srv — Aegis State-Machine Registry API

> Port: **3116**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

REST API for the aegis schema: TLA+ state-machine registries (constants, variables, states, transitions, invariants, properties, temporal properties, resolution-schema mappings), validation and model-check results, and audited execution logs.

**71 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/registries` |  |
| POST | `/api/registries` |  |
| DELETE | `/api/registries/:id` | Soft delete: is_active = false (schema partial unique index on active name) |
| GET | `/api/registries/:id` |  |
| PATCH | `/api/registries/:id` |  |
| GET | `/api/registries/:id/attribute-mappings` | attribute-mappings |
| POST | `/api/registries/:id/attribute-mappings` |  |
| DELETE | `/api/registries/:id/attribute-mappings/:cid` |  |
| GET | `/api/registries/:id/attribute-mappings/:cid` |  |
| PATCH | `/api/registries/:id/attribute-mappings/:cid` |  |
| GET | `/api/registries/:id/concept-mappings` | concept-mappings |
| POST | `/api/registries/:id/concept-mappings` |  |
| DELETE | `/api/registries/:id/concept-mappings/:cid` |  |
| GET | `/api/registries/:id/concept-mappings/:cid` |  |
| PATCH | `/api/registries/:id/concept-mappings/:cid` |  |
| GET | `/api/registries/:id/constants` | constants |
| POST | `/api/registries/:id/constants` |  |
| DELETE | `/api/registries/:id/constants/:cid` |  |
| GET | `/api/registries/:id/constants/:cid` |  |
| PATCH | `/api/registries/:id/constants/:cid` |  |
| GET | `/api/registries/:id/execution-log` | execution-log |
| POST | `/api/registries/:id/execution-log` |  |
| DELETE | `/api/registries/:id/execution-log/:cid` |  |
| GET | `/api/registries/:id/execution-log/:cid` |  |
| PATCH | `/api/registries/:id/execution-log/:cid` |  |
| GET | `/api/registries/:id/invariants` | invariants |
| POST | `/api/registries/:id/invariants` |  |
| DELETE | `/api/registries/:id/invariants/:cid` |  |
| GET | `/api/registries/:id/invariants/:cid` |  |
| PATCH | `/api/registries/:id/invariants/:cid` |  |
| POST | `/api/registries/:id/model-check` | the TLA+ module with invariants/properties as cfg checks. - Otherwise fall back to the deterministic structural state-space checker (model-checker.ts) over the structured aegis graph. Persists the result to aegis.model_check_result. TLC is failure-isolated: a checker crash/timeout yields an `error`  |
| GET | `/api/registries/:id/properties` | properties |
| POST | `/api/registries/:id/properties` |  |
| DELETE | `/api/registries/:id/properties/:cid` |  |
| GET | `/api/registries/:id/properties/:cid` |  |
| PATCH | `/api/registries/:id/properties/:cid` |  |
| GET | `/api/registries/:id/relationship-mappings` | relationship-mappings |
| POST | `/api/registries/:id/relationship-mappings` |  |
| DELETE | `/api/registries/:id/relationship-mappings/:cid` |  |
| GET | `/api/registries/:id/relationship-mappings/:cid` |  |
| PATCH | `/api/registries/:id/relationship-mappings/:cid` |  |
| GET | `/api/registries/:id/revisions` | Immutable registry revisions (Phase A) |
| POST | `/api/registries/:id/revisions` |  |
| GET | `/api/registries/:id/revisions/:rid` |  |
| GET | `/api/registries/:id/states` | states |
| POST | `/api/registries/:id/states` |  |
| DELETE | `/api/registries/:id/states/:cid` |  |
| GET | `/api/registries/:id/states/:cid` |  |
| PATCH | `/api/registries/:id/states/:cid` |  |
| GET | `/api/registries/:id/temporal-properties` | temporal-properties |
| POST | `/api/registries/:id/temporal-properties` |  |
| DELETE | `/api/registries/:id/temporal-properties/:cid` |  |
| GET | `/api/registries/:id/temporal-properties/:cid` |  |
| PATCH | `/api/registries/:id/temporal-properties/:cid` |  |
| GET | `/api/registries/:id/transitions` | transitions |
| POST | `/api/registries/:id/transitions` |  |
| DELETE | `/api/registries/:id/transitions/:cid` |  |
| GET | `/api/registries/:id/transitions/:cid` |  |
| PATCH | `/api/registries/:id/transitions/:cid` |  |
| POST | `/api/registries/:id/validate` | Action endpoints |
| GET | `/api/registries/:id/validation-results` |  |
| GET | `/api/registries/:id/variables` | variables |
| POST | `/api/registries/:id/variables` |  |
| DELETE | `/api/registries/:id/variables/:cid` |  |
| GET | `/api/registries/:id/variables/:cid` |  |
| PATCH | `/api/registries/:id/variables/:cid` |  |
| GET | `/api/registries/:id/wind-compilations` | Aegis revision -> Wind compilation |
| POST | `/api/registries/:id/wind-compilations` |  |
| GET | `/api/registries/:id/wind-compilations/:cid` |  |
| GET | `/api/registries/name/:name` |  |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->
