-- ============================================================================
-- shrapnel migration 0004: stereotype model (StereoTypes — nominal typing)
-- ----------------------------------------------------------------------------
-- Single-owner delivery, per the 0003 doctrine: ONE source applied to BOTH the
-- `nexus` and `sol` databases via SHRAPNEL_PG_DSN=<dsn> npm run migrate.
--
-- Implements the architect-approved Reading B design from Assembly thread
-- 948f8a6f-9790-49ba-b806-dc5ff9ee529f ("StereoTypes for Shrapnel: data
-- shapes extending interfaces — DBA review requested") under its five binding
-- doctrine constraints, plus the analyst conditions from the same thread:
--
--   C1 shallow hierarchy: depth <= 3, enforced; each `extends` MUST carry a
--      rationale (NOT NULL when a parent is set).
--   C2 composition default: nothing here pushes authoring toward
--      inheritance; `extends` is opt-in and must be justified.
--   C3 additive, NULL-typed only: object_instance gains two nullable columns;
--      no existing rows are altered; classification proceeds incrementally
--      with dispositions. No instance-level parent_id (Reading A rejected).
--   C4 acyclicity trigger at the table level: required, not deferred.
--   C5 Keychains honesty: contracts are versioned; a child's `extends` pins
--      the parent REVISION, so parent edits create new revisions and cannot
--      silently change child conformance. No silent reclassification: a
--      membership row may only exist when conformance evidence exists on the
--      same object.
--
-- Contents:
--   1. shrapnel.stereotype           — mutable identity (unique name).
--   2. shrapnel.stereotype_revision  — immutable versioned contract (pinned
--      parent revision, extends rationale, contract fingerprint, depth).
--      Append-only triggers forbid UPDATE/DELETE.
--   3. shrapnel.stereotype_field     — per-revision required field set
--      (field_id FK to shrapnel.field).
--   4. object_instance.stereotype_id / stereotype_revision_id — nullable
--      membership link, composite-FK'd to prove the revision belongs to the
--      declared identity.
--   5. Trigger package: acyclicity + depth (immediate), contract fingerprint
--      and required-field superset (DEFERRED constraint triggers — verified
--      at COMMIT so revision + field rows land in one transaction), evidence
--      gate on membership, append-only.
--   6. Idempotent reconcile-backfill: stereotype `concept_import_state` v1
--      over the 9 import-state members; conformance evidence + membership
--      for every object already carrying the complete member set. Zero
--      mutation of existing rows; re-running is a no-op and reports counts.
--
-- Conformance-evidence convention (analyst condition 3: "treat conformance
-- as evaluable data"):
--   field  property_name = 'stereotype_conformance'  (String, code 2)
--   value  value_string 'conformant' | 'nonconformant'
--   Written as an ordinary OAV row on the classified object, so conformance
--   verdicts remain queryable facts, are never silently repaired, and are
--   never entangled with the stereotype tables themselves.
--
-- PG 17 core sha256() is used (no pgcrypto dependency). No psql
-- meta-commands: this file is runner-safe (node src/scripts/migrate.js).
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. stereotype: mutable identity (the named type)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS shrapnel.stereotype_seq;

CREATE TABLE IF NOT EXISTS shrapnel.stereotype (
    id          bigint PRIMARY KEY DEFAULT nextval('shrapnel.stereotype_seq'::regclass),
    name        text NOT NULL,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_stereotype_name' AND conrelid = 'shrapnel.stereotype'::regclass
  ) THEN
    ALTER TABLE shrapnel.stereotype
      ADD CONSTRAINT uq_stereotype_name UNIQUE (name);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. stereotype_revision: immutable versioned contract
--    (C5: parent pinned by revision; contract fingerprint; depth recorded)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS shrapnel.stereotype_revision_seq;

CREATE TABLE IF NOT EXISTS shrapnel.stereotype_revision (
    id                   bigint PRIMARY KEY DEFAULT nextval('shrapnel.stereotype_revision_seq'::regclass),
    stereotype_id        bigint NOT NULL REFERENCES shrapnel.stereotype(id),
    version              integer NOT NULL,
    parent_revision_id   bigint,
    parent_stereotype_id bigint,
    extends_rationale    text,
    depth                integer NOT NULL DEFAULT 0,
    contract_fingerprint text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),

    -- A child's parent revision must belong to the declared parent identity.
    -- uq_sterev_stereotype_id must precede fk_sterev_parent_identity: inline
    -- self-referential FKs may only cite constraints declared earlier.
    CONSTRAINT uq_sterev_stereotype_id UNIQUE (stereotype_id, id),
    CONSTRAINT uq_sterev_identity_version UNIQUE (stereotype_id, version),
    CONSTRAINT fk_sterev_parent_identity
      FOREIGN KEY (parent_stereotype_id, parent_revision_id)
      REFERENCES shrapnel.stereotype_revision (stereotype_id, id),
    CONSTRAINT ck_sterev_parent_rationale
      CHECK (parent_revision_id IS NULL OR
             (extends_rationale IS NOT NULL AND btrim(extends_rationale) <> '')),
    CONSTRAINT ck_sterev_depth_range
      CHECK (depth >= 0 AND depth <= 3),
    CONSTRAINT ck_sterev_fingerprint_format
      CHECK (contract_fingerprint ~ '^sha256:[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_sterev_parent
  ON shrapnel.stereotype_revision (parent_revision_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. stereotype_field: the per-revision required field set (the interface)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS shrapnel.stereotype_field_seq;

CREATE TABLE IF NOT EXISTS shrapnel.stereotype_field (
    id                     bigint PRIMARY KEY DEFAULT nextval('shrapnel.stereotype_field_seq'::regclass),
    stereotype_revision_id bigint NOT NULL REFERENCES shrapnel.stereotype_revision(id),
    field_id               bigint NOT NULL REFERENCES shrapnel.field(id),
    required               boolean NOT NULL DEFAULT true,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_sterev_field UNIQUE (stereotype_revision_id, field_id)
);

CREATE INDEX IF NOT EXISTS idx_stereofield_field
  ON shrapnel.stereotype_field (field_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. object membership (C3: additive, NULL-typed, no instance parent_id)
--    The composite FK proves the revision belongs to the declared identity.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE shrapnel.object_instance
  ADD COLUMN IF NOT EXISTS stereotype_revision_id bigint,
  ADD COLUMN IF NOT EXISTS stereotype_id          bigint;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_objinst_membership_pair'
      AND conrelid = 'shrapnel.object_instance'::regclass
  ) THEN
    ALTER TABLE shrapnel.object_instance
      ADD CONSTRAINT ck_objinst_membership_pair
      CHECK ((stereotype_id IS NULL AND stereotype_revision_id IS NULL)
          OR (stereotype_id IS NOT NULL AND stereotype_revision_id IS NOT NULL));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_objinst_stereotype_identity'
      AND conrelid = 'shrapnel.object_instance'::regclass
  ) THEN
    ALTER TABLE shrapnel.object_instance
      ADD CONSTRAINT fk_objinst_stereotype_identity
      FOREIGN KEY (stereotype_id, stereotype_revision_id)
      REFERENCES shrapnel.stereotype_revision (stereotype_id, id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_objinst_stereotype_revision
  ON shrapnel.object_instance (stereotype_revision_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Trigger package
-- ─────────────────────────────────────────────────────────────────────────────

-- 5a. Depth helper (0 for roots); useful to consumers (Keychains manifests).
CREATE OR REPLACE FUNCTION shrapnel.stereotype_depth_of(p_revision_id bigint)
RETURNS integer
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
  v_cur   bigint := p_revision_id;
  v_depth integer := 0;
  v_next  bigint;
BEGIN
  IF p_revision_id IS NULL THEN
    RETURN 0;
  END IF;
  LOOP
    SELECT parent_revision_id INTO v_next
      FROM shrapnel.stereotype_revision WHERE id = v_cur;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stereotype_depth_of: revision % not found', v_cur;
    END IF;
    EXIT WHEN v_next IS NULL;
    v_depth := v_depth + 1;
    v_cur   := v_next;
  END LOOP;
  RETURN v_depth;
END;
$function$;

-- 5b. Acyclicity + depth (C4 + C1). Walks the pinned parent chain.
--     (With append-only revisions and FK-to-existing-parents a cycle cannot
--     normally form; the walk is kept as the required C4 guard for restores
--     and any future parent-mutating path.)
CREATE OR REPLACE FUNCTION shrapnel.check_stereotype_acyclic()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_cur          bigint;
  v_seen         bigint[] := ARRAY[]::bigint[];
  v_hops         integer := 0;
  v_max          integer := 3;
  v_parent_depth integer;
BEGIN
  IF NEW.parent_revision_id IS NULL THEN
    NEW.depth := 0;
    RETURN NEW;
  END IF;

  v_cur := NEW.parent_revision_id;
  LOOP
    IF v_cur = ANY (v_seen) THEN
      RAISE EXCEPTION 'stereotype_revision %: cycle detected in extends chain',
        NEW.id USING ERRCODE = '23514';
    END IF;
    v_seen := v_seen || v_cur;

    SELECT parent_revision_id, depth INTO v_cur, v_parent_depth
      FROM shrapnel.stereotype_revision WHERE id = v_cur;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stereotype_revision %: parent revision % not found',
        NEW.id, v_cur USING ERRCODE = '23503';
    END IF;
    IF v_cur IS NULL THEN
      -- Reached the root of the pinned chain; its depth is authoritative.
      NEW.depth := v_parent_depth + v_hops + 1;
      EXIT;
    END IF;
    v_hops := v_hops + 1;
  END LOOP;

  IF NEW.depth > v_max THEN
    RAISE EXCEPTION 'stereotype_revision %: depth % exceeds maximum % (C1 shallow-hierarchy doctrine)',
      NEW.id, NEW.depth, v_max USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_revision_acyclic ON shrapnel.stereotype_revision;
CREATE TRIGGER trg_stereotype_revision_acyclic
  BEFORE INSERT ON shrapnel.stereotype_revision
  FOR EACH ROW EXECUTE FUNCTION shrapnel.check_stereotype_acyclic();

-- 5c. Contract fingerprint: 'sha256:' || sha256(canonical contract JSON).
--     Canonical input: stereotype id, pinned parent revision, extends
--     rationale, sorted required field ids. Order-independent for fields.
CREATE OR REPLACE FUNCTION shrapnel.stereotype_sort_ids(p bigint[])
RETURNS bigint[]
LANGUAGE sql
IMMUTABLE
AS $function$
  SELECT coalesce((SELECT array_agg(x ORDER BY x) FROM unnest(p) AS x), ARRAY[]::bigint[])
$function$;

CREATE OR REPLACE FUNCTION shrapnel.stereotype_canonical_contract(
  p_stereotype_id      bigint,
  p_parent_revision_id bigint,
  p_extends_rationale  text,
  p_required_field_ids bigint[]
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
          'required_field_ids', to_jsonb(shrapnel.stereotype_sort_ids(p_required_field_ids))
        )::text,
        'UTF8'
      )
    ),
    'hex'
  )
$function$;

-- Fingerprint + superset are DEFERRED CONSTRAINT TRIGGERS: a revision row and
-- its stereotype_field rows must land in the same transaction, and both are
-- verified against the complete contract at COMMIT (analyst condition 2:
-- "version the complete contract"). A revision committed without its fields
-- fails; a child missing parent-required fields fails; a wrong fingerprint
-- fails — all atomically.
CREATE OR REPLACE FUNCTION shrapnel.verify_stereotype_fingerprint()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_expected  text;
  v_field_ids bigint[];
BEGIN
  SELECT coalesce(array_agg(field_id ORDER BY field_id), ARRAY[]::bigint[])
    INTO v_field_ids
    FROM shrapnel.stereotype_field
   WHERE stereotype_revision_id = NEW.id AND required;

  v_expected := shrapnel.stereotype_canonical_contract(
    NEW.stereotype_id, NEW.parent_revision_id,
    NEW.extends_rationale, v_field_ids
  );

  IF v_expected <> NEW.contract_fingerprint THEN
    RAISE EXCEPTION 'stereotype_revision %: contract_fingerprint mismatch (expected %, got %)',
      NEW.id, v_expected, NEW.contract_fingerprint USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_revision_fingerprint ON shrapnel.stereotype_revision;
CREATE CONSTRAINT TRIGGER trg_stereotype_revision_fingerprint
  AFTER INSERT ON shrapnel.stereotype_revision
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION shrapnel.verify_stereotype_fingerprint();

-- 5d. Required-field superset down the extends chain (C1/C2 discipline).
CREATE OR REPLACE FUNCTION shrapnel.check_stereotype_field_superset()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_missing bigint[];
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
  RETURN NULL;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_revision_superset ON shrapnel.stereotype_revision;
CREATE CONSTRAINT TRIGGER trg_stereotype_revision_superset
  AFTER INSERT ON shrapnel.stereotype_revision
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION shrapnel.check_stereotype_field_superset();

-- 5e. Append-only: revisions are immutable history (C5).
CREATE OR REPLACE FUNCTION shrapnel.forbid_stereotype_revision_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'stereotype_revision % is append-only; % rejected',
    COALESCE(OLD.id, NEW.id), TG_OP USING ERRCODE = '23514';
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_revision_no_update ON shrapnel.stereotype_revision;
CREATE TRIGGER trg_stereotype_revision_no_update
  BEFORE UPDATE ON shrapnel.stereotype_revision
  FOR EACH ROW EXECUTE FUNCTION shrapnel.forbid_stereotype_revision_mutation();

DROP TRIGGER IF EXISTS trg_stereotype_revision_no_delete ON shrapnel.stereotype_revision;
CREATE TRIGGER trg_stereotype_revision_no_delete
  BEFORE DELETE ON shrapnel.stereotype_revision
  FOR EACH ROW EXECUTE FUNCTION shrapnel.forbid_stereotype_revision_mutation();

-- 5f. Membership conformance-evidence gate (C5 / analyst condition 3):
--     an object may only be classified when its conformance evidence — an
--     ordinary OAV fact (field 'stereotype_conformance' = 'conformant') —
--     already exists for that object. Nonconformant objects must not be
--     classified; there is no silent default materialization.
CREATE OR REPLACE FUNCTION shrapnel.check_membership_evidence_present()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_field_id bigint;
BEGIN
  IF NEW.stereotype_revision_id IS NULL THEN
    RETURN NEW;  -- unclassified object; nothing to prove
  END IF;

  SELECT id INTO v_field_id
    FROM shrapnel.field
   WHERE property_name = 'stereotype_conformance';

  IF v_field_id IS NULL OR NOT EXISTS (
    SELECT 1
    FROM shrapnel.object_attribute_value oav
    JOIN shrapnel.value v         ON v.id = oav.value_id AND v.value_type_code = 2
    JOIN shrapnel.value_string vs ON vs.id = v.id AND vs.value = 'conformant'
    WHERE oav.object_id = NEW.id
      AND oav.field_id  = v_field_id
  ) THEN
    RAISE EXCEPTION 'object %: stereotype membership requires conformance evidence (OAV field ''stereotype_conformance'' = ''conformant'') recorded for the object; conformance is evaluable data, not an implicit default',
      NEW.id USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_objinst_membership_evidence ON shrapnel.object_instance;
CREATE TRIGGER trg_objinst_membership_evidence
  BEFORE INSERT OR UPDATE OF stereotype_id, stereotype_revision_id ON shrapnel.object_instance
  FOR EACH ROW EXECUTE FUNCTION shrapnel.check_membership_evidence_present();

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Idempotent reconcile-backfill: concept_import_state v1
--    (architect-cleared pilot family; the live import-state records)
--
--    Order matters and is trigger-aware:
--      6a  get-or-create the 9 member field rows
--      6b  compute the contract fingerprint over those field ids
--      6c  insert stereotype identity + revision v1 (fingerprint correct at
--          insert; deferred triggers verify at COMMIT after 6d)
--      6d  insert the stereotype_field rows (required)
--      6e  per-object: seed conformance evidence, then classify — each
--          classification is gated by the evidence the same transaction
--          just wrote (or that already existed)
-- ─────────────────────────────────────────────────────────────────────────────

DO $backfill$
DECLARE
  v_stereoid    bigint;
  v_revid       bigint;
  v_field_ids   bigint[];
  v_fingerprint text;
  v_field_conf  bigint;
  v_obj         record;
  v_value_id    bigint;
  v_evidence    integer := 0;
  v_membered    integer := 0;
  v_skipped     integer := 0;
  v_missing     integer := 0;
BEGIN
  -- 6a. Member field rows (get-or-create by unique property_name).
  --     All 9 live import-state members verified on nexus; presence is the
  --     contract (projected_to_graph is Boolean-encoded on nexus but the
  --     required-field check here is presence, not encoding).
  FOR v_obj IN
    SELECT DISTINCT pn
    FROM unnest(ARRAY[
      'asset_id', 'import_priority_tier', 'service_domain',
      'classification_rationale', 'collision_disposition',
      'import_batch_id', 'mapping_generation',
      'projected_to_graph', 'projection_generation'
    ]) AS pn
  LOOP
    IF NOT EXISTS (SELECT 1 FROM shrapnel.field WHERE property_name = v_obj.pn) THEN
      INSERT INTO shrapnel.field
        (is_calculated, field_index, label, name, property_name, field_type_code)
      VALUES
        (false, 0, v_obj.pn, v_obj.pn, v_obj.pn, 2);
    END IF;
  END LOOP;

  SELECT coalesce(array_agg(f.id ORDER BY f.id), ARRAY[]::bigint[])
    INTO v_field_ids
    FROM shrapnel.field f
    WHERE f.property_name IN (
      'asset_id', 'import_priority_tier', 'service_domain',
      'classification_rationale', 'collision_disposition',
      'import_batch_id', 'mapping_generation',
      'projected_to_graph', 'projection_generation'
    );

  IF array_length(v_field_ids, 1) <> 9 THEN
    RAISE EXCEPTION 'backfill: expected 9 member fields, found %', array_length(v_field_ids, 1);
  END IF;

  -- 6b/6c. Identity + revision v1 with the correct fingerprint at insert.
  SELECT id INTO v_stereoid FROM shrapnel.stereotype WHERE name = 'concept_import_state';
  IF v_stereoid IS NULL THEN
    INSERT INTO shrapnel.stereotype (name, description)
    VALUES ('concept_import_state',
            'Import-state contract over resolution-concept ingest metadata (thread b214eba8): tier, domain, rationale, collision disposition, batch, mapping/projection generation, projected flag.')
    RETURNING id INTO v_stereoid;
  END IF;

  SELECT id INTO v_revid
    FROM shrapnel.stereotype_revision r
   WHERE r.stereotype_id = v_stereoid AND r.version = 1;

  IF v_revid IS NULL THEN
    v_fingerprint := shrapnel.stereotype_canonical_contract(
      v_stereoid, NULL, NULL, v_field_ids);
    INSERT INTO shrapnel.stereotype_revision
      (stereotype_id, version, parent_revision_id, parent_stereotype_id,
       extends_rationale, depth, contract_fingerprint)
    VALUES
      (v_stereoid, 1, NULL, NULL, NULL, 0, v_fingerprint)
    RETURNING id INTO v_revid;
  END IF;

  -- 6d. Required field set for revision v1 (idempotent).
  INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
  SELECT v_revid, f.id, true
  FROM shrapnel.field f
  WHERE f.property_name IN (
    'asset_id', 'import_priority_tier', 'service_domain',
    'classification_rationale', 'collision_disposition',
    'import_batch_id', 'mapping_generation',
    'projected_to_graph', 'projection_generation'
  )
  ON CONFLICT (stereotype_revision_id, field_id) DO NOTHING;

  -- 6e. Per-object evidence + classification.
  SELECT id INTO v_field_conf FROM shrapnel.field
   WHERE property_name = 'stereotype_conformance';
  IF v_field_conf IS NULL THEN
    INSERT INTO shrapnel.field
      (is_calculated, field_index, label, name, property_name, field_type_code)
    VALUES
      (false, 0, 'StereoType Conformance', 'StereoType Conformance',
       'stereotype_conformance', 2)
    RETURNING id INTO v_field_conf;
    RAISE NOTICE 'backfill: created conformance evidence field id %', v_field_conf;
  END IF;

  FOR v_obj IN
    SELECT DISTINCT o.id AS oid
    FROM shrapnel.object_instance o
    JOIN shrapnel.object_attribute_value oav ON oav.object_id = o.id
    JOIN shrapnel.field f ON f.id = oav.field_id
    WHERE f.property_name = 'import_priority_tier'
  LOOP
    -- Object must carry the complete required member set.
    IF EXISTS (
      SELECT 1
      FROM shrapnel.stereotype_field sf
      WHERE sf.stereotype_revision_id = v_revid
        AND NOT EXISTS (
          SELECT 1
          FROM shrapnel.object_attribute_value oav
          JOIN shrapnel.field f ON f.id = oav.field_id
          WHERE oav.object_id = v_obj.oid
            AND f.id = sf.field_id
        )
    ) THEN
      v_missing := v_missing + 1;
      CONTINUE;
    END IF;

    -- Conformance evidence (idempotent): shell value + value_string + OAV.
    IF NOT EXISTS (
      SELECT 1 FROM shrapnel.object_attribute_value oav
      WHERE oav.object_id = v_obj.oid AND oav.field_id = v_field_conf
    ) THEN
      INSERT INTO shrapnel.value (value_type_code) VALUES (2)
        RETURNING id INTO v_value_id;
      INSERT INTO shrapnel.value_string (id, value) VALUES (v_value_id, 'conformant');
      INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
        VALUES (v_obj.oid, v_field_conf, v_value_id);
      v_evidence := v_evidence + 1;
    END IF;

    -- Classification (idempotent; evidence-gate trigger enforces the fact).
    IF (SELECT stereotype_revision_id FROM shrapnel.object_instance
        WHERE id = v_obj.oid) IS NULL THEN
      UPDATE shrapnel.object_instance
         SET stereotype_revision_id = v_revid,
             stereotype_id          = v_stereoid
       WHERE id = v_obj.oid;
      v_membered := v_membered + 1;
    ELSE
      v_skipped := v_skipped + 1;
    END IF;
  END LOOP;

  RAISE NOTICE 'backfill: concept_import_state v1 — % evidence rows written, % objects classified, % already classified, % objects missing required members',
    v_evidence, v_membered, v_skipped, v_missing;
END;
$backfill$;

COMMIT;
