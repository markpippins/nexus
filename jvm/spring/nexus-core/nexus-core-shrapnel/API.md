# nexus-core-shrapnel — JVM Shrapnel EAV Object Store Port

> Port: **8092**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

JVM port of shrapnel inside the nexus-core monolith (Spring, JdbcTemplate over the shrapnel schema): encode/decode EAV objects, fields, field types, stereotypes. Routes mounted at /api/shrapnel/*; mirrors the typescript/shrapnel surface; contract-first via typespec/v1/shrapnel.

**19 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/shrapnel/encode` | encode (routes/encode.js) |
| GET | `/api/shrapnel/field-types` | field types (routes/field-types.js) |
| GET | `/api/shrapnel/field-types/{code}` |  |
| GET | `/api/shrapnel/fields` | fields (routes/fields.js) |
| POST | `/api/shrapnel/fields` |  |
| GET | `/api/shrapnel/fields/{id}` |  |
| GET | `/api/shrapnel/health` | health (routes/health.js) |
| GET | `/api/shrapnel/objects` | objects (routes/objects.js) |
| POST | `/api/shrapnel/objects` |  |
| DELETE | `/api/shrapnel/objects/{id}` |  |
| GET | `/api/shrapnel/objects/{id}` |  |
| POST | `/api/shrapnel/objects/{id}/classify` |  |
| GET | `/api/shrapnel/objects/{id}/conformance` |  |
| GET | `/api/shrapnel/objects/{id}/values` |  |
| GET | `/api/shrapnel/stereotypes` | stereotypes (routes/stereotypes.js) |
| POST | `/api/shrapnel/stereotypes/revisions` |  |
| GET | `/api/shrapnel/stereotypes/{name}` |  |
| GET | `/api/shrapnel/stereotypes/{name}/chain` |  |
| GET | `/api/shrapnel/stereotypes/{name}/contract` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->




