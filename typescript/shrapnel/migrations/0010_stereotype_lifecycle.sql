-- ============================================================================
-- 0010_stereotype_lifecycle.sql
--
-- Retire / tombstone verb for the shrapnel EAV catalog (plan 8261661,
-- completing plan 8261660).
--
-- NUMBERING
-- ---------
-- The design record ("Design: retire/tombstone verb for the shrapnel EAV
-- catalog", agent record cdeed033) called this file 0009. That number was
-- consumed by the hard prerequisite it names in §8: the 0005 subtransaction
-- freeze defect, fixed in 0009_stereotype_field_freeze_subtransaction.sql
-- (PR #628). The verb therefore lands as 0010. Nothing is renumbered: a
-- migration that has been applied is never rewritten.
--
-- THE GAP THIS CLOSES
-- -------------------
-- 0008's stereotype_reconcile converges DECLARED state but never removes a
-- stereotype. Drop one from the TypeSpec source and it lingers forever.
--
-- Retirement CANNOT be a revision. A retirement revision would carry the same
-- fields, parent and rationale as its head, so stereotype_canonical_contract
-- would return the same contract_fingerprint. Folding a `retired` flag into
-- the fingerprint would invalidate every committed fingerprint (Mongo
-- schema_fingerprint values, committed evidence SQL). Keeping the fingerprint
-- honest would make reconcile no-op on the retirement — invisible — and would
-- make un-retiring impossible, because re-adding the name produces an
-- identical fingerprint.
--
-- So retirement is LIFECYCLE, not contract: an append-only event log beside
-- the immutable revision chain, touching no revision and no fingerprint.
--
-- THE MODEL
-- ---------
--   active ──► deprecated ──► retired
--      ▲            │             │
--      └────────────┴─────────────┘        (reactivation)
--
--   deprecated = forward gate only: reconcile still works, object_classify
--                still works (and reports lifecycle_state), existing
--                instances and history are untouched.
--   retired    = stereotype_reconcile and object_classify REFUSE.
--
-- ABSENCE NEVER DESTROYS
-- ----------------------
-- Retirement requires a POSITIVE @retired annotation in the source. A
-- stereotype owned by a source but absent from its declared catalog is
-- reported as `orphan_candidate` and NEVER acted on. CI can fail the build on
-- an unannotated orphan without destroying anything. Destruction requires
-- three independent things: an annotation in the source, a reviewed plan, and
-- a two-phase apply.
--
-- OWNERSHIP
-- ---------
-- A "retire everything I did not declare" verb would, on first run, take out
-- every hand-authored stereotype plus 0007's WorkRequest. Retirement is
-- therefore scoped to the source that created the stereotype
-- (stereotype_source_owner). First claim wins; ownership cannot be
-- transferred. Reconcile/create of an unowned name is still allowed — only
-- lifecycle transitions require ownership.
--
-- TWO PHASES
-- ----------
--   stereotype_lifecycle_plan(...)   pure read; returns actions + plan_token
--   stereotype_lifecycle_report(...) alias of plan, matching the design
--                                    record's report/apply naming
--   stereotype_lifecycle_apply(...)  applies, but only if the token still
--                                    matches the recomputed plan
--
-- The token is a sha256 over the whole plan document INCLUDING the observed
-- head revision id/version/fingerprint of every named stereotype. A plan
-- approved against a catalog that has since moved is refused. A hash is not
-- invertible, so apply is given the plan inputs as well as the token; it
-- recomputes the token from those inputs against the CURRENT catalog and
-- refuses on any difference. The token is the approval, not the input.
--
-- DB REFUSES, NOT THE CALLER
-- --------------------------
-- The lifecycle validation trigger (not only the apply function) enforces:
--   * from_state equals the stereotype's current state;
--   * only the four legal transitions;
--   * a non-empty reason (a tombstone without a reason cannot be justified);
--   * retirement pins at_revision_id (the observed head);
--   * retirement requires the source to own the stereotype;
--   * retirement is refused while any live (non-retired) child transitively
--     extends it. Apply emits retirements in reverse-topological order, but
--     the DB enforces the order rather than trusting it.
--
-- WHAT THIS DOES NOT DO
-- ---------------------
--   * No hard delete: the stereotype, its revisions and its history remain.
--   * No cascade: children are retired explicitly, children first.
--   * No retroactive un-classification: existing instances keep their
--     classification forever.
--   * No inference from absence, ever.
--   * No change to stereotype_create_revision. The two write-path guards are
--     additive to stereotype_reconcile and object_classify and are no-ops
--     until something is actually retired.
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. The append-only lifecycle event log
-- ────────────────────────────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS shrapnel.stereotype_lifecycle_event_seq;

CREATE TABLE IF NOT EXISTS shrapnel.stereotype_lifecycle_event (
    event_id       bigint PRIMARY KEY
                     DEFAULT nextval('shrapnel.stereotype_lifecycle_event_seq'::regclass),
    stereotype_id  bigint NOT NULL REFERENCES shrapnel.stereotype(id),
    from_state     text   NOT NULL,
    to_state       text   NOT NULL,
    reason         text   NOT NULL,
    source         text   NOT NULL,
    at_revision_id bigint REFERENCES shrapnel.stereotype_revision(id),
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_sterev_lifecycle_states
      CHECK (from_state IN ('active', 'deprecated', 'retired')
         AND to_state   IN ('active', 'deprecated', 'retired')),
    CONSTRAINT ck_sterev_lifecycle_transition
      CHECK ((from_state, to_state) IN (
        ('active',     'deprecated'),
        ('deprecated', 'active'),
        ('deprecated', 'retired'),
        ('retired',    'active')
      )),
    CONSTRAINT ck_sterev_lifecycle_reason
      CHECK (btrim(reason) <> ''),
    CONSTRAINT ck_sterev_lifecycle_source
      CHECK (btrim(source) <> ''),
    CONSTRAINT ck_sterev_lifecycle_retired_head
      CHECK (to_state <> 'retired' OR at_revision_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_sterev_lifecycle_current
  ON shrapnel.stereotype_lifecycle_event (stereotype_id, event_id DESC);

COMMENT ON TABLE shrapnel.stereotype_lifecycle_event IS
  'Append-only lifecycle log for shrapnel stereotypes. Current state is the '
  'newest event; reversal is another insert, never an erase. Touches no '
  'revision and no contract fingerprint.';

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Ownership: retirement is scoped to the source that created the name
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS shrapnel.stereotype_source_owner (
    source        text   NOT NULL,
    stereotype_id bigint NOT NULL REFERENCES shrapnel.stereotype(id),
    registered_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_stereotype_source_owner PRIMARY KEY (source, stereotype_id),
    -- At most one owner per stereotype: ownership is claimed on first
    -- successful reconcile and can never be transferred.
    CONSTRAINT uq_stereotype_single_owner UNIQUE (stereotype_id),
    CONSTRAINT ck_stereotype_owner_source CHECK (btrim(source) <> '')
);

COMMENT ON TABLE shrapnel.stereotype_source_owner IS
  'Which emitter source created a stereotype. First claim wins; a second '
  'source can never claim the same stereotype. Scopes the retire verb so it '
  'cannot touch hand-authored entries or 0007 WorkRequest.';

-- ────────────────────────────────────────────────────────────────────────────
-- 3. Current state + ownership helpers
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_current_state(p_stereotype_id bigint)
RETURNS text
LANGUAGE sql
STABLE
AS $function$
  -- A stereotype with no event has never left the default state.
  SELECT coalesce(
    (SELECT e.to_state
       FROM shrapnel.stereotype_lifecycle_event e
      WHERE e.stereotype_id = p_stereotype_id
      ORDER BY e.event_id DESC
      LIMIT 1),
    'active');
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_current_state(bigint) IS
  'Current lifecycle state of a stereotype: the newest event''s to_state, or '
  '''active'' when no lifecycle event exists.';


CREATE OR REPLACE FUNCTION shrapnel.stereotype_claim_ownership(p_source text, p_name text)
RETURNS bigint
LANGUAGE plpgsql
AS $function$
DECLARE
  v_stereotype_id bigint;
  v_owner         text;
BEGIN
  IF p_source IS NULL OR btrim(p_source) = '' THEN
    RAISE EXCEPTION 'stereotype_claim_ownership: source is required';
  END IF;

  SELECT id INTO v_stereotype_id FROM shrapnel.stereotype WHERE name = p_name;
  IF v_stereotype_id IS NULL THEN
    RAISE EXCEPTION 'stereotype_claim_ownership: stereotype % not found; reconcile it before claiming ownership', p_name;
  END IF;

  INSERT INTO shrapnel.stereotype_source_owner (source, stereotype_id)
  VALUES (p_source, v_stereotype_id)
  ON CONFLICT (stereotype_id) DO NOTHING;

  SELECT source INTO v_owner
    FROM shrapnel.stereotype_source_owner
   WHERE stereotype_id = v_stereotype_id;

  IF v_owner IS DISTINCT FROM p_source THEN
    RAISE EXCEPTION 'stereotype_claim_ownership: stereotype % is already owned by source %; ownership cannot be transferred',
      p_name, v_owner USING ERRCODE = '23514';
  END IF;

  RETURN v_stereotype_id;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_claim_ownership(text, text) IS
  'Idempotently claim a stereotype for an emitter source. First claim wins; '
  'a second source is refused. Ownership gates every lifecycle transition.';


-- ────────────────────────────────────────────────────────────────────────────
-- 4. Append-only + semantic validation of lifecycle events
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.forbid_stereotype_lifecycle_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  RAISE EXCEPTION 'stereotype_lifecycle_event % is append-only; % rejected',
    COALESCE(OLD.event_id, NEW.event_id), TG_OP USING ERRCODE = '23514';
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_lifecycle_no_update
  ON shrapnel.stereotype_lifecycle_event;
CREATE TRIGGER trg_stereotype_lifecycle_no_update
  BEFORE UPDATE ON shrapnel.stereotype_lifecycle_event
  FOR EACH ROW EXECUTE FUNCTION shrapnel.forbid_stereotype_lifecycle_mutation();

DROP TRIGGER IF EXISTS trg_stereotype_lifecycle_no_delete
  ON shrapnel.stereotype_lifecycle_event;
CREATE TRIGGER trg_stereotype_lifecycle_no_delete
  BEFORE DELETE ON shrapnel.stereotype_lifecycle_event
  FOR EACH ROW EXECUTE FUNCTION shrapnel.forbid_stereotype_lifecycle_mutation();


CREATE OR REPLACE FUNCTION shrapnel.validate_stereotype_lifecycle_event()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_current       text;
  v_owner         text;
  v_parent_name   text;
  v_live_children text[];
BEGIN
  -- The event must start from the state the catalog is actually in. This is
  -- what makes a forged or stale event impossible, not merely impolite.
  v_current := shrapnel.stereotype_current_state(NEW.stereotype_id);
  IF NEW.from_state <> v_current THEN
    RAISE EXCEPTION 'stereotype_lifecycle_event: from_state % does not match current state % for stereotype %',
      NEW.from_state, v_current, NEW.stereotype_id USING ERRCODE = '23514';
  END IF;

  -- Lifecycle transitions are owner-scoped. This is the structural reason the
  -- verb can never touch a hand-authored entry or 0007's WorkRequest: they
  -- have no owner row.
  SELECT o.source INTO v_owner
    FROM shrapnel.stereotype_source_owner o
   WHERE o.stereotype_id = NEW.stereotype_id;
  IF v_owner IS NULL THEN
    RAISE EXCEPTION 'stereotype_lifecycle_event: stereotype % has no registered owner; lifecycle transitions are owner-scoped',
      NEW.stereotype_id USING ERRCODE = '23514';
  END IF;
  IF v_owner <> NEW.source THEN
    RAISE EXCEPTION 'stereotype_lifecycle_event: stereotype % is owned by source %, not %',
      NEW.stereotype_id, v_owner, NEW.source USING ERRCODE = '23514';
  END IF;

  IF NEW.to_state = 'retired' THEN
    -- A tombstone without a reason is a tombstone nobody can justify later.
    IF NEW.reason IS NULL OR btrim(NEW.reason) = '' THEN
      RAISE EXCEPTION 'stereotype_lifecycle_event: retirement of stereotype % requires a non-empty reason',
        NEW.stereotype_id USING ERRCODE = '23514';
    END IF;

    -- Pin the decision to the observed head.
    IF NEW.at_revision_id IS NULL THEN
      RAISE EXCEPTION 'stereotype_lifecycle_event: retirement of stereotype % must pin at_revision_id (the observed head)',
        NEW.stereotype_id USING ERRCODE = '23514';
    END IF;

    SELECT s.name INTO v_parent_name FROM shrapnel.stereotype s WHERE s.id = NEW.stereotype_id;

    -- Refuse retiring a live parent. Apply emits children first, but the DB
    -- enforces reverse-topological order rather than trusting the caller.
    SELECT array_agg(cs.name ORDER BY cs.name)
      INTO v_live_children
      FROM shrapnel.stereotype cs
      JOIN LATERAL shrapnel.stereotype_resolve(cs.name) cr ON true
     WHERE cs.id <> NEW.stereotype_id
       AND shrapnel.stereotype_current_state(cs.id) <> 'retired'
       AND shrapnel.stereotype_extends(cr.head_revision_id, v_parent_name);

    IF v_live_children IS NOT NULL THEN
      RAISE EXCEPTION 'stereotype_lifecycle_event: cannot retire % while live children % still extend it (retire children first)',
        v_parent_name, v_live_children USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_stereotype_lifecycle_validate
  ON shrapnel.stereotype_lifecycle_event;
CREATE TRIGGER trg_stereotype_lifecycle_validate
  BEFORE INSERT ON shrapnel.stereotype_lifecycle_event
  FOR EACH ROW EXECUTE FUNCTION shrapnel.validate_stereotype_lifecycle_event();


-- ────────────────────────────────────────────────────────────────────────────
-- 5. Phase 1: the plan (pure read; emits the plan token)
--
-- Actions:
--   create            declared, not in the catalog -> reconcile will create it
--   reconcile         declared active, already active -> normal reconcile
--   deprecate         declared @deprecated, currently active
--   retire            declared @retired; apply moves active->deprecated->retired
--   activate          declared active, currently deprecated/retired (reversal)
--   orphan_candidate  owned by the source but absent from the declared catalog
--                     — ADVISORY ONLY, never acted on
--   blocked           a destructive action the DB will refuse
--
-- p_declared is the full compiled catalog (every name the source declares).
-- p_retired / p_deprecated are the subsets carrying the annotations; they
-- must be subsets of p_declared. p_reasons maps name -> {reason, reference}.
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_lifecycle_plan(
  p_source     text,
  p_declared   text[],
  p_retired    text[] DEFAULT ARRAY[]::text[],
  p_deprecated text[] DEFAULT ARRAY[]::text[],
  p_reasons    jsonb  DEFAULT '{}'::jsonb
)
RETURNS TABLE(
  name             text,
  current_state    text,
  action           text,
  reason           text,
  blocked_by       text,
  head_revision_id bigint,
  plan_token       text
)
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
  v_declared   text[];
  v_retired    text[];
  v_deprecated text[];
BEGIN
  IF p_source IS NULL OR btrim(p_source) = '' THEN
    RAISE EXCEPTION 'stereotype_lifecycle_plan: source is required';
  END IF;
  IF p_reasons IS NOT NULL AND jsonb_typeof(p_reasons) <> 'object' THEN
    RAISE EXCEPTION 'stereotype_lifecycle_plan: p_reasons must be a JSON object keyed by stereotype name';
  END IF;

  -- Deduplicate and sort, so the token is stable under argument reordering.
  v_declared := ARRAY(
    SELECT DISTINCT n FROM unnest(coalesce(p_declared, ARRAY[]::text[])) AS u(n) ORDER BY n
  );
  v_retired := ARRAY(
    SELECT DISTINCT n FROM unnest(coalesce(p_retired, ARRAY[]::text[])) AS u(n) ORDER BY n
  );
  v_deprecated := ARRAY(
    SELECT DISTINCT n FROM unnest(coalesce(p_deprecated, ARRAY[]::text[])) AS u(n) ORDER BY n
  );

  -- An annotation on an absent name is not a retirement instruction. Absence
  -- is never sufficient; requiring the name to also be declared keeps that
  -- rule true at the API boundary as well as in the docs.
  IF EXISTS (
    SELECT 1 FROM unnest(v_retired) AS u(n) WHERE NOT (u.n = ANY (v_declared))
  ) THEN
    RAISE EXCEPTION 'stereotype_lifecycle_plan: every @retired name must also be declared (absent names are never retired)';
  END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(v_deprecated) AS u(n) WHERE NOT (u.n = ANY (v_declared))
  ) THEN
    RAISE EXCEPTION 'stereotype_lifecycle_plan: every @deprecated name must also be declared';
  END IF;
  IF EXISTS (
    SELECT 1 FROM unnest(v_retired) AS u(n) WHERE u.n = ANY (v_deprecated)
  ) THEN
    RAISE EXCEPTION 'stereotype_lifecycle_plan: a stereotype cannot be annotated both @retired and @deprecated';
  END IF;

  RETURN QUERY
  WITH names AS (
    -- Every name the source declares, plus every name the source already owns
    -- (the latter is how an orphan is discovered — from ownership, not from
    -- scanning the whole catalog, which would expose hand-authored entries).
    SELECT u.n AS name
      FROM unnest(v_declared) AS u(n)
    UNION
    SELECT s.name
      FROM shrapnel.stereotype_source_owner o
      JOIN shrapnel.stereotype s ON s.id = o.stereotype_id
     WHERE o.source = p_source
       AND NOT (s.name = ANY (v_declared))
       AND shrapnel.stereotype_current_state(s.id) <> 'retired'
  ),
  info AS (
    SELECT
      nm.name,
      s.id                               AS stereotype_id,
      r.head_revision_id,
      r.version                          AS head_version,
      hr.contract_fingerprint            AS head_fingerprint,
      shrapnel.stereotype_current_state(s.id) AS current_state,
      o.source                           AS owner_source,
      (nm.name = ANY (v_retired))        AS want_retired,
      (nm.name = ANY (v_deprecated))     AS want_deprecated,
      (nm.name = ANY (v_declared))       AS is_declared,
      nullif(btrim(coalesce(p_reasons -> nm.name ->> 'reason', '')), '')    AS ann_reason,
      nullif(btrim(coalesce(p_reasons -> nm.name ->> 'reference', '')), '') AS ann_reference,
      (
        -- Live children that are NOT themselves scheduled for retirement by
        -- this same source in this same plan. A whole subtree can therefore
        -- be retired in one reviewed plan; a child left live blocks the
        -- parent, and apply refuses the whole plan if any row is blocked.
        SELECT array_agg(cs.name ORDER BY cs.name)
          FROM shrapnel.stereotype cs
          JOIN LATERAL shrapnel.stereotype_resolve(cs.name) cr ON true
         WHERE s.id IS NOT NULL
           AND cs.id <> s.id
           AND shrapnel.stereotype_current_state(cs.id) <> 'retired'
           AND shrapnel.stereotype_extends(cr.head_revision_id, nm.name)
           AND NOT (
             cs.name = ANY (v_retired)
             AND EXISTS (
               SELECT 1 FROM shrapnel.stereotype_source_owner co
                WHERE co.stereotype_id = cs.id AND co.source = p_source
             )
           )
      ) AS live_children
    FROM names nm
    LEFT JOIN shrapnel.stereotype s
           ON s.name = nm.name
    LEFT JOIN LATERAL shrapnel.stereotype_resolve(nm.name) r ON true
    LEFT JOIN shrapnel.stereotype_revision hr ON hr.id = r.head_revision_id
    LEFT JOIN shrapnel.stereotype_source_owner o ON o.stereotype_id = s.id
  ),
  decided AS (
    SELECT
      i.*,
      CASE
        WHEN NOT i.is_declared THEN 'orphan_candidate'
        WHEN i.want_retired THEN
          CASE
            WHEN i.stereotype_id IS NULL                              THEN 'blocked'
            WHEN i.owner_source IS DISTINCT FROM p_source            THEN 'blocked'
            WHEN i.current_state = 'retired'                         THEN 'retire'
            WHEN i.ann_reason IS NULL                                THEN 'blocked'
            WHEN i.live_children IS NOT NULL                         THEN 'blocked'
            ELSE 'retire'
          END
        WHEN i.want_deprecated THEN
          CASE
            WHEN i.stereotype_id IS NULL                              THEN 'blocked'
            WHEN i.owner_source IS DISTINCT FROM p_source            THEN 'blocked'
            WHEN i.current_state = 'retired'                         THEN 'blocked'
            WHEN i.ann_reason IS NULL                                THEN 'blocked'
            ELSE 'deprecate'
          END
        WHEN i.stereotype_id IS NULL                                 THEN 'create'
        WHEN i.current_state IN ('deprecated', 'retired') THEN
          CASE
            WHEN i.owner_source IS DISTINCT FROM p_source            THEN 'blocked'
            ELSE 'activate'
          END
        ELSE 'reconcile'
      END AS action,
      CASE
        WHEN NOT i.is_declared THEN
          'owned by ' || p_source || ' but absent from the declared catalog; absence never retires'
        WHEN i.want_retired AND i.stereotype_id IS NULL THEN
          'annotated @retired but not present in the catalog'
        WHEN i.want_retired AND i.owner_source IS DISTINCT FROM p_source THEN
          'not owned by source ' || p_source
        WHEN i.want_retired AND i.current_state = 'retired' THEN
          'already retired; no transition'
        WHEN i.want_retired AND i.ann_reason IS NULL THEN
          '@retired requires a non-empty reason'
        WHEN i.want_retired AND i.live_children IS NOT NULL THEN
          'live children must be retired first'
        WHEN i.want_retired THEN
          i.ann_reason
        WHEN i.want_deprecated AND i.stereotype_id IS NULL THEN
          'annotated @deprecated but not present in the catalog'
        WHEN i.want_deprecated AND i.owner_source IS DISTINCT FROM p_source THEN
          'not owned by source ' || p_source
        WHEN i.want_deprecated AND i.current_state = 'retired' THEN
          'a retired stereotype cannot be deprecated'
        WHEN i.want_deprecated AND i.ann_reason IS NULL THEN
          '@deprecated requires a non-empty reason'
        WHEN i.want_deprecated THEN
          i.ann_reason
        WHEN i.stereotype_id IS NULL THEN
          'declared but not in the catalog; reconcile will create it'
        WHEN i.current_state IN ('deprecated', 'retired')
             AND i.owner_source IS DISTINCT FROM p_source THEN
          'not owned by source ' || p_source
        WHEN i.current_state IN ('deprecated', 'retired') THEN
          'declared active; reactivation restores the name'
        ELSE
          'declared state matches'
      END AS reason,
      CASE
        WHEN NOT i.is_declared THEN NULL
        WHEN i.want_retired AND i.stereotype_id IS NULL THEN 'not_in_catalog'
        WHEN i.want_retired AND i.owner_source IS DISTINCT FROM p_source THEN 'not_owned'
        WHEN i.want_retired AND i.ann_reason IS NULL THEN 'missing_reason'
        WHEN i.want_retired AND i.live_children IS NOT NULL
          THEN 'live_children:' || array_to_string(i.live_children, ',')
        WHEN i.want_deprecated AND i.stereotype_id IS NULL THEN 'not_in_catalog'
        WHEN i.want_deprecated AND i.owner_source IS DISTINCT FROM p_source THEN 'not_owned'
        WHEN i.want_deprecated AND i.current_state = 'retired' THEN 'retired'
        WHEN i.want_deprecated AND i.ann_reason IS NULL THEN 'missing_reason'
        WHEN NOT i.want_retired AND NOT i.want_deprecated
             AND i.stereotype_id IS NOT NULL
             AND i.current_state IN ('deprecated', 'retired')
             AND i.owner_source IS DISTINCT FROM p_source THEN 'not_owned'
        ELSE NULL
      END AS blocked_by
    FROM info i
  ),
  tokenized AS (
    SELECT
      'sha256:' || encode(
        sha256(
          convert_to(
            jsonb_build_object(
              'source',     p_source,
              'declared',   to_jsonb(v_declared),
              'retired',    to_jsonb(v_retired),
              'deprecated', to_jsonb(v_deprecated),
              'reasons',    coalesce(p_reasons, '{}'::jsonb),
              'rows', (
                SELECT jsonb_agg(
                         jsonb_build_object(
                           'name',             d.name,
                           'current_state',    d.current_state,
                           'action',           d.action,
                           'reason',           d.reason,
                           'blocked_by',       d.blocked_by,
                           'stereotype_id',    d.stereotype_id,
                           'head_revision_id', d.head_revision_id,
                           'head_version',     d.head_version,
                           'head_fingerprint', d.head_fingerprint
                         ) ORDER BY d.name)
                  FROM decided d
              )
            )::text,
            'UTF8'
          )
        ),
        'hex'
      ) AS plan_token
  )
  SELECT d.name, d.current_state, d.action, d.reason, d.blocked_by,
         d.head_revision_id, t.plan_token
    FROM decided d
    CROSS JOIN tokenized t
   ORDER BY d.name;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_lifecycle_plan(text, text[], text[], text[], jsonb) IS
  'Phase 1 of the retire/tombstone verb: a pure read that classifies every '
  'declared and owned stereotype and returns a plan_token bound to the '
  'observed head revisions. Absent names are orphan_candidate and are never '
  'acted on.';


-- The design record (cdeed033) calls phase 1 "report". Keep the name the
-- design and the CI contract use, as an alias of the implementation. It reads
-- exactly the same rows and the same token, so an approved report applies.
CREATE OR REPLACE FUNCTION shrapnel.stereotype_lifecycle_report(
  p_source     text,
  p_declared   text[],
  p_retired    text[] DEFAULT ARRAY[]::text[],
  p_deprecated text[] DEFAULT ARRAY[]::text[],
  p_reasons    jsonb  DEFAULT '{}'::jsonb
)
RETURNS TABLE(
  name             text,
  current_state    text,
  action           text,
  reason           text,
  blocked_by       text,
  head_revision_id bigint,
  plan_token       text
)
LANGUAGE sql
STABLE
AS $function$
  SELECT *
    FROM shrapnel.stereotype_lifecycle_plan(
           p_source, p_declared, p_retired, p_deprecated, p_reasons);
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_lifecycle_report(text, text[], text[], text[], jsonb) IS
  'Read-only alias of stereotype_lifecycle_plan, matching the design record''s '
  'report/apply naming. Safe for CI: it writes nothing and an unannotated '
  'orphan_candidate can fail a build without destroying anything.';


-- ────────────────────────────────────────────────────────────────────────────
-- 6. Phase 2: apply, bound to the phase-1 token
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_lifecycle_apply(
  p_source     text,
  p_plan_token text,
  p_declared   text[],
  p_retired    text[] DEFAULT ARRAY[]::text[],
  p_deprecated text[] DEFAULT ARRAY[]::text[],
  p_reasons    jsonb  DEFAULT '{}'::jsonb
)
RETURNS TABLE(name text, from_state text, to_state text)
LANGUAGE plpgsql
AS $function$
DECLARE
  v_expected text;
  v_blocked  text;
  r          record;
  v_sid      bigint;
  v_current  text;
  v_head     bigint;
  v_reason   text;
BEGIN
  IF p_plan_token IS NULL OR btrim(p_plan_token) = '' THEN
    RAISE EXCEPTION 'stereotype_lifecycle_apply: p_plan_token is required (two-phase apply)';
  END IF;

  -- Recompute against the CURRENT catalog. Any movement in a head revision,
  -- a fingerprint, an ownership row or a lifecycle state changes the token.
  SELECT p.plan_token INTO v_expected
    FROM shrapnel.stereotype_lifecycle_plan(
           p_source, p_declared, p_retired, p_deprecated, p_reasons) p
   LIMIT 1;

  IF v_expected IS DISTINCT FROM p_plan_token THEN
    RAISE EXCEPTION 'stereotype_lifecycle_apply: plan token is stale — the catalog changed after phase 1; re-run the report and review the new plan'
      USING ERRCODE = '40001';
  END IF;

  -- All-or-nothing: a blocked row means the plan is not safe to apply. This
  -- is deliberately stricter than skipping the blocked rows — a partially
  -- applied destructive plan is not a plan anyone reviewed.
  SELECT string_agg(p.name || ' (' || coalesce(p.blocked_by, 'blocked') || ')',
                    ', ' ORDER BY p.name)
    INTO v_blocked
    FROM shrapnel.stereotype_lifecycle_plan(
           p_source, p_declared, p_retired, p_deprecated, p_reasons) p
   WHERE p.action = 'blocked';

  IF v_blocked IS NOT NULL THEN
    RAISE EXCEPTION 'stereotype_lifecycle_apply: plan contains blocked actions: %', v_blocked;
  END IF;

  FOR r IN
    SELECT p.name, p.action
      FROM shrapnel.stereotype_lifecycle_plan(
             p_source, p_declared, p_retired, p_deprecated, p_reasons) p
     WHERE p.action IN ('deprecate', 'retire', 'activate')
     ORDER BY
       -- Retirements first, children before parents. The trigger re-checks
       -- live children, so this ordering is a convenience the DB verifies,
       -- not an assumption it trusts.
       CASE WHEN p.action = 'retire' THEN 0 ELSE 1 END,
       (SELECT rv.depth
          FROM shrapnel.stereotype_resolve(p.name) sr
          JOIN shrapnel.stereotype_revision rv ON rv.id = sr.head_revision_id)
         DESC NULLS LAST,
       p.name
  LOOP
    SELECT s.id INTO v_sid FROM shrapnel.stereotype s WHERE s.name = r.name;
    SELECT sr.head_revision_id INTO v_head
      FROM shrapnel.stereotype_resolve(r.name) sr;

    v_current := shrapnel.stereotype_current_state(v_sid);

    v_reason := nullif(btrim(coalesce(p_reasons -> r.name ->> 'reason', '')), '');
    IF v_reason IS NULL THEN
      -- Retirement and deprecation are destructive-ish forward gates: a
      -- tombstone or a stop-authoring decision without a reason is not
      -- auditable. Reactivation is a reversal that restores the declared
      -- state, so it gets a deterministic reason instead of blocking the
      -- whole plan over a name the caller did not annotate.
      IF r.action = 'activate' THEN
        v_reason := 'reactivated: declared active by source ' || p_source;
      ELSE
        RAISE EXCEPTION 'stereotype_lifecycle_apply: % requires a non-empty reason for action %',
          r.name, r.action;
      END IF;
    END IF;

    IF r.action = 'retire' THEN
      -- active -> retired is two events, because active -> retired is not a
      -- legal transition. The intermediate deprecation is real history, not
      -- a formality: it records that the name stopped being authored before
      -- it was tombstoned.
      IF v_current = 'active' THEN
        INSERT INTO shrapnel.stereotype_lifecycle_event
          (stereotype_id, from_state, to_state, reason, source, at_revision_id)
        VALUES (v_sid, 'active', 'deprecated', v_reason, p_source, v_head);
        name := r.name; from_state := 'active'; to_state := 'deprecated';
        RETURN NEXT;
        v_current := 'deprecated';
      END IF;

      IF v_current = 'deprecated' THEN
        INSERT INTO shrapnel.stereotype_lifecycle_event
          (stereotype_id, from_state, to_state, reason, source, at_revision_id)
        VALUES (v_sid, 'deprecated', 'retired', v_reason, p_source, v_head);
        name := r.name; from_state := 'deprecated'; to_state := 'retired';
        RETURN NEXT;
      END IF;

    ELSIF r.action = 'deprecate' THEN
      IF v_current = 'active' THEN
        INSERT INTO shrapnel.stereotype_lifecycle_event
          (stereotype_id, from_state, to_state, reason, source, at_revision_id)
        VALUES (v_sid, 'active', 'deprecated', v_reason, p_source, v_head);
        name := r.name; from_state := 'active'; to_state := 'deprecated';
        RETURN NEXT;
      END IF;

    ELSIF r.action = 'activate' THEN
      IF v_current IN ('deprecated', 'retired') THEN
        INSERT INTO shrapnel.stereotype_lifecycle_event
          (stereotype_id, from_state, to_state, reason, source, at_revision_id)
        VALUES (v_sid, v_current, 'active', v_reason, p_source, v_head);
        name := r.name; from_state := v_current; to_state := 'active';
        RETURN NEXT;
      END IF;
    END IF;
  END LOOP;

  RETURN;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_lifecycle_apply(text, text, text[], text[], text[], jsonb) IS
  'Phase 2 of the retire/tombstone verb. Refuses a stale plan token and any '
  'blocked row, then writes lifecycle events in reverse-topological order. '
  'Never deletes a stereotype, never cascades, never touches an instance.';


-- ────────────────────────────────────────────────────────────────────────────
-- 7. Ownership-claiming reconcile (the emitter path)
--
-- 0008's stereotype_reconcile stays byte-compatible (its signature and
-- behaviour are unchanged). This wrapper is the source-aware entry point: it
-- reconciles, then claims ownership on first success. Claiming after the
-- reconcile is what makes ownership mean "the source that created it".
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_reconcile_owned(
  p_source           text,
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
  v_revision bigint;
  v_created  boolean;
BEGIN
  SELECT r.revision_id, r.created INTO v_revision, v_created
    FROM shrapnel.stereotype_reconcile(
           p_name, p_extends_revision, p_rationale,
           p_required_fields, p_optional_fields) r;

  PERFORM shrapnel.stereotype_claim_ownership(p_source, p_name);

  RETURN QUERY SELECT v_revision, v_created;
END;
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_reconcile_owned(text, text, bigint, text, text[], text[]) IS
  'Source-aware reconcile: 0008 reconciliation followed by an idempotent '
  'ownership claim. The first source to reconcile a name owns it thereafter.';


-- ────────────────────────────────────────────────────────────────────────────
-- 8. Write-path guard 1: reconcile refuses a retired name
--
-- Re-issued from 0008 with the guard added. Everything else is byte-for-byte
-- the 0008 body; the guard is a no-op until something is actually retired.
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

  -- Retirement is a forward gate: reconcile never silently resurrects a
  -- retired name and never silently no-ops on it. Reactivate first.
  IF v_stereotype_id IS NOT NULL
     AND shrapnel.stereotype_current_state(v_stereotype_id) = 'retired' THEN
    RAISE EXCEPTION 'stereotype_reconcile: stereotype % is retired; reactivate it before reconciling (retirement is lifecycle, not contract)', p_name
      USING ERRCODE = '23514';
  END IF;

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


-- ────────────────────────────────────────────────────────────────────────────
-- 9. Write-path guard 2: object_classify refuses a retired stereotype
--
-- Re-issued from 0005 with the guard added. Deprecated stereotypes remain
-- usable (that is the whole point of the two-state model) and the returned
-- jsonb now carries lifecycle_state so callers can see it. Everything else is
-- the 0005 body, unchanged.
-- ────────────────────────────────────────────────────────────────────────────
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
  v_lifecycle     text;
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

  v_lifecycle := shrapnel.stereotype_current_state(v_stereotype_id);

  -- Retirement is the forward gate that makes retirement mean anything: no
  -- NEW instance may enter a retired stereotype. Existing instances keep
  -- their classification forever (no retroactive un-classification).
  IF v_lifecycle = 'retired' THEN
    RAISE EXCEPTION 'object_classify: stereotype % is retired; classification into a retired stereotype is refused', v_stereotype
      USING ERRCODE = '23514';
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
    'object_id',       p_object_id,
    'stereotype',      v_stereotype,
    'revision_id',     p_revision_id,
    'disposition',     p_disposition,
    'classified',      true,
    'lifecycle_state', v_lifecycle
  );
END;
$function$;


-- ────────────────────────────────────────────────────────────────────────────
-- 10. Read surface: current lifecycle state per stereotype
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW shrapnel.v_stereotype_lifecycle AS
SELECT s.id            AS stereotype_id,
       s.name,
       shrapnel.stereotype_current_state(s.id) AS current_state,
       o.source        AS owner_source,
       e.event_id      AS last_event_id,
       e.from_state    AS last_from_state,
       e.to_state      AS last_to_state,
       e.reason        AS last_reason,
       e.at_revision_id AS last_at_revision_id,
       e.created_at    AS last_changed_at
FROM shrapnel.stereotype s
LEFT JOIN shrapnel.stereotype_source_owner o ON o.stereotype_id = s.id
LEFT JOIN LATERAL (
  SELECT ev.* FROM shrapnel.stereotype_lifecycle_event ev
   WHERE ev.stereotype_id = s.id
   ORDER BY ev.event_id DESC
   LIMIT 1
) e ON true;

COMMENT ON VIEW shrapnel.v_stereotype_lifecycle IS
  'Current lifecycle state, owner and last transition for every stereotype.';
