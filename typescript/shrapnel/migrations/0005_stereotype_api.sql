-- ============================================================================
-- shrapnel migration 0005: stereotype database API (functions + views)
-- ----------------------------------------------------------------------------
-- Single-owner delivery, per the 0003 doctrine: ONE source applied to BOTH the
-- `nexus` and `sol` databases via SHRAPNEL_PG_DSN=<dsn> npm run migrate.
--
-- Packages the 0004 stereotype model's queries and constructions as callable
-- database API, so consumers (SOLScript loader, Resolution read-through,
-- Keychains manifests, shrapnel-srv REST) stop hand-writing chain walks and
-- conformance joins.
--
-- ALSO hardens the 0004 model itself (section 0), closing gaps found while
-- validating the API on a fresh bootstrap:
--   * contract fingerprint v2 — canonical input now covers the FULL field
--     document [{field_id, required}] over ALL fields (required + optional),
--     not just the required ids. Under 0004 v1, editing a committed revision's
--     OPTIONAL field rows (required->false flips, row deletions) was
--     undetectable: the fingerprint ignored non-required fields and there was
--     no tamper guard on stereotype_field at all. v2 fixes both: the freeze
--     trigger (0c) rejects any post-commit write to a revision's field rows,
--     and v2 fingerprints make the committed bytes part of the identity.
--     Root revisions (no parent) are re-fingerprinted v2 on apply; child
--     revisions keep their v1 fingerprints (recomputing would break the
--     parent-superset comparison basis recorded at their commit) — the two
--     generations coexist because the freeze trigger now guards the rows.
--   * superset check v2 — also rejects REQUIRED->optional downgrades of a
--     parent-required field, not just absence (a child may narrow a parent's
--     guarantees only by adding, never by weakening).
--   * duplicate-constraint bug in 0004's CREATE TABLE removed at the source
--     (uq_sterev_identity_version was declared twice; fresh bootstraps of the
--     uncorrected file would abort on the second declaration).
--
-- Introspection:
--   stereotype_resolve(name)                -> (stereotype_id, head_revision_id, version)
--   stereotype_chain(revision)              -> table(name, version, hop)   [self=hop 0, parents outward]
--   stereotype_extends(child_rev, name)     -> boolean (transitive; false for self)
--   stereotype_effective_contract(revision) -> table(property_name, required, origin_revision, origin_stereotype, origin_version)
--                                              compiled parent-union-child contract; nearest declaration wins.
--                                              CONTRACT-LEVEL ONLY: no payload merging (analyst condition 1).
--   object_conformance(object_id)           -> jsonb {object_id, classified, stereotype, revision_id, conformant, missing[]}
--                                              read-only evaluation; distinct from the stored
--                                              stereotype_conformance evidence fact (write-gate record).
--
-- Construction:
--   stereotype_create_revision(name, extends_revision, rationale,
--                              required_fields text[], optional_fields text[])
--       -> bigint  one-call revision constructor: identity get-or-create,
--                  version = max+1 (uq_sterev_identity_version arbitrates races),
--                  server-computed v2 fingerprint over the full field document;
--                  the 0004 deferred triggers verify superset/depth/fingerprint
--                  at the caller's COMMIT.
--   object_classify(object_id, revision, disposition)
--       -> jsonb   atomic classification: pre-checks the EFFECTIVE contract,
--                  get-or-creates the conformant-evidence OAV row (plus a
--                  disposition record when provided), then sets membership.
--                  Any failure aborts the surrounding transaction — conformance
--                  is evaluable data and is never silently repaired.
--
-- Views:
--   v_stereotype_contract   per-revision declared contract (property-level)
--   v_object_stereotype     classified objects with stereotype identity
--
-- Non-goals (binding): no revision mutation path (append-only, C5); no payload
-- merging (Reading A stays rejected); no authority semantics.
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. 0004 hardening
-- ─────────────────────────────────────────────────────────────────────────────

