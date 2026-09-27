-- 002_intent_record_segment_sets.sql
-- Repairs partial-migration drift from 001_segment_sets.sql: the live
-- database has candidate_segment_sets and requirement_segment_sets but is
-- missing intent_record_segment_sets (001 was not fully applied).
--
-- Idempotent: safe to re-run (IF NOT EXISTS guards). Additive only —
-- never ALTERs or DROPs tables owned by nebula-srv.

create table if not exists nebula.intent_record_segment_sets
(
    id                uuid                     default gen_random_uuid() not null primary key,
    intent_record_id  uuid                                               not null, -- -> intent_records.id
    segment_set_id    uuid                                               not null, -- -> segment_sets.id
    role              text                     default 'primary'::text   not null
        constraint intent_record_segment_sets_role_check
            check (role = ANY (ARRAY ['primary'::text, 'supporting'::text])),
    active            boolean                  default true              not null,
    created_at        timestamp with time zone default now()             not null,
    unique (intent_record_id, segment_set_id)
);

alter table nebula.intent_record_segment_sets
    owner to pguser;
