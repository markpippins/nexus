# draft-srv — Draft Service Workspace / DB Workbench API

> Port: **3170**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Draft service workspace hosting new backend components pending promotion to dedicated services. Current tenant: DB Workbench API (multi-engine database browsing, query/DDL execution, schema listing, and connection testing) backing data-explorer-ui.

**6 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/db/databases` |  |
| GET | `/api/db/engines` | Engine capability catalog (NEW, additive — lets the UI grey out not-yet-enabled engines instead of failing opaquely). |
| POST | `/api/db/query` |  |
| POST | `/api/db/schemas` |  |
| POST | `/api/db/test-connection` |  |
| GET | `/api/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->

