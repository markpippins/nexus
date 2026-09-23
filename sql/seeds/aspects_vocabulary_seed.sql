-- ============================================================================
-- Seed: aspects.governed_tag_vocabulary -- first ratified seed set
-- (fleet classification axes).
--
-- Grounded in bin/config/fleet-annotations.json, whose own _comment marks
-- these classifications as "Nexus judgment layered on the osmium declared
-- source ... Not fleet fact" -- precisely what a governed vocabulary owns.
-- Axes = the classification keys the file actually uses (environment, os,
-- server_type, framework, service_type); values = the values actually
-- observed (Production, Development, Linux, Physical, Spring Boot,
-- REST API). Nothing invented.
--
-- Normalization: lower-kebab per python/expression/tag_adapter.py
-- _normalize_tag (_TAG_RE = [^a-z0-9._:-]+ -> '-', strip, lower, trim '-').
-- Asserted fail-closed in the DO block below.
--
-- member_kind: 'category' for the axes themselves, 'value' for entries under
-- them (binding_port.py maps governed tags to solver ContractConcepts and
-- list_relationships() turns parent_id into parent->child edges). applies_to:
-- 'host' for host-level axes (environment/os/server_type), 'service' for
-- service-level axes (framework/service_type).
--
-- IDs: deterministic uuid5(namespace='5333056f-2750-599b-b81a-9eadf8e29a7f',
-- name=<row name>); the namespace is itself uuid5(NAMESPACE_URL,
-- 'https://nexus.internal/aspects/vocabulary-seed'). Precomputed literals so
-- this file is pure SQL (PostgreSQL has no builtin uuid5); re-derivation:
--   python3 -c "import uuid; ns=uuid.uuid5(uuid.NAMESPACE_URL,'https://nexus.internal/aspects/vocabulary-seed'); print(uuid.uuid5(ns,'<name>'))"
--
-- Provenance: bin/config/fleet-annotations.json (axes + observed values),
-- review record bc724f6b (vocabulary design authority), agent record 13fc04ea
-- (seed intent). Every seeded row's notes end with the marker "seed:13fc04ea";
-- the assertion block scopes itself to that marker, so only seed rows are
-- checked and later non-seed rows never trip the guards.
--
-- Idempotent: NOT EXISTS-guarded on active name (unique index
-- governed_tag_vocabulary_active_name_idx). Re-runs are no-ops.
-- Run with: psql -v ON_ERROR_STOP=1 -f sql/seeds/aspects_vocabulary_seed.sql
-- ============================================================================

INSERT INTO aspects.governed_tag_vocabulary
    (id, name, normalized_name, member_kind, definition, applies_to, parent_id, ratified_by, notes, created_at)
SELECT
    v.id::uuid,
    v.name,
    v.normalized_name,
    v.member_kind,
    v.definition,
    v.applies_to,
    v.parent_id::uuid,
    'engineer',
    v.notes,
    now()
FROM (VALUES
    -- ---- axes (member_kind = category, no parent) --------------------------
    ('931e1d2d-eef8-57ed-bb1b-3d7939bfb7d3', 'Environment',
     'environment', 'category',
     'Deployment environment axis. Governed classification of a host or service by deployment environment.',
     'host', NULL,
     'Axis provenance: fleet-annotations.json hosts[].environment (Nexus judgment layer per its own _comment). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('d7b69b14-9ed6-5613-b822-1ece221cee93', 'Operating System',
     'operating-system', 'category',
     'Operating system axis. Governed classification of a host by operating system.',
     'host', NULL,
     'Axis provenance: fleet-annotations.json hosts[].os (Nexus judgment layer). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('e9f65b9a-6227-5c33-9fef-9ce0f9dd123a', 'Server Type',
     'server-type', 'category',
     'Server type axis. Governed classification of a host by hardware or instance class.',
     'host', NULL,
     'Axis provenance: fleet-annotations.json hosts[].server_type (Nexus judgment layer). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('8033338d-ee5e-5456-8ed1-8f20864ea7ab', 'Framework',
     'framework', 'category',
     'Framework axis. Governed classification of a service by implementation framework.',
     'service', NULL,
     'Axis provenance: fleet-annotations.json services[].framework (Nexus judgment layer). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('2cb60c37-a9b8-5912-a3a3-1ba4c3921629', 'Service Type',
     'service-type', 'category',
     'Service type axis. Governed classification of a service by interface archetype.',
     'service', NULL,
     'Axis provenance: fleet-annotations.json services[].service_type (Nexus judgment layer). Seed: agent record 13fc04ea. seed:13fc04ea'),
    -- ---- values (member_kind = value, parent = their axis) -----------------
    ('80248587-8b9a-52ed-afc2-65bc065609fd', 'Production',
     'production', 'value',
     'Production environment. Governed value under the Environment axis.',
     'host', '931e1d2d-eef8-57ed-bb1b-3d7939bfb7d3',
     'Observed in fleet-annotations.json (hosts titanium, helium, vanadium and peers). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('ea494e25-ffd3-55d5-a9d0-ae575eb6e29d', 'Development',
     'development', 'value',
     'Development environment. Governed value under the Environment axis.',
     'host', '931e1d2d-eef8-57ed-bb1b-3d7939bfb7d3',
     'Observed in fleet-annotations.json. Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('f3ac66ef-761a-5045-bc48-3c77a2297b5b', 'Linux',
     'linux', 'value',
     'Linux operating system. Governed value under the Operating System axis.',
     'host', 'd7b69b14-9ed6-5613-b822-1ece221cee93',
     'Observed in fleet-annotations.json (all hosts). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('d27b723e-dee5-5d0a-8af6-2973c34276b1', 'Physical',
     'physical', 'value',
     'Physical hardware class. Governed value under the Server Type axis.',
     'host', 'e9f65b9a-6227-5c33-9fef-9ce0f9dd123a',
     'Observed in fleet-annotations.json (all hosts). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('c08e0e2f-c3b4-5640-a569-9f25e62f7953', 'Spring Boot',
     'spring-boot', 'value',
     'Spring Boot framework. Governed value under the Framework axis.',
     'service', '8033338d-ee5e-5456-8ed1-8f20864ea7ab',
     'Observed in fleet-annotations.json (service nexus-core). Seed: agent record 13fc04ea. seed:13fc04ea'),
    ('a9fab40c-14c5-5034-97af-e72fa8829cc1', 'REST API',
     'rest-api', 'value',
     'REST API service archetype. Governed value under the Service Type axis.',
     'service', '2cb60c37-a9b8-5912-a3a3-1ba4c3921629',
     'Observed in fleet-annotations.json (service nexus-core). Seed: agent record 13fc04ea. seed:13fc04ea')
) AS v(id, name, normalized_name, member_kind, definition, applies_to, parent_id, notes)
WHERE NOT EXISTS (
    SELECT 1
    FROM aspects.governed_tag_vocabulary g
    WHERE g.name = v.name
      AND g.expired_at IS NULL
);

