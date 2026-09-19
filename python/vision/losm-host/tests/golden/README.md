# Golden captures — vision-srv (:8003) wire baseline

Reference evidence for the vision-srv → losm-host consolidation
(architect decision `5d8e10fd`). Captured live from titanium on 2026-09-19.

| File | What it pins |
|---|---|
| `vision-srv-openapi.json` | Full OpenAPI 3.1 of vision-srv as deployed — the compat source of truth |
| `losm-host-openapi.json` | OpenAPI of losm-host **before** the absorb (pre-consolidation state) |
| `golden_wr_list_200.json` | `GET /api/work-requests` with an empty store (`[]`) — pre-incident baseline |
| `golden_branches_200.json` | `GET /api/branches` empty (`[]`) |
| `golden_artifacts_200.json` | `GET /api/artifacts` empty (`[]`) |
| `golden_wr_404.json` | `{"detail":"Work request not found"}` — the 404 envelope to preserve |
| `golden_health_200.json` | vision-srv `/health` = `{"status":"ok"}` |
| `golden_branch_row_shape.json` | Real branch row from PG (before cleanup) — key order/type reference |

## Why the row-level captures are mostly empty

During capture, the live WR create path returned 500: the CI-bootstrap
(`sql/ci-bootstrap/nexus-ci-bootstrap.sql`) re-created
`vision.work_requests_losm` as a **non-updatable CASE view** over
`work_requests_history`, so SQLAlchemy inserts fail with
`FeatureNotSupported`. Routed as an incident (separate record); the tests
therefore pin shapes via the sealed SQLite store + the shared
`losm_store` models both services use — which guarantees identical
serialization by construction (the exact key set is asserted in
`tests/test_compat_surface.py::WR_ROW_KEYS`).

A live re-capture after the store fix can diff against
`vision-srv-openapi.json` and the asserted key set.
