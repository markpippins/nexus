-- =============================================================================
-- sheet-conf-001 fixture: minimal QBE-era shrapnel baseline.
-- -----------------------------------------------------------------------------
-- V128__shrapnel_eav_object_store.sql assumes the legacy (pre-V-numbering)
-- QBE-era store already exists; this fixture recreates exactly the surfaces
-- V128 and V166 touch, for hermetic per-run test databases. Definitions are
-- kept faithful to the live nexus database (dumped 2026-09-16), including the
-- legacy nullable value_type_code / field_type_code columns, varchar(255)
-- string values, and the two sequences. Deliberately minimal: no QBE tables,
-- no stereotype machinery, no data — V128's DO-blocks are conditional and
-- tolerate the fixture's exact shape (they created the same objects in the
-- live DB's history). Applied BEFORE V128 and V166 by the test harness.
-- =============================================================================

-- Constraint names match the ones V128's conditional blocks probe for
-- (pk_field_type_code / pk_shrapnel_field / pk_shrapnel_value /
-- pk_shrapnel_value_string / pk_shrapnel_value_long) so those blocks skip;
-- V128 then adds its own uq_field_property_name on top.
CREATE SCHEMA IF NOT EXISTS shrapnel;

CREATE SEQUENCE IF NOT EXISTS shrapnel.field_seq START 1;
CREATE SEQUENCE IF NOT EXISTS shrapnel.value_seq START 1;
CREATE SEQUENCE IF NOT EXISTS shrapnel.value_string_seq START 1;
CREATE SEQUENCE IF NOT EXISTS shrapnel.value_long_seq START 1;

-- ── field_type: the 1..7 code registry (seeded, as live) ────────────────────
CREATE TABLE shrapnel.field_type (
    code        integer NOT NULL,
    name        character varying(255) NOT NULL,
    description text,
    pg_type     text,
    CONSTRAINT pk_field_type_code PRIMARY KEY (code)
);
INSERT INTO shrapnel.field_type (code, name, description, pg_type) VALUES
    (1, 'Long',      '64-bit integer',          'bigint'),
    (2, 'String',    'UTF-8 text (255 max)',    'text'),
    (3, 'Double',    'IEEE-754 double',         'double precision'),
    (4, 'Boolean',   'true/false',              'boolean'),
    (5, 'Timestamp', 'point in time',           'timestamptz'),
    (6, 'JSONB',     'binary JSON',             'jsonb'),
    (7, 'UUID',      'RFC 4122 identifier',     'uuid');

-- ── field: attribute metadata (legacy shape: nullable type code) ────────────
CREATE TABLE shrapnel.field (
    id              bigint DEFAULT nextval('shrapnel.field_seq'::regclass) NOT NULL,
    is_calculated   boolean NOT NULL,
    field_index     integer NOT NULL,
    label           character varying(255) NOT NULL,
    name            character varying(255) NOT NULL,
    property_name   character varying(255) NOT NULL,
    field_type_code integer,
    CONSTRAINT pk_shrapnel_field PRIMARY KEY (id),
    CONSTRAINT fk_field_type_code FOREIGN KEY (field_type_code)
        REFERENCES shrapnel.field_type (code)
);

-- ── value + the two QBE-era extension tables ────────────────────────────────
CREATE TABLE shrapnel.value (
    id              bigint DEFAULT nextval('shrapnel.value_seq'::regclass) NOT NULL,
    value_type_code integer,
    CONSTRAINT pk_shrapnel_value PRIMARY KEY (id)
);

CREATE TABLE shrapnel.value_long (
    id    bigint NOT NULL,
    value bigint NOT NULL,
    CONSTRAINT pk_shrapnel_value_long PRIMARY KEY (id),
    CONSTRAINT fk_value_long_value FOREIGN KEY (id)
        REFERENCES shrapnel.value (id) ON DELETE CASCADE
);

CREATE TABLE shrapnel.value_string (
    id    bigint NOT NULL,
    value character varying(255) NOT NULL,
    CONSTRAINT pk_shrapnel_value_string PRIMARY KEY (id),
    CONSTRAINT fk_value_string_value FOREIGN KEY (id)
        REFERENCES shrapnel.value (id) ON DELETE CASCADE
);
