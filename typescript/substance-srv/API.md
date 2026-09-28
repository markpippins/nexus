# substance-srv — Segment Sets API

> Port: **3115**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Segment sets over transcript chunks: reusable, possibly non-contiguous collections of nebula.segments_history rows that candidates and requirements reference instead of copying source text forward. Reads are cached in Redis; writes invalidate rather than write through.

**11 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/:domain_type/:domain_id/segment-sets` |  |
| POST | `/:domain_type/:domain_id/segment-sets` |  |
| DELETE | `/:domain_type/:domain_id/segment-sets/:segment_set_id` |  |
| GET | `/healthz` |  |
| GET | `/segment-sets` |  |
| POST | `/segment-sets` |  |
| GET | `/segment-sets/:segment_set_id` |  |
| PATCH | `/segment-sets/:segment_set_id` |  |
| POST | `/segment-sets/:segment_set_id/members` |  |
| DELETE | `/segment-sets/:segment_set_id/members/:segment_id` |  |
| POST | `/segment-sets/from-segments` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->

