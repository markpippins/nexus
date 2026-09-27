-- 002_drop_intent_record_segment_sets.sql
-- Intent records were eliminated as a domain concept: nebula.intent_records
-- no longer exists, no service exposes intent-record routes, and no UI
-- surfaces them. substance's 001_segment_sets.sql predates that removal and
-- defined an intent_record_segment_sets join table (plus the matching
-- DomainType entry), which 00X on this branch briefly created before the
-- concept's elimination was noticed.
--
-- This migration removes the vestigial table. Idempotent: safe to re-run.
-- It only drops a table introduced by the substance scheme itself — it does
-- not touch any table owned by nebula-srv or historical migrations.

drop table if exists nebula.intent_record_segment_sets;
