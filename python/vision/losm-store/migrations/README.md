# losm-store migrations — HISTORICAL ARTIFACTS

> **Disposition (DBA, 2026-09-20, thread 63d4ddb9, under Option B ruling
> thread 402d8a0d / decision 2026-09-20T00:57Z):** the ci-bootstrap is the
> **single replayable source** for this store. These migration files are
> historical artifacts — they are **not expected to replay clean**, and no
> repair path is planned. Do not "fix" them piecemeal; the end state
> (Stage 6 of plan 8261650) demotes the legacy surfaces they build to views
> over `resolution.work_request`.

## Why they don't replay (documented, not repaired)

Three latent defects, all masked in live because live was built from the
ci-bootstrap final state — verified by scratch replay 001→015(patched)→016
(PR #369 standby verification: losm-store suite 14 passed / 0 failed):

| Defect | Migration | Symptom |
|---|---|---|
| **D1** | 001 | `work_requests_history.id` has NO generation default (bare `INTEGER NOT NULL`, composite PK) — any autoincrement insert dies with NULL id. Half of incident e772b969's write-path failure. |
| **D2** | 015 | `ALTER`s `parent_request_id` onto the history table but never refreshes 001's `vision.work_requests` write-surface view — fresh 001→015 replay fails at 015:202 before the DAG machinery runs. (`CREATE OR REPLACE` cannot fix it: view column structure change.) |
| **D3** | 015 | Recursive `dag_tree` CTE drops the `VARCHAR(36)` typmod on `path` → recursive-term type mismatch error. |

## If a replayable legacy chain is ever needed again

The proven patch snippets live in **closed PR #369** (Option-A standby:
`016_repair_work_requests_losm_standby.sql` carries D1 + the `_losm` half of
D2; its diff was verified by the scratch replay above). Start there.

## Canonical direction

WorkRequest canonicalizes into `resolution.work_request` (V186, plan 8261650
Stage 1); writers repoint at Stages 2–4; this store's surfaces demote to
views at Stage 6. New WR persistence code must target the canonical store —
not anything built by this chain.

REFS: ruling 402d8a0d · remediation 63d4ddb9 · incident e772b969 ·
consumer map e3850398 · closed PR #369 · plan 8261650.
