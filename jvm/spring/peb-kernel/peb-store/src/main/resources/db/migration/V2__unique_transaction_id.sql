-- V2: Add UNIQUE constraint on peb.violations.transaction_id
--
-- Prevents duplicate violation rows when MCP retries resend the same
-- transaction. PostgreSQL allows multiple NULL values in a UNIQUE
-- constraint, so the rare null-transaction_id row is unaffected.
-- If existing data has duplicates, this migration will fail — run a
-- dedup CTE first or add WHERE clause deferral.
--
-- 2026-09-27 repair: the original referenced peb_violations, the table
-- name at the time V1 flew (June 2026). V1 was later rewritten to create
-- final names in the peb schema directly, which made this migration
-- reference a table no longer created by the chain — discovered when PR
-- #594 ran peb-kernel tests in CI for the first time and needed a
-- fresh-database bootstrap. Checksums differ from titanium's
-- flyway_schema_history; that history was never validated against the
-- repo files (Flyway was disabled in application.yml) and the same DDL
-- is already live in the peb schema, so this repair only re-couples the
-- chain to reality. Fresh-DB proven in CI on the #594 build job.

ALTER TABLE peb.violations
    ADD CONSTRAINT uq_peb_violations_transaction_id UNIQUE (transaction_id);
