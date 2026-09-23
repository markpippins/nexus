# nexus-core-aegis — JVM Aegis State-Machine Registry Port

> Port: **8092**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

JVM port of aegis-srv inside the nexus-core monolith (Spring, JdbcTemplate over the aegis schema): TLA+ state-machine registries, validation and model-check results, Wind compilations, and audited execution logs. Mirrors the typescript/aegis-srv route surface; aligned via TypeSpec.

**71 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/aegis/registries` | Registries (root CRUD) |
| POST | `/api/aegis/registries` |  |
| GET | `/api/aegis/registries/name/{name}` |  |
| DELETE | `/api/aegis/registries/{id}` |  |
| GET | `/api/aegis/registries/{id}` |  |
| PATCH | `/api/aegis/registries/{id}` |  |
| GET | `/api/aegis/registries/{id}/attribute-mappings` | attribute-mappings |
| POST | `/api/aegis/registries/{id}/attribute-mappings` |  |
| DELETE | `/api/aegis/registries/{id}/attribute-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/attribute-mappings/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/attribute-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/concept-mappings` | concept-mappings |
| POST | `/api/aegis/registries/{id}/concept-mappings` |  |
| DELETE | `/api/aegis/registries/{id}/concept-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/concept-mappings/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/concept-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/constants` | constants |
| POST | `/api/aegis/registries/{id}/constants` |  |
| DELETE | `/api/aegis/registries/{id}/constants/{cid}` |  |
| GET | `/api/aegis/registries/{id}/constants/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/constants/{cid}` |  |
| GET | `/api/aegis/registries/{id}/execution-log` | execution-log |
| POST | `/api/aegis/registries/{id}/execution-log` |  |
| DELETE | `/api/aegis/registries/{id}/execution-log/{cid}` |  |
| GET | `/api/aegis/registries/{id}/execution-log/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/execution-log/{cid}` |  |
| GET | `/api/aegis/registries/{id}/invariants` | invariants |
| POST | `/api/aegis/registries/{id}/invariants` |  |
| DELETE | `/api/aegis/registries/{id}/invariants/{cid}` |  |
| GET | `/api/aegis/registries/{id}/invariants/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/invariants/{cid}` |  |
| POST | `/api/aegis/registries/{id}/model-check` | Model-check |
| GET | `/api/aegis/registries/{id}/model-check-results` |  |
| GET | `/api/aegis/registries/{id}/properties` | properties |
| POST | `/api/aegis/registries/{id}/properties` |  |
| DELETE | `/api/aegis/registries/{id}/properties/{cid}` |  |
| GET | `/api/aegis/registries/{id}/properties/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/properties/{cid}` |  |
| GET | `/api/aegis/registries/{id}/relationship-mappings` | relationship-mappings |
| POST | `/api/aegis/registries/{id}/relationship-mappings` |  |
| DELETE | `/api/aegis/registries/{id}/relationship-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/relationship-mappings/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/relationship-mappings/{cid}` |  |
| GET | `/api/aegis/registries/{id}/revisions` | Immutable registry revisions (Phase A) |
| POST | `/api/aegis/registries/{id}/revisions` |  |
| GET | `/api/aegis/registries/{id}/revisions/{rid}` |  |
| GET | `/api/aegis/registries/{id}/states` | states |
| POST | `/api/aegis/registries/{id}/states` |  |
| DELETE | `/api/aegis/registries/{id}/states/{cid}` |  |
| GET | `/api/aegis/registries/{id}/states/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/states/{cid}` |  |
| GET | `/api/aegis/registries/{id}/temporal-properties` | temporal-properties |
| POST | `/api/aegis/registries/{id}/temporal-properties` |  |
| DELETE | `/api/aegis/registries/{id}/temporal-properties/{cid}` |  |
| GET | `/api/aegis/registries/{id}/temporal-properties/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/temporal-properties/{cid}` |  |
| GET | `/api/aegis/registries/{id}/transitions` | transitions |
| POST | `/api/aegis/registries/{id}/transitions` |  |
| DELETE | `/api/aegis/registries/{id}/transitions/{cid}` |  |
| GET | `/api/aegis/registries/{id}/transitions/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/transitions/{cid}` |  |
| POST | `/api/aegis/registries/{id}/validate` | Validate |
| GET | `/api/aegis/registries/{id}/validation-results` | Results listings |
| GET | `/api/aegis/registries/{id}/variables` | variables |
| POST | `/api/aegis/registries/{id}/variables` |  |
| DELETE | `/api/aegis/registries/{id}/variables/{cid}` |  |
| GET | `/api/aegis/registries/{id}/variables/{cid}` |  |
| PATCH | `/api/aegis/registries/{id}/variables/{cid}` |  |
| GET | `/api/aegis/registries/{id}/wind-compilations` | Wind compilations |
| POST | `/api/aegis/registries/{id}/wind-compilations` |  |
| GET | `/api/aegis/registries/{id}/wind-compilations/{cid}` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->


