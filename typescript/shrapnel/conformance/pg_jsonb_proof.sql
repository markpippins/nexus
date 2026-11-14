-- ============================================================================
-- STAGED-INERT conformance proof layer — NOT A MIGRATION
-- ----------------------------------------------------------------------------
-- Plan 8261656. This file is deliberately NOT under `migrations/` and is never
-- referenced by `src/scripts/migrate.js` (which only walks `migrations/`). It
-- creates TEMP tables inside a transaction and ROLLBACKs: it proves the
-- PostgreSQL-JSONB side of the storage-adapter conformance matrix without
-- touching a production collection, schema, or row.
--
-- Run manually only, against a disposable database:
--   psql "$SHRAPNEL_TEST_DSN" -v ON_ERROR_STOP=1 -f conformance/pg_jsonb_proof.sql
--
-- The SELECT output is the PG-JSONB half of the semantic descriptor. The
-- canonical comparison half is produced by
-- `python/SOLScript/solscript/adapters/shrapnel_conformance.py`. Both must
-- yield the same type identity, lineage references, governed values, and typed
-- unknown/unavailable statuses (see docs/shrapnel/conformance-matrix.md).
-- ============================================================================

BEGIN;

-- Effective contract (parent union child, nearest declaration wins). Mirrors
-- shrapnel.stereotype_effective_contract()'s shape: required, origin revision,
-- origin stereotype, origin version.
CREATE TEMP TABLE conformance_effective_contract (
    property_name     text    PRIMARY KEY,
    required          boolean NOT NULL,
    value_type        text    NOT NULL,
    origin_revision   integer NOT NULL,
    origin_stereotype text    NOT NULL,
    origin_version    integer NOT NULL
) ON COMMIT DROP;

INSERT INTO conformance_effective_contract
    (property_name, required, value_type, origin_revision, origin_stereotype, origin_version)
VALUES
    ('request_id',              true,  'uuid',        3, 'work_request_tester_attestation', 1),
    ('status',                  true,  'text',        3, 'work_request_tester_attestation', 1),
    ('created_at',              true,  'timestamptz', 3, 'work_request_tester_attestation', 1),
    ('dispatch_state',          true,  'text',        3, 'work_request_tester_attestation', 1),
    ('worker_id',               true,  'uuid',        3, 'work_request_tester_attestation', 1),
    ('attestation_id',          true,  'uuid',        3, 'work_request_tester_attestation', 1),
    ('attested_at',             true,  'timestamptz', 3, 'work_request_tester_attestation', 1),
    ('attestation_verdict',     true,  'text',        3, 'work_request_tester_attestation', 1),
    ('attestation_evidence_ref', false, 'text',        3, 'work_request_tester_attestation', 1),
    ('attestation_payload',     false, 'jsonb',       3, 'work_request_tester_attestation', 1),
    -- Inherited: declared on the dispatchable parent revision, never redeclared.
    ('dispatch_lease_until',    false, 'timestamptz', 2, 'work_request_dispatchable', 1),
    -- Inherited: declared on the base revision, never redeclared.
    ('request_kind',            false, 'text',        1, 'work_request', 1),
    ('lineage_ref',             false, 'text',        1, 'work_request', 1);

-- One PostgreSQL-JSONB-shaped instance. `instance_values` is the decoded
-- OAV/JSONB payload; `unavailable_fields` lets the proof exercise typed
-- UNAVAILABLE without pretending a missing value is a value.
CREATE TEMP TABLE conformance_instance (
    object_id          text  PRIMARY KEY,
    stereotype         text  NOT NULL,
    revision_id        integer NOT NULL,
    instance_values    jsonb NOT NULL,
    unavailable_fields text[] NOT NULL DEFAULT '{}',
    timestamps         jsonb NOT NULL DEFAULT '{}'::jsonb
) ON COMMIT DROP;

INSERT INTO conformance_instance
    (object_id, stereotype, revision_id, instance_values, unavailable_fields, timestamps)
VALUES (
    'pg-wr-attestation-unknown-1',
    'work_request_tester_attestation',
    3,
    '{
       "request_id": "wr-8261656",
       "status": "claimed",
       "created_at": "2026-09-25T13:22:25Z",
       "dispatch_state": "dispatched",
       "worker_id": "worker-63be8d6b",
       "attestation_id": "att-0002",
       "attestation_payload": null
     }'::jsonb,
    ARRAY['dispatch_lease_until'],
    '{"created_at": "2026-09-25T13:22:25Z", "updated_at": "2026-09-25T13:22:25Z"}'::jsonb
);

-- Typed status projection. The CASE is the SQL mirror of the single
-- normalize_instance() path: unavailable > governed > unknown(required) >
-- absent(optional). A JSON null is governed; a missing key is not.
SELECT
    i.object_id,
    i.stereotype,
    i.revision_id,
    c.property_name,
    CASE
        WHEN c.property_name = ANY (i.unavailable_fields) THEN 'unavailable'
        WHEN i.instance_values ? c.property_name           THEN 'governed'
        WHEN c.required                                    THEN 'unknown'
        ELSE 'absent'
    END AS typed_status,
    c.required,
    c.origin_revision,
    c.origin_stereotype,
    c.origin_version,
    CASE
        WHEN i.instance_values ? c.property_name THEN i.instance_values -> c.property_name
        ELSE NULL
    END AS governed_value
FROM conformance_instance AS i
JOIN conformance_effective_contract AS c ON true
ORDER BY c.required DESC, c.property_name;

-- Storage-independent type identity: no table/collection/object id enters it.
SELECT
    'shrapnel:stereotype:' || 'work_request_tester_attestation' AS type_id,
    'work_request_tester_attestation'                           AS stereotype_name,
    3                                                           AS revision_id,
    1                                                           AS version;

-- No production effect: temp tables disappear with the rollback.
ROLLBACK;