-- 0a. Fingerprint v2: canonical input is the FULL field document
--     [{"id": <field_id>, "required": <bool>}, ...] sorted by field_id,
--     plus stereotype id, pinned parent revision, extends rationale.
--     Order-independent for fields; covers required AND optional members.
CREATE OR REPLACE FUNCTION shrapnel.stereotype_canonical_contract(
  p_stereotype_id      bigint,
  p_parent_revision_id bigint,
  p_extends_rationale  text,
  p_fields             jsonb   -- [{"id": bigint, "required": boolean}, ...]
)
RETURNS text
LANGUAGE sql
STABLE
AS $function$
  SELECT 'sha256:' || encode(
    sha256(
      convert_to(
        jsonb_build_object(
          'stereotype_id',      p_stereotype_id,
          'parent_revision_id', p_parent_revision_id,
          'extends_rationale',  p_extends_rationale,
          'fields', (
            SELECT coalesce(jsonb_agg(e ORDER BY (e->>'id')::bigint), '[]'::jsonb)
            FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(p_fields) = 'array' THEN p_fields
                        ELSE '[]'::jsonb END) e
          )
        )::text,
        'UTF8'
      )
    ),
    'hex'
  )
$function$;

-- Convenience helper (also the canonical form used by every v2 fingerprint
-- computation): the revision's full field document, sorted by field id.
CREATE OR REPLACE FUNCTION shrapnel.stereotype_fields_document(p_revision_id bigint)
RETURNS jsonb
LANGUAGE sql
STABLE
AS $function$
  SELECT coalesce(jsonb_agg(jsonb_build_object('id', sf.field_id, 'required', sf.required)
                            ORDER BY sf.field_id), '[]'::jsonb)
  FROM shrapnel.stereotype_field sf
  WHERE sf.stereotype_revision_id = p_revision_id
$function$;

-- Retire the 0004 v1 canonical-contract signature (required-ids bigint[]):
-- keeping both would let new code compute v1 fingerprints that the v2
-- verifier then rejects. The v2 jsonb-document signature is the only form.
DROP FUNCTION IF EXISTS shrapnel.stereotype_canonical_contract(bigint, bigint, text, bigint[]);

-- 0b. Fingerprint verifier v2: same trigger-function signature as 0004's
--     (CREATE OR REPLACE keeps its OID, so trg_stereotype_revision_fingerprint
--     automatically executes this body from now on). Computes the expected
--     fingerprint over the FULL field document. AFTER INSERT only, so already
--     committed revisions are never re-verified; every revision inserted
--     after this migration lands is verified v2.
CREATE OR REPLACE FUNCTION shrapnel.verify_stereotype_fingerprint()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_expected text;
  v_fields   jsonb;
BEGIN
  SELECT coalesce(jsonb_agg(jsonb_build_object('id', sf.field_id, 'required', sf.required)
                            ORDER BY sf.field_id), '[]'::jsonb)
    INTO v_fields
    FROM shrapnel.stereotype_field sf
   WHERE sf.stereotype_revision_id = NEW.id;

  v_expected := shrapnel.stereotype_canonical_contract(
    NEW.stereotype_id, NEW.parent_revision_id, NEW.extends_rationale, v_fields);

  IF v_expected <> NEW.contract_fingerprint THEN
    RAISE EXCEPTION 'stereotype_revision %: contract_fingerprint mismatch (expected %, got %)',
      NEW.id, v_expected, NEW.contract_fingerprint USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$function$;

