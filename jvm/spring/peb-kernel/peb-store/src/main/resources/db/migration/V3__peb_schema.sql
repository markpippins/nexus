-- V3: peb-schema migration (historical record)
--
-- History: V3 originally migrated peb_* tables from public to the peb
-- schema and stripped the peb_ prefix. That state was later rewritten
-- directly into V1 (tables created in peb with final names), so this
-- step is now a no-op kept as a chain placeholder. It is deliberately
-- unconditional so it succeeds on both fresh databases (tables already
-- in peb) and legacy databases whose flyway_schema_history was lost.
--
-- 2026-09-27 note (PR #594): the repo chain drifted from titanium's
-- applied history (checksums differ). This repair re-couples the chain
-- to what production actually contains; see V2 for the full story.

CREATE SCHEMA IF NOT EXISTS peb;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'peb_state') THEN
        ALTER TABLE public.peb_state       SET SCHEMA peb;
        ALTER TABLE public.peb_transactions SET SCHEMA peb;
        ALTER TABLE public.peb_decisions    SET SCHEMA peb;
        ALTER TABLE public.peb_traces       SET SCHEMA peb;
        ALTER TABLE public.peb_violations   SET SCHEMA peb;
        ALTER TABLE public.peb_capabilities SET SCHEMA peb;

        ALTER TABLE peb.peb_state        RENAME TO state;
        ALTER TABLE peb.peb_transactions RENAME TO transactions;
        ALTER TABLE peb.peb_decisions    RENAME TO decisions;
        ALTER TABLE peb.peb_traces       RENAME TO traces;
        ALTER TABLE peb.peb_violations   RENAME TO violations;
        ALTER TABLE peb.peb_capabilities RENAME TO capabilities;
    ELSE
        RAISE NOTICE 'V3: peb_* tables not in public — V1 already created final names in peb; nothing to do';
    END IF;
END $$;
