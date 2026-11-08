## Humanities | bin/drafts/ README

Quick orientation for anyone landing in this folder.

### What belongs here

- **Drafts reviewed and ready** (e.g. `071-role-inbox-pointers.sql`): candidate DDL or DML that has passed internal review and is waiting on the DBA to place and apply. These are numbered to match the migration ledger so placement order is obvious.
- **Drafts not yet vetted** (e.g. `migration-069/`, `DRAFT-068-fixed.sql`, `draft068-review-fixes.patch`): work in progress, a prior attempt that was sent back, or a review pack. Treat these as proposals, not ready-to-run.
- **Retryable carve-outs on hold** (e.g. `dba-backfill-session-id.sh`, `backfill_evidence_items.sql`): SQL or scripts that are self-contained but deliberately **not** applied as migration 072 inside `bin/drafts/` — they are held because the session_id / evidence_items gaps they patch are *downstream consequences* of the PG WAL triage, not independent defects.
- **Operator docs for a merged proposal** (e.g. `dba-charter-apply/POST-MERGE-RUNBOOK.md`): how to apply and verify a charter whose code is already on main.

### Influential, for context only — NOT applied as migration 071/069 from here

**These two files are DRAFTS under review, not executable here.** They were restored today because the prior repo-wide read strip dropped their bodies, not because they are cleared to run. Treat them as reference for the DBA lane and the schema-version audit proposal until they pass the same gates as everything else in this folder (DBA sign-off, ordering check, production-target confirmation). If in doubt, do not run them from `bin/drafts/`.

- `071-role-inbox-pointers.sql` — durable per-role inbox watermarks, Postgres canonical, Redis demoted to cache.
- `migration-069/069-schema-version-forensic-stamp.sql` + `APPLY-069.sh` — forensic stamping for `nebula.schema_version` (audit table + trigger + self-stamp).

### Why the session_id backfill is held (short form)