-- 0c. stereotype_field freeze: a revision's field rows are part of the
--     versioned contract. A field row may only be INSERTed, UPDATEd, or
--     DELETEd while its revision row was created in the SAME transaction
--     (revision xmin == current xid) — that is the deferred-trigger commit
--     protocol. After the creating transaction commits, the contract is
--     frozen: post-commit INSERTs (silent contract growth), required-flag
--     flips, and row removals are all rejected. (Caveat documented: a
--     revision + fields built inside a subtransaction with its own xid would
--     false-positive; the constructor does not use subtransactions.)
CREATE OR REPLACE FUNCTION shrapnel.forbid_stereotype_field_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_revision_id bigint;
BEGIN
  v_revision_id := COALESCE(OLD.stereotype_revision_id, NEW.stereotype_revision_id);
  IF EXISTS (
    SELECT 1 FROM shrapnel.stereotype_revision r
    WHERE r.id = v_revision_id
      AND r.xmin::text::bigint <> (txid_current() % 4294967296)::bigint
  ) THEN
    RAISE EXCEPTION 'stereotype_field rows for revision % are frozen (append-only contract); % rejected',
      v_revision_id, TG_OP USING ERRCODE = '23514';
  END IF;
  -- BEFORE ROW triggers MUST return the row to keep the operation alive:
  -- returning NULL would silently cancel the INSERT/UPDATE/DELETE.
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_field_no_update ON shrapnel.stereotype_field;
DROP TRIGGER IF EXISTS trg_stereotype_field_no_delete ON shrapnel.stereotype_field;
DROP TRIGGER IF EXISTS trg_stereotype_field_freeze ON shrapnel.stereotype_field;
CREATE TRIGGER trg_stereotype_field_freeze
  BEFORE INSERT OR UPDATE OR DELETE ON shrapnel.stereotype_field
  FOR EACH ROW EXECUTE FUNCTION shrapnel.forbid_stereotype_field_mutation();

-- 0d. Superset v2: presence AND required-flag monotonicity. A child may add
--     fields and keep parent-required fields required; it may NEVER downgrade
--     a parent-required field to optional, nor drop it.
CREATE OR REPLACE FUNCTION shrapnel.check_stereotype_field_superset_v2()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_missing bigint[];
  v_weakened bigint[];
BEGIN
  IF NEW.parent_revision_id IS NULL THEN
    RETURN NULL;
  END IF;

  SELECT array_agg(pf.field_id ORDER BY pf.field_id)
    INTO v_missing
    FROM shrapnel.stereotype_field pf
    WHERE pf.stereotype_revision_id = NEW.parent_revision_id
      AND pf.required
      AND NOT EXISTS (
        SELECT 1 FROM shrapnel.stereotype_field cf
        WHERE cf.stereotype_revision_id = NEW.id
          AND cf.field_id = pf.field_id
      );
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'stereotype_revision %: required fields % missing relative to parent revision % (child contract must be a superset)',
      NEW.id, v_missing, NEW.parent_revision_id USING ERRCODE = '23514';
  END IF;

  SELECT array_agg(pf.field_id ORDER BY pf.field_id)
    INTO v_weakened
    FROM shrapnel.stereotype_field pf
    WHERE pf.stereotype_revision_id = NEW.parent_revision_id
      AND pf.required
      AND EXISTS (
        SELECT 1 FROM shrapnel.stereotype_field cf
        WHERE cf.stereotype_revision_id = NEW.id
          AND cf.field_id = pf.field_id
          AND cf.required = false
      );
  IF v_weakened IS NOT NULL THEN
    RAISE EXCEPTION 'stereotype_revision %: fields % downgraded from required to optional relative to parent revision % (required-flags are monotonic down the chain)',
      NEW.id, v_weakened, NEW.parent_revision_id USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_revision_superset ON shrapnel.stereotype_revision;
CREATE CONSTRAINT TRIGGER trg_stereotype_revision_superset
  AFTER INSERT ON shrapnel.stereotype_revision
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION shrapnel.check_stereotype_field_superset_v2();

-- Re-fingerprint ROOT revisions (no parent) to v2: their canonical input has
-- no dependency on other revisions' fingerprint generation, and root rows are
-- the identity anchor of every chain. Child revisions keep v1 fingerprints
-- (see header) — the freeze trigger (0c) guards their field rows instead.
-- Idempotent: re-running matches fingerprints that are already v2.
-- The append-only UPDATE trigger is disabled for this controlled, ledgered
-- administrative rewrite and re-enabled immediately (same transaction).
ALTER TABLE shrapnel.stereotype_revision
  DISABLE TRIGGER trg_stereotype_revision_no_update;
