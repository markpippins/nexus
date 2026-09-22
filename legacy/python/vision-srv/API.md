# vision-srv — LOSM REST API

> Port: **8003**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

FastAPI backend for the LOSM (Layered Operational State Machine): work requests, branches, artifacts, and DAG compilation/validation.

**13 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

> OpenAPI spec captured live from the service's /openapi.json (FastAPI-native, schema-complete); the table below is the source-route inventory.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/artifacts` |  |
| POST | `/api/artifacts` |  |
| GET | `/api/branches` |  |
| POST | `/api/branches` |  |
| GET | `/api/work-requests` |  |
| POST | `/api/work-requests` |  |
| DELETE | `/api/work-requests/{wr_id}` |  |
| GET | `/api/work-requests/{wr_id}` |  |
| PATCH | `/api/work-requests/{wr_id}` |  |
| GET | `/api/work-requests/{wr_id}/dag` |  |
| GET | `/api/work-requests/{wr_id}/dag/path/{target_wr_id}` |  |
| GET | `/api/work-requests/{wr_id}/dag/validate` |  |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->







---

# vision-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:8003`. FastAPI service (Pydantic-typed). **The committed
> [`openapi.yaml`](./openapi.yaml) is captured live from `/openapi.json` and is
> the authoritative, schema-complete spec** — every request/response body below
> is defined there with full Pydantic schemas. This section summarizes the
> resource envelopes for quick reference.

## Work request envelope (vision.work_requests)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/work-requests` | List work requests. |
| `POST /api/work-requests` | Create a work request (WRP identity + address + DCO). |
| `GET /api/work-requests/{wr_id}` | Single work request. |
| `PATCH /api/work-requests/{wr_id}` | Update (state transition / metadata). |
| `DELETE /api/work-requests/{wr_id}` | Delete. |
| `GET /api/work-requests/{wr_id}/dag` | The work request's state-DAG. |
| `GET /api/work-requests/{wr_id}/dag/path/{target_wr_id}` | DAG path between two work requests. |
| `GET /api/work-requests/{wr_id}/dag/validate` | DAG validation report. |

Core WR fields (see openapi.yaml for the full Pydantic models): `wr_id`,
`work_request_uuid`, `entity_key`, `state`, `address` (`cal://…`), `dco`
(domain-change-order: intent, identity, address, delta, compile receipt),
`context`, `timestamps`, `correlation_id`, `causation_id`.

## Branch envelope (vision.branches)

`GET /api/branches` — list branches · `POST /api/branches` — create a branch
(from a parent work request; the DAG lineage fork). Fields per openapi.yaml.

## Artifact envelope (vision.artifacts)

`GET /api/artifacts` — list artifacts · `POST /api/artifacts` — store an
artifact (IR/spec/execution outputs keyed to a work request). Fields per
openapi.yaml.

## Health

`GET /health` — process health.

## Notes

- This is the LOSM (Layered Operational State Machine) backend — the WRP
  authority for work-request identity, state-DAG, and address semantics.
- Use `openapi.yaml` for the exact field contracts; it is regenerated from the
  live service and is always current.

