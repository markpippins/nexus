-- ============================================================================
-- 0008_stereotype_reconcile.sql
--
-- Idempotent catalog reconciliation (plan 8261660, steps 1-2).
--
-- The #591 emitter compiles a TypeSpec program into an ordered migration of
-- shrapnel.stereotype_create_revision(...) calls. Because the catalog is
-- append-only by revision, re-applying that migration mints a new revision
-- every time. This migration adds the reconcile verb: the same declared
-- state, re-applied, writes nothing.
--
-- DESIGN — why the decision lives here and not in the emitter
-- -----------------------------------------------------------
-- The contract fingerprint is computed SERVER-side by
-- stereotype_canonical_contract (0005 §0a), which #591 deliberately did not
-- reimplement client-side. If idempotency were an emitter concern, the emitter
-- would have to compute a fingerprint to decide whether to emit anything,
-- walking straight back into that reimplementation. So the emitter never learns
-- what a fingerprint is; it emits a different function name and the database
-- decides. The comparison is exact and single-sourced because it calls the
-- very function that will build the fingerprint if a write is warranted.
--
-- DESIGN — why this is a NEW function and not a flag on create_revision
-- ----------------------------------------------------------------------
-- stereotype_create_revision's RETURN VALUE IS A PARENT POINTER. Callers
-- capture it and feed it straight back in as the child's p_extends_revision
-- (see api_check_stereotype.sql: `... AS base_rev \gset` then `:base_rev`;
-- the emitter emits the same shape as a stereotype_resolve() subselect).
-- Its contract is "returns the revision I just minted." If the same function
-- also returned "whatever the head happens to be" on a no-op, that pointer
-- would mean two different things depending on internal state, and in a
-- partially-populated database a caller would silently parent a fresh chain
-- onto a pre-existing revision at the wrong depth — while believing it had
-- built a new chain. Keeping the verbs separate keeps that contract crisp:
-- create_revision always mints, reconcile returns (revision_id, created) and
-- says which it did.
--
-- It also keeps create_revision's observable behavior byte-identical, so
-- every migration already committed on main replays exactly as written.
--
-- SCOPE — reconciliation converges declared state; it never removes
-- ----------------------------------------------------------------
-- Dropping a stereotype from the TypeSpec source and re-running leaves the
-- stereotype in the catalog. There is no tombstone and no retire verb here.
-- That is deliberate, and the gap is named rather than papered over.
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Shared field-document construction
--
-- Extracted verbatim from stereotype_create_revision's field loop (0005 §6)
-- so that the reconciliation decision and the write read the SAME field
-- document. Two copies of this loop would be free to drift, and a no-op
-- verdict computed against different inputs than the ones that would have
-- been written is precisely the silent-skip failure this feature must not
-- have.
--
-- p_error_prefix exists so create_revision keeps its EXACT original error
-- text while reconcile reports itself. Behavior of create_revision, including
-- the wording of every RAISE, is unchanged.
--
-- Side effect, by design and unchanged from 0005: an unknown property_name is
-- get-or-created into shrapnel.field (default String, type code 2).
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_fields_document_for(
  p_required_fields  text[],
  p_optional_fields  text[],
  p_error_prefix     text DEFAULT 'stereotype_create_revision'
)
RETURNS jsonb
LANGUAGE plpgsql
AS $function$
DECLARE
  v_fields jsonb := '[]'::jsonb;
  v_fid    bigint;
  v_seen   text[] := ARRAY[]::text[];
  t        record;
BEGIN
  FOR t IN
    SELECT pn, true AS req FROM unnest(coalesce(p_required_fields, ARRAY[]::text[])) pn
    UNION ALL
    SELECT pn, false FROM unnest(coalesce(p_optional_fields, ARRAY[]::text[])) pn
  LOOP
    IF t.pn = ANY (v_seen) THEN
      RAISE EXCEPTION '%: field % declared more than once', p_error_prefix, t.pn;
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

  RETURN v_fields;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_fields_document_for(text[], text[], text) IS
  'Build the v2 fingerprint field document [{"id","required"}] from property '
  'names, get-or-creating unknown fields. Shared by create_revision and '
  'reconcile so the no-op verdict and the write read identical inputs.';


-- ────────────────────────────────────────────────────────────────────────────
-- 2. create_revision, re-issued against the shared helper
--
-- CREATE OR REPLACE keeps the OID, so every existing dependency and the
-- ci-bootstrap dump shape are unaffected. The ONLY change from 0005 §6 is that
-- the field loop now delegates to the helper. Validation order, the C1
-- rationale guard, the parent lookup, the next-version computation
-- (uq_sterev_identity_version still arbitrates races), the server-computed v2
-- fingerprint, and every error message are byte-for-byte as they were.
--
-- This is a REPLACE, not an edit of 0005: 0005 is already applied wherever the
-- ledger has run, so a corrected 0005 would never re-execute.
-- ────────────────────────────────────────────────────────────────────────────
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
  v_fields               jsonb;
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

  -- Fields: get-or-create by property_name, then build the full v2 fields
  -- document. Same shared helper the reconcile verdict is computed from.
  v_fields := shrapnel.stereotype_fields_document_for(
    p_required_fields, p_optional_fields, 'stereotype_create_revision');

  -- Next version (uq_sterev_identity_version arbitrates concurrent races).
  SELECT coalesce(max(version), 0) + 1 INTO v_version
  FROM shrapnel.stereotype_revision
  WHERE stereotype_id = v_stereotype_id;

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