DO $rehash$
DECLARE
  r record;
  v_fields jsonb;
  v_expected text;
  v_changed integer := 0;
BEGIN
  FOR r IN
    SELECT id, stereotype_id FROM shrapnel.stereotype_revision
    WHERE parent_revision_id IS NULL
  LOOP
    v_fields := shrapnel.stereotype_fields_document(r.id);
    v_expected := shrapnel.stereotype_canonical_contract(
      r.stereotype_id, NULL, NULL, v_fields);

    IF (SELECT contract_fingerprint FROM shrapnel.stereotype_revision
        WHERE id = r.id) <> v_expected THEN
      UPDATE shrapnel.stereotype_revision
         SET contract_fingerprint = v_expected
       WHERE id = r.id;
      v_changed := v_changed + 1;
    END IF;
  END LOOP;
  RAISE NOTICE '0005: re-fingerprinted % root revision(s) to v2', v_changed;
END;
$rehash$;
ALTER TABLE shrapnel.stereotype_revision
  ENABLE TRIGGER trg_stereotype_revision_no_update;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. stereotype_resolve: name -> current head revision
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.stereotype_resolve(p_name text)
RETURNS TABLE(stereotype_id bigint, head_revision_id bigint, version integer)
LANGUAGE sql
STABLE
AS $function$
  SELECT s.id, r.id, r.version
  FROM shrapnel.stereotype s
  JOIN shrapnel.stereotype_revision r ON r.stereotype_id = s.id
  WHERE s.name = p_name
  ORDER BY r.version DESC
  LIMIT 1
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. stereotype_chain: the packaged recursive walk (self = hop 0, parents outward)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.stereotype_chain(p_revision_id bigint)
RETURNS TABLE(name text, version integer, hop integer)
LANGUAGE sql
STABLE
AS $function$
  WITH RECURSIVE walk AS (
    SELECT r.id, r.stereotype_id, r.version, r.parent_revision_id, 0 AS hop
    FROM shrapnel.stereotype_revision r
    WHERE r.id = p_revision_id
    UNION ALL
    SELECT p.id, p.stereotype_id, p.version, p.parent_revision_id, w.hop + 1
    FROM shrapnel.stereotype_revision p
    JOIN walk w ON p.id = w.parent_revision_id
  )
  SELECT s.name, w.version, w.hop
  FROM walk w
  JOIN shrapnel.stereotype s ON s.id = w.stereotype_id
  ORDER BY w.hop
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. stereotype_extends: transitive test against a stereotype NAME
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.stereotype_extends(p_child_revision bigint, p_ancestor_name text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $function$
  SELECT EXISTS (
    SELECT 1
    FROM shrapnel.stereotype_chain(p_child_revision) c
    WHERE c.hop > 0
      AND c.name = p_ancestor_name
  )
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. stereotype_effective_contract: compiled (flattened) contract with origin
--    provenance. Nearest declaration wins (self = hop 0 first). Contract-level
--    only — the object's stored payload is never merged or rewritten.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.stereotype_effective_contract(p_revision_id bigint)
RETURNS TABLE(property_name text, required boolean,
              origin_revision bigint, origin_stereotype text, origin_version integer)
LANGUAGE sql
STABLE
AS $function$
  WITH RECURSIVE walk AS (
    SELECT r.id, r.stereotype_id, r.version, r.parent_revision_id, 0 AS hop
    FROM shrapnel.stereotype_revision r
    WHERE r.id = p_revision_id
    UNION ALL
    SELECT p.id, p.stereotype_id, p.version, p.parent_revision_id, w.hop + 1
    FROM shrapnel.stereotype_revision p
    JOIN walk w ON p.id = w.parent_revision_id
  )
  SELECT DISTINCT ON (f.property_name)
         f.property_name,
         sf.required,
         w.id,
         s.name,
         w.version
  FROM walk w
  JOIN shrapnel.stereotype_field sf ON sf.stereotype_revision_id = w.id
  JOIN shrapnel.field f             ON f.id = sf.field_id
  JOIN shrapnel.stereotype s        ON s.id = w.stereotype_id
  ORDER BY f.property_name, w.hop
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. object_conformance: read-only conformance evaluation as jsonb
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.object_conformance(p_object_id bigint)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
  v_stereotype text;
  v_revision   bigint;
  v_missing    text[];
BEGIN
  SELECT s.name, o.stereotype_revision_id
    INTO v_stereotype, v_revision
  FROM shrapnel.object_instance o
  LEFT JOIN shrapnel.stereotype_revision r ON r.id = o.stereotype_revision_id
  LEFT JOIN shrapnel.stereotype s          ON s.id = r.stereotype_id
  WHERE o.id = p_object_id;

  IF v_revision IS NULL THEN
    RETURN jsonb_build_object('object_id', p_object_id, 'classified', false);
  END IF;

  SELECT coalesce(array_agg(e.property_name ORDER BY e.property_name), ARRAY[]::text[])
    INTO v_missing
  FROM shrapnel.stereotype_effective_contract(v_revision) e
  WHERE e.required
    AND NOT EXISTS (
      SELECT 1
      FROM shrapnel.object_attribute_value oav
      JOIN shrapnel.field f ON f.id = oav.field_id
      WHERE oav.object_id = p_object_id
        AND f.property_name = e.property_name
    );

  RETURN jsonb_build_object(
    'object_id',    p_object_id,
    'classified',   true,
    'stereotype',   v_stereotype,
    'revision_id',  v_revision,
    'conformant',   (v_missing IS NULL OR array_length(v_missing, 1) IS NULL),
    'missing',      to_jsonb(coalesce(v_missing, ARRAY[]::text[]))
  );
END;
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. stereotype_create_revision: one-call revision constructor
--    (run inside the caller's transaction; the 0004 deferred triggers verify
--    fingerprint/superset at COMMIT)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.stereotype_create_revision(
  p_name             text,
  p_extends_revision bigint,
  p_rationale        text,
  p_required_fields  text[],
  p_optional_fields  text[] DEFAULT NULL
)
RETURNS bigint
LANGUAGE plpgsql
AS $function$
DECLARE
  v_stereotype_id        bigint;
  v_parent_stereotype_id bigint;
  v_parent_depth         integer;
  v_version              integer;
  v_revision             bigint;
  v_fields               jsonb := '[]'::jsonb;
  v_fid                  bigint;
  v_seen                 text[] := ARRAY[]::text[];
  t                      record;
BEGIN
  IF p_name IS NULL OR btrim(p_name) = '' THEN
    RAISE EXCEPTION 'stereotype_create_revision: name is required';
  END IF;

  -- Identity get-or-create.
  SELECT id INTO v_stereotype_id FROM shrapnel.stereotype WHERE name = p_name;
  IF v_stereotype_id IS NULL THEN
    INSERT INTO shrapnel.stereotype (name) VALUES (p_name)
    RETURNING id INTO v_stereotype_id;
  END IF;

  -- Parent validation (C1: rationale required when extending).
  IF p_extends_revision IS NOT NULL THEN
    IF p_rationale IS NULL OR btrim(p_rationale) = '' THEN
      RAISE EXCEPTION 'stereotype_create_revision: extends requires a rationale (C1 shallow-hierarchy doctrine)';
    END IF;
    SELECT stereotype_id, depth INTO v_parent_stereotype_id, v_parent_depth
    FROM shrapnel.stereotype_revision
    WHERE id = p_extends_revision;
    IF v_parent_stereotype_id IS NULL THEN
      RAISE EXCEPTION 'stereotype_create_revision: parent revision % not found', p_extends_revision;
    END IF;
  END IF;

  -- Next version (uq_sterev_identity_version arbitrates concurrent races).
  SELECT coalesce(max(version), 0) + 1 INTO v_version
  FROM shrapnel.stereotype_revision
  WHERE stereotype_id = v_stereotype_id;

  -- Fields: get-or-create by property_name (default String, code 2), then
  -- build the full fields document [{id, required}] for the v2 fingerprint.
  -- A field may not be declared twice in one call (required and optional
  -- are mutually exclusive per field).
  -- On nexus the field INSERT fires trg_sync_field_metadata_to_resolution
  -- (present live); on sol no such trigger exists — both paths are correct.
  FOR t IN
    SELECT pn, true AS req FROM unnest(coalesce(p_required_fields, ARRAY[]::text[])) pn
    UNION ALL
    SELECT pn, false FROM unnest(coalesce(p_optional_fields, ARRAY[]::text[])) pn
  LOOP
    IF t.pn = ANY (v_seen) THEN
      RAISE EXCEPTION 'stereotype_create_revision: field % declared more than once', t.pn;
    END IF;
    v_seen := v_seen || t.pn;

    SELECT id INTO v_fid FROM shrapnel.field WHERE property_name = t.pn;
    IF v_fid IS NULL THEN
      INSERT INTO shrapnel.field
        (is_calculated, field_index, label, name, property_name, field_type_code)
      VALUES
        (false, 0, t.pn, t.pn, t.pn, 2)
      RETURNING id INTO v_fid;
    END IF;
    v_fields := v_fields || jsonb_build_object('id', v_fid, 'required', t.req);
  END LOOP;

  -- Revision row with the server-computed v2 fingerprint over the FULL field
  -- document. The immediate acyclicity trigger recomputes depth; the deferred
  -- fingerprint/superset/field-integrity triggers verify at COMMIT.
  INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
  VALUES
    (v_stereotype_id, v_version, p_extends_revision, v_parent_stereotype_id,
     p_rationale, coalesce(v_parent_depth + 1, 0),
     shrapnel.stereotype_canonical_contract(
       v_stereotype_id, p_extends_revision, p_rationale, v_fields))
  RETURNING id INTO v_revision;

  INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
  SELECT v_revision, (e->>'id')::bigint, (e->>'required')::boolean
  FROM jsonb_array_elements(v_fields) e;

  RETURN v_revision;
END;
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. object_classify: atomic, evidence-producing classification
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shrapnel.object_classify(
  p_object_id    bigint,
  p_revision_id  bigint,
  p_disposition  text
)
RETURNS jsonb
LANGUAGE plpgsql
AS $function$
DECLARE
  v_stereotype_id bigint;
  v_stereotype    text;
  v_field_conf    bigint;
  v_field_disp    bigint;
  v_value_id      bigint;
  v_missing       text[];
BEGIN
  IF NOT EXISTS (SELECT 1 FROM shrapnel.object_instance WHERE id = p_object_id) THEN
    RAISE EXCEPTION 'object_classify: object % not found', p_object_id;
  END IF;

  SELECT s.id, s.name INTO v_stereotype_id, v_stereotype
  FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE r.id = p_revision_id;
  IF v_stereotype_id IS NULL THEN
    RAISE EXCEPTION 'object_classify: revision % not found', p_revision_id;
  END IF;

  -- Pre-check the EFFECTIVE contract (inherited + own requirements).
  -- Failure aborts the whole call: conformance is evaluable data and is
  -- never repaired by materializing defaults.
  SELECT coalesce(array_agg(e.property_name ORDER BY e.property_name), ARRAY[]::text[])
    INTO v_missing
  FROM shrapnel.stereotype_effective_contract(p_revision_id) e
  WHERE e.required
    AND NOT EXISTS (
      SELECT 1
      FROM shrapnel.object_attribute_value oav
      JOIN shrapnel.field f ON f.id = oav.field_id
      WHERE oav.object_id = p_object_id
        AND f.property_name = e.property_name
    );
  IF v_missing IS NOT NULL AND array_length(v_missing, 1) > 0 THEN
    RAISE EXCEPTION 'object_classify: object % missing required members %; conformance is evaluable data, not an implicit default',
      p_object_id, v_missing USING ERRCODE = '23514';
  END IF;

  -- Conformance-evidence OAV row (get-or-create) — satisfies the 0004
  -- membership evidence gate within this same transaction.
  SELECT id INTO v_field_conf FROM shrapnel.field
   WHERE property_name = 'stereotype_conformance';
  IF v_field_conf IS NULL THEN
    INSERT INTO shrapnel.field
      (is_calculated, field_index, label, name, property_name, field_type_code)
    VALUES
      (false, 0, 'StereoType Conformance', 'StereoType Conformance',
       'stereotype_conformance', 2)
    RETURNING id INTO v_field_conf;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM shrapnel.object_attribute_value
    WHERE object_id = p_object_id AND field_id = v_field_conf
  ) THEN
    INSERT INTO shrapnel.value (value_type_code) VALUES (2)
      RETURNING id INTO v_value_id;
    INSERT INTO shrapnel.value_string (id, value) VALUES (v_value_id, 'conformant');
    INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
      VALUES (p_object_id, v_field_conf, v_value_id);
  END IF;

  -- Disposition record (auditability of the classification act itself).
  IF p_disposition IS NOT NULL AND btrim(p_disposition) <> '' THEN
    SELECT id INTO v_field_disp FROM shrapnel.field
     WHERE property_name = 'stereotype_conformance_disposition';
    IF v_field_disp IS NULL THEN
      INSERT INTO shrapnel.field
        (is_calculated, field_index, label, name, property_name, field_type_code)
      VALUES
        (false, 0, 'StereoType Conformance Disposition',
         'StereoType Conformance Disposition',
         'stereotype_conformance_disposition', 2)
      RETURNING id INTO v_field_disp;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM shrapnel.object_attribute_value
      WHERE object_id = p_object_id AND field_id = v_field_disp
    ) THEN
      INSERT INTO shrapnel.value (value_type_code) VALUES (2)
        RETURNING id INTO v_value_id;
      INSERT INTO shrapnel.value_string (id, value) VALUES (v_value_id, p_disposition);
      INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
        VALUES (p_object_id, v_field_disp, v_value_id);
    END IF;
  END IF;

  -- Membership (the evidence gate trigger sees the row inserted above).
  UPDATE shrapnel.object_instance
     SET stereotype_id          = v_stereotype_id,
         stereotype_revision_id = p_revision_id
   WHERE id = p_object_id;

  RETURN jsonb_build_object(
    'object_id',   p_object_id,
    'stereotype',  v_stereotype,
    'revision_id', p_revision_id,
    'disposition', p_disposition,
    'classified',  true
  );
END;
$function$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Views for ad-hoc use
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW shrapnel.v_stereotype_contract AS
SELECT s.name              AS stereotype_name,
       r.id                AS revision_id,
       r.version,
       r.depth,
       r.contract_fingerprint,
       f.property_name,
       sf.required
FROM shrapnel.stereotype_revision r
JOIN shrapnel.stereotype s        ON s.id = r.stereotype_id
JOIN shrapnel.stereotype_field sf ON sf.stereotype_revision_id = r.id
JOIN shrapnel.field f             ON f.id = sf.field_id;

CREATE OR REPLACE VIEW shrapnel.v_object_stereotype AS
SELECT o.id        AS object_id,
       o.created_at,
       s.name      AS stereotype_name,
       r.id        AS revision_id,
       r.version,
       r.depth
FROM shrapnel.object_instance o
JOIN shrapnel.stereotype_revision r ON r.id = o.stereotype_revision_id
JOIN shrapnel.stereotype s          ON s.id = r.stereotype_id;

COMMIT;
