-- 069-add-ledger-content-hash.sql — content_hash column on nebula.schema_version
--
-- Decision 23 (architect ruling 7f2b377a, 2026-09-29): the migration gate binds
-- migration CONTENT, not git revision. Property 2: post-apply, the runner
-- records the applied content hash into the ledger, so divergence is auditable
-- from the database alone, without the repo.
--
-- Nullable on purpose: rows 001-041 predate per-version ledger tracking, and
-- 042-068 were stamped before this column existed. Those rows are filled by
-- bin/backfill-migration-content-hashes.py (documented, dry-run by default) —
-- the backfill is deliberately NOT in this file, because a migration must
-- reproduce byte-for-byte on every environment, and "today's bytes" are not a
-- stable artifact. Only 069+ carry their own hash at stamp time, stamped by
-- migrate.ts from the single read the gate verified (no TOCTOU re-read).
--
-- Forward plan (DBA sketch 7c5fe000 item D): from this version on, every
-- pending file's sha256 is compared against migrations/attestations.json
-- BEFORE apply (migrate-gate.ts content binding, Decision 23 property 1) and
-- recorded here after (property 2).

ALTER TABLE nebula.schema_version
    ADD COLUMN content_hash TEXT;

COMMENT ON COLUMN nebula.schema_version.content_hash IS
    'sha256 of the migration file bytes as applied (Decision 23 content binding). NULL for versions stamped before this column existed; historical rows are backfilled from migrations/attestations.json by bin/backfill-migration-content-hashes.py.';