-- ────────────────────────────────────────────────────────────────────────────
-- 3. stereotype_reconcile: the idempotent verb
--
-- Same argument shape as create_revision. Returns the resulting head revision
-- id plus whether this call CREATED it (true) or found it already current
-- (false). Callers that want idempotence MUST read `created` rather than
-- assuming a returned id means a row was written.
--
-- The no-op decision and the write:
--   * the candidate fingerprint is computed by calling the SAME
--     stereotype_canonical_contract that create_revision will call, over a
--     field document built by the SAME shared helper;
--   * on any divergence the write is delegated to create_revision itself, so
--     reconcile can never write a row that create_revision would not.
--
-- Idempotence is exact, not heuristic: it holds when the declared state AND
-- its whole upstream closure are unchanged. Because p_extends_revision is
-- inside the fingerprint, a new parent head legitimately mints a child
-- revision even when the child's own declared fields are byte-identical —
-- superset and depth conformance are parent-relative, so the child must
-- re-derive against the new head. Emitting topological order is load-bearing
-- for correctness here, not merely tidy.
--
-- The no-op branch is NOT covered by the deferred trigger apparatus: the
-- fingerprint, superset-v2, C1 depth and field-integrity triggers are all
-- CONSTRAINT triggers that fire at COMMIT over WRITTEN ROWS, and a no-op
-- writes none. The `created` flag is therefore not a diagnostic nicety — it
-- is the only signal distinguishing "correctly skipped" from "silently wrong",
-- and callers should surface it.
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_reconcile(
  p_name             text,
  p_extends_revision bigint,
  p_rationale        text,
  p_required_fields  text[],
  p_optional_fields  text[] DEFAULT NULL
)
RETURNS TABLE(revision_id bigint, created boolean)
LANGUAGE plpgsql
AS $function$
DECLARE
  v_stereotype_id        bigint;
  v_parent_stereotype_id bigint;
  v_parent_depth         integer;
  v_fields               jsonb;
  v_candidate            text;
  v_head                 bigint;
  v_head_fingerprint     text;
BEGIN
  IF p_name IS NULL OR btrim(p_name) = '' THEN
    RAISE EXCEPTION 'stereotype_reconcile: name is required';
  END IF;

  SELECT id INTO v_stereotype_id FROM shrapnel.stereotype WHERE name = p_name;

  -- C1 guard, applied before any write decision so a malformed call cannot
  -- reach the fingerprint comparison.
  IF p_extends_revision IS NOT NULL THEN
    IF p_rationale IS NULL OR btrim(p_rationale) = '' THEN
      RAISE EXCEPTION 'stereotype_reconcile: extends requires a rationale (C1 shallow-hierarchy doctrine)';
    END IF;
    SELECT stereotype_id, depth INTO v_parent_stereotype_id, v_parent_depth
    FROM shrapnel.stereotype_revision
    WHERE id = p_extends_revision;
    IF v_parent_stereotype_id IS NULL THEN
      RAISE EXCEPTION 'stereotype_reconcile: parent revision % not found', p_extends_revision;
    END IF;
  END IF;

  -- Build the field document through the shared helper. This get-or-creates
  -- any unknown field, exactly as create_revision would. For a genuine re-apply
  -- every field already exists, so a no-op run inserts zero field rows — an
  -- invariant the 0008 checks assert directly.
  v_fields := shrapnel.stereotype_fields_document_for(
    p_required_fields, p_optional_fields, 'stereotype_reconcile');

  -- The decision. Only meaningful for a stereotype that already exists.
  IF v_stereotype_id IS NOT NULL THEN
    SELECT r.head_revision_id INTO v_head
    FROM shrapnel.stereotype_resolve(p_name) r;

    IF v_head IS NOT NULL THEN
      v_candidate := shrapnel.stereotype_canonical_contract(
        v_stereotype_id, p_extends_revision, p_rationale, v_fields);

      SELECT contract_fingerprint INTO v_head_fingerprint
      FROM shrapnel.stereotype_revision WHERE id = v_head;

      IF v_head_fingerprint = v_candidate THEN
        RETURN QUERY SELECT v_head, false;
        RETURN;
      END IF;
    END IF;
  END IF;

  -- Divergence, or a first apply. Hand the write to create_revision so the
  -- minting path is literally the same function, unchanged, including its
  -- version allocation and server-computed fingerprint.
  RETURN QUERY
    SELECT shrapnel.stereotype_create_revision(
             p_name, p_extends_revision, p_rationale,
             p_required_fields, p_optional_fields),
           true;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_reconcile(text, bigint, text, text[], text[]) IS
  'Idempotent catalog reconciliation. Returns (revision_id, created): created '
  'is false when the declared state already matches the head revision and '
  'nothing was written. Never removes a stereotype; there is no retire verb.';
