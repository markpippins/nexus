# schemas/migrations/resolution — SOL resolution schema (canonical SQL home)

Canonical tracked home for the `resolution` PostgreSQL schema artifacts.

## Location / how to save

**Save resolution SQL dumps HERE** (not at `/home/codex/dev/` root):

```
nexus/schemas/migrations/resolution/
```

This is the tracked copy that gets applied to the `nexus` database's
`resolution` schema. Files kept here:

| File | What it is |
|---|---|
| `resolution_schema_consolidated.sql` | Base full schema dump (pg_dump); apply the post-dump v28-v33 chain below to reach the current live contract |
| `resolution_data_consolidated.sql` | Full data dump (pg_dump) — **current canonical seed data** |
| `resolution_migration_v{3,4,5,6,7,11,12,13,17,18,19,20,21,22,23,23b,24b,28,29,30,31,32,33,34,35,36,37,38,39}.sql` | Incremental migrations (v3 → v39) |
| `resolution_evaluate_proposition_v1.sql` | Proposition evaluation function (disposition fast path) |
| `resolution_comparator_v1.sql` | Cross-representation disagreement detection (`detect_disagreement`) |
| `resolution_authority_resolution_v1.sql` | Authority resolution via verified statement |
| `resolution_scheduler_v1.sql` | Reconciliation sweep (`run_reconciliation_sweep`) |
| `resolution_on_change_v2.sql` | Change-event re-evaluation hook (`on_change`) |
| `resolution_reopen_fix_v2.sql` | Relational-aware reopen of disputed propositions |
| `resolution_compiler_v{2,3,5,6,prototype}.sql` | SOL IR compiler functions (versions) |
| `resolution_peb_bridge_v{1,2}.sql` | PEB bridge schema |
| `resolution_oq_seed.sql`, `resolution_rollup_seed.sql` | Seed data sets |
| `resolution_schema_v2.sql`, `resolution.sql` | Earlier full-schema states |

## Apply procedure (refreshed consolidated files)

When the **consolidated** files are refreshed, apply them to the live DB:

```bash
export PGPASSWORD=pgpass
# 1. backup current (empty/old) schema
pg_dump -h localhost -U pguser -d nexus --schema=resolution --schema-only \
  -f /tmp/opencode/resolution_backup_$(date +%F).sql
# 2. full swap (only when no external deps — pre-check with pg_depend)
psql -h localhost -U pguser -d nexus -c "DROP SCHEMA resolution CASCADE;"
psql -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 \
  -f schemas/migrations/resolution/resolution_schema_consolidated.sql
psql -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 \
  -f schemas/migrations/resolution/resolution_data_consolidated.sql
# 3. apply the ordered post-dump chain through v37, including v31/v32/v33,
#    v34/v35, the v36 Shrapnel bridge, and the v37 field metadata sync
```

Notes:
- Dumps are pg_dump 16.14 `--no-owner` style; `\restrict`/`\unrestrict`
  markers are no-ops on local psql 17.10 (verified).
- The `resolution` schema is a SOL sandbox — "zero blast radius to
  production" per its own comment. No external schema depends on it.
- Last verified live chain: v28 execution claims + evidence vocabulary, v29 T24
  graph-edge evidence bridge, v30 verified execution admission bridge, v31 frame
  dimensions, v32 context-aware proposition evaluation, v33 unambiguous sweep
  overloads, v34 verified_statement immutability trigger (adopted from the
  /claude experimental branch), v35 frame semantics (meaning of frame
  dimensions as first-class proposition vocabulary), v36 read-only Shrapnel
  state bridge,   and v37 Shrapnel field metadata synchronization. V38 adds Lilac Wave 1
  provenance/topology; V39 is a staged guarded cleanup for the orphaned
  illustrative `escalates_to` expression (apply only after plan 8261673's
  Drafts/admin gate and explicit user approval). V28-V39 are

  incremental migrations on top of v24b. v37 requires the authoritative
  `shrapnel.field` and `resolution` schemas to coexist in the target database;
  apply it to each database independently only after its Resolution schema is
  present.
  The consolidated dump is a base recovery artifact
  and does not yet include the v31-v37 delta; apply the ordered migrations
  after restoring it, or regenerate the dump from the live catalog before
  using it as a full recovery source.

## claude/ — deviation branch

The `claude/` directory below this one is a deviation/experimental branch that
forked at the v30/v31 boundary. It does NOT carry authority — it is a
laboratory for candidate deltas:

- `resolution_migration_v33.sql` → `verified_statement_immutable()` trigger
  (adopted into canonical v34, 2026-08-24)
- `resolution_schema_consolidated.sql` and `resolution_data_consolidated.sql`
  diverge from canonical after v30 — NOT a baseline for migration

Rule: /claude content is reviewed and selectively adopted; it is never applied
as a schema snapshot. Diffs against canonical are interpreted as candidate
semantic deltas, not drift to be repaired.

## Related

- `schemas/migrations/tackle/` — role personas + procedure registry SQL (same convention)
- `vision` schema — separate (see `/home/codex/dev/vision.sql` upstream)