-- ---------------------------------------------------------------------------
-- Fail-closed assertions: abort (ON_ERROR_STOP) on any violation. Scoped to
-- the seed marker so only rows this file owns are checked.
-- ---------------------------------------------------------------------------
DO $seed_assert$
DECLARE
    bad_normalization int;
    bad_member_kind   int;
    bad_applies_to    int;
    bad_axis_shape    int;
    bad_value_parent  int;
    total_seed        int;
    axes              int;
    values_           int;
BEGIN
    -- 1. normalized_name equals the tag-adapter normalization of name
    SELECT count(*) INTO bad_normalization
    FROM aspects.governed_tag_vocabulary g
    WHERE g.notes LIKE '%seed:13fc04ea%'
      -- lower() FIRST (mirrors python: .strip().lower() precedes the regex);
      -- replacing before lower() would let uppercase letters match the
      -- complement class and be eaten.
      AND btrim(regexp_replace(lower(g.name), '[^a-z0-9._:-]+', '-', 'g'), '-')
          <> g.normalized_name;

    -- 2. member_kind domain
    SELECT count(*) INTO bad_member_kind
    FROM aspects.governed_tag_vocabulary g
    WHERE g.notes LIKE '%seed:13fc04ea%'
      AND g.member_kind NOT IN ('category', 'value');

    -- 3. applies_to domain
    SELECT count(*) INTO bad_applies_to
    FROM aspects.governed_tag_vocabulary g
    WHERE g.notes LIKE '%seed:13fc04ea%'
      AND g.applies_to NOT IN ('host', 'service');

    -- 4. axes: category rows have no parent
    SELECT count(*) INTO bad_axis_shape
    FROM aspects.governed_tag_vocabulary g
    WHERE g.notes LIKE '%seed:13fc04ea%'
      AND g.member_kind = 'category'
      AND g.parent_id IS NOT NULL;

    -- 5. values: value rows point at an active seed category
    SELECT count(*) INTO bad_value_parent
    FROM aspects.governed_tag_vocabulary g
    WHERE g.notes LIKE '%seed:13fc04ea%'
      AND g.member_kind = 'value'
      AND (
            g.parent_id IS NULL
         OR NOT EXISTS (
                SELECT 1 FROM aspects.governed_tag_vocabulary p
                WHERE p.id = g.parent_id
                  AND p.member_kind = 'category'
                  AND p.expired_at IS NULL
                  AND p.notes LIKE '%seed:13fc04ea%'
            )
          );

    -- 6. expected census: 11 rows = 5 axes + 6 values
    SELECT count(*),
           count(*) FILTER (WHERE member_kind = 'category'),
           count(*) FILTER (WHERE member_kind = 'value')
      INTO total_seed, axes, values_
    FROM aspects.governed_tag_vocabulary
    WHERE notes LIKE '%seed:13fc04ea%';

    IF bad_normalization + bad_member_kind + bad_applies_to
       + bad_axis_shape + bad_value_parent > 0 THEN
        RAISE EXCEPTION 'aspects vocabulary seed assertions failed: normalization=% member_kind=% applies_to=% axis_shape=% value_parent=%',
            bad_normalization, bad_member_kind, bad_applies_to, bad_axis_shape, bad_value_parent;
    END IF;

    IF total_seed <> 11 OR axes <> 5 OR values_ <> 6 THEN
        RAISE EXCEPTION 'aspects vocabulary seed census wrong: total=% (expected 11), axes=% (expected 5), values=% (expected 6)',
            total_seed, axes, values_;
    END IF;

    RAISE NOTICE 'aspects vocabulary seed verified: % rows (% axes, % values), all assertions passed',
        total_seed, axes, values_;
END
$seed_assert$;
