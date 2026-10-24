# `nexus/sql/migrations/` — placement note

**There is no apply directory here.** The `sql` migration family lives
**flat** in `nexus/sql/`, and this directory exists only to record why.

## The rule

| Family | Location | Discovered by | Ledger |
|---|---|---|---|
| `sql` (V-series) | `nexus/sql/V###__name.sql` | manual/DBA apply | `resolution.migration_ledger`, `schema_name='sql'` |
| `nebula-srv` | `typescript/nebula-srv/migrations/NNN-name.sql` | `nebula-srv`'s own runner (`migrate.ts`, `FILE_RE = /^\d{3})-.*\.sql$/`) | `nebula.schema_version` |
| `shrapnel` | `typescript/shrapnel/migrations/NNN-name.sql` | its own runner | `shrapnel._migration_ledger` |

## Why V205 is at `nexus/sql/V205__w_a_duplicate_history_reconciliation.sql`

Plan **8261653** named `nexus/sql/migrations` as a target file path, but
that path does not exist in the repository and **no runner globs it**. A
migration placed here would never be picked up by an apply mechanism — it
would be a dead artifact, and (worse) a second copy of a migration is
precisely the "duplicate history" failure this plan exists to reconcile.

The evidence for the flat placement:

- `resolution.migration_ledger` carries exactly two `sql`-family rows —
  `V203__reconcile_dba_rover_role_rows` and
  `V204__assembly_user_disposition_dba_rover` — and both source files
  live flat at `nexus/sql/V203__*.sql` and `nexus/sql/V204__*.sql`.
- The V-series e2e test reads the migration flat:
  `python/nexus_core/wrp/tests/test_v198_st01_promotion_batch_e2e.py`
  resolves `_REPO_ROOT / "sql" / "V198__st01_promotion_batch_candidate_set_keys.sql"`.
- Every prior V-series file is flat: `nexus/sql/V038__…` through
  `nexus/sql/V204__…`.
- The retro-ledger companion for this family is also flat:
  `nexus/sql/migration_ledger_retro_V203_V204.sql`.

## Deviation, on the record

Placing V205 flat is a **deliberate, disclosed deviation** from plan
8261653's literal target-file list. It was made because a migration no
apply mechanism can reach is not a delivered migration.

If the Architect or the DBA prefers a `sql/migrations/` directory to become
the forward home of the V-series, that is a **repository-wide convention
change**, not a one-file move: the runner, the ledger writer, the
retro-ledger companion convention and the V198 e2e test would all have to
move together. That change is out of scope for plan 8261653 and belongs to
its own plan.

## Apply

```bash
psql -d nexus -f sql/V205__w_a_duplicate_history_reconciliation.sql
```

The migration creates **empty** ledger surfaces and refuses a double
apply. The dispositions themselves are applied by the DBA lane, from the
builder-side inventory in `python/nebula/` — see
`nexus/docs/promotion-flow/w-a-duplicate-history-reconciliation.md`.
