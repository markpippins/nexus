-- V182 — resolution.relation_vocabulary: governed relation-type vocabulary (pre-stage)
--
-- Freeze authority : ontologist freeze draft df6b70c4-ec1a-4d53-affe-79fcb33d48c9
--                    (status:proposed; binding 2026-09-24T23:59Z absent objection)
-- Proposal thread  : discussions b2c268b1-0882-4725-b527-407774342d2f
-- Pattern          : Lilac pre-stage — merged inert-by-construction; the FKs land here
--                    but a vocabulary row with zero consumers enforces nothing until
--                    existing columns carry values. **DO NOT APPLY TO LIVE until the
--                    freeze binds (2026-09-24) or DBA records early mechanism GO.**
-- Rulings honored  : R-1 fold has_dependency -> depends_on (expire + re-insert flipped)
--                    R-2 candidate_has_state_record expired (instance-level, T22 re-home)
--                    R-6 inverse_of populated only for symmetric contradicts
--                    R-7 FK by name on both relationship tables (NOT VALID here;
--                    VALIDATE CONSTRAINT is a follow-up after soak)
-- House style      : expire-not-delete, additive only; mirrors V172/V179 idioms;
--                    idempotent ON CONFLICT; no destructive DDL.
-- Replication      : R9 — confirm vanadium replication with operator at apply time.

-- ─────────────────────────────────────────────────────────────────────────────
-- Guard: refuse to run if a prior vocabulary FK already exists (re-run safety
-- without IF NOT EXISTS ambiguity). Pre-stage must be inert-by-construction:
-- if consumers appear before this migration, stop and re-assess.
-- ─────────────────────────────────────────────────────────────────────────────
DO $block$
DECLARE
  v_fk_count integer;
BEGIN
  SELECT count(*) INTO v_fk_count
    FROM pg_constraint
   WHERE conname IN ('concept_relationship_relationship_type_fk',
                     'representation_relationship_relationship_type_fk');

  IF v_fk_count > 0 THEN
    RAISE EXCEPTION 'V182 guard: vocabulary FK constraint(s) already exist (%) — pre-stage invariant violated', v_fk_count;
  END IF;
END
$block$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Vocabulary table
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resolution.relation_vocabulary (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL UNIQUE,
  definition  text NOT NULL,
  applies_to  text NOT NULL DEFAULT 'both'
              CHECK (applies_to IN ('concept', 'representation', 'both')),
  inverse_of  uuid REFERENCES resolution.relation_vocabulary(id)
              DEFERRABLE INITIALLY DEFERRED,
  ratified_by text,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expired_at  timestamptz
);

COMMENT ON TABLE  resolution.relation_vocabulary IS
  'Governed relation-type vocabulary for concept_relationship and representation_relationship (V182). Only ratified types are capturable as edges (FK by name, ruling R-7 of freeze draft df6b70c4). Expire-not-delete; new types enter via the insert ceremony with an ontologist freeze record.';
COMMENT ON COLUMN resolution.relation_vocabulary.name IS
  'Canonical snake_case relation-type name; the FK target for relationship_type columns.';
COMMENT ON COLUMN resolution.relation_vocabulary.definition IS
  'Binding X->Y reading, fixed by ontologist freeze.';
COMMENT ON COLUMN resolution.relation_vocabulary.applies_to IS
  'Edge-kind discriminator: concept, representation, or both.';
COMMENT ON COLUMN resolution.relation_vocabulary.inverse_of IS
  'Set only for symmetric types (R-6); no inverse-duplicate types are minted.';

-- One active row per name; history rows (expired_at set) may repeat the name.
CREATE UNIQUE INDEX IF NOT EXISTS relation_vocabulary_name_active_uidx
  ON resolution.relation_vocabulary (name)
  WHERE expired_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Seed — 23 types per freeze draft df6b70c4 (exact definitions; casing
--    matches live usage for the 7 pre-existing types)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO resolution.relation_vocabulary (name, definition, applies_to, ratified_by, notes)
VALUES
  -- Concept-level (18)
  ('produces',
   'Y is the governed output of X''s resolution stage; X remains intact and operative.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('transforms_into',
   'Y replaces X as the operative artifact in the next planned pipeline stage; X retained as provenance.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('basis_of',
   'Y''s content is grounded in X''s content; X is neither consumed nor replaced.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('depends_on',
   'X''s validity, existence, or correct functioning requires Y.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Sole dependency type (R-1: has_dependency folded; direction carries the reading).'),
  ('member_of',
   'X is a constituent part of composite Y.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Weakest entry (R-4): dropped before binding if redundant with the Requirement->Specification pipeline edge.'),
  ('provenance_of',
   'X attests to Y''s origin; X is immutable evidence for where Y came from.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Evidentiary backing — distinct from derives_from (R-3).'),
  ('spawns',
   'Y is a new, thereafter-independent artifact created as a direct product of processing X (e.g. requirement decomposition).',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('defines',
   'X is the authoritative specification of Y''s meaning or contract.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('implements',
   'X is a concrete realization satisfying Y''s contract.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('projects',
   'X is a derived, non-authoritative view or projection of canonical Y.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('derives_from',
   'Y was produced from X''s content (transformational descent).',
   'concept', 'ontologist freeze draft df6b70c4',
   'Transformational descent — distinct from provenance_of (R-3).'),
  ('validates',
   'X attests, checks, or evidences Y''s correctness.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('constrains',
   'X restricts Y''s legal states or admissible shapes.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('supersedes',
   'X replaces Y as the operative artifact in an unplanned, corrective capacity; Y retired-but-retained.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Corrective replacement — distinct from transforms_into planned stage promotion (R-5).'),
  ('contradicts',
   'X and Y make incompatible claims about the same subject.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Symmetric (R-6); inverse_of = self.'),
  ('supports',
   'X provides affirmative evidence for Y.',
   'concept', 'ontologist freeze draft df6b70c4', NULL),
  ('mentions',
   'X contains a bare reference to Y, asserting no relationship beyond the reference.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Expression graph projections of candidate links (E0).'),
  ('replies_to',
   'X is a discursive response to Y within a discussion topology.',
   'concept', 'ontologist freeze draft df6b70c4',
   'Expression E0 discussion/reply relations.'),
  -- Representation-level operational (5)
  ('calls',
   'X invokes Y at runtime.',
   'representation', 'ontologist freeze draft df6b70c4', NULL),
  ('consumes',
   'X uses Y as a data input (data-flow).',
   'representation', 'ontologist freeze draft df6b70c4', NULL),
  ('writes',
   'X produces persistent writes into Y.',
   'representation', 'ontologist freeze draft df6b70c4', NULL),
  ('reads',
   'X reads from Y at the storage level.',
   'representation', 'ontologist freeze draft df6b70c4',
   'Contrast consumes (data-flow vs storage-level read).'),
  ('uses',
   'X employs Y as a tool or dependency.',
   'representation', 'ontologist freeze draft df6b70c4',
   'Residual operational relation; prefer a more specific type when one exists.')
ON CONFLICT (name) DO UPDATE
  SET definition  = EXCLUDED.definition,
      applies_to  = EXCLUDED.applies_to,
      ratified_by = EXCLUDED.ratified_by,
      notes       = EXCLUDED.notes,
      expired_at  = NULL
  WHERE resolution.relation_vocabulary.expired_at IS NOT NULL;

-- R-6: contradicts is the only seeded symmetric type (self-inverse).
UPDATE resolution.relation_vocabulary v
   SET inverse_of = v.id
  FROM resolution.relation_vocabulary s
 WHERE s.name = 'contradicts'
   AND v.name = 'contradicts'
   AND v.inverse_of IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Edge reconciliation (R-1 / R-2) — expected live counts: 1 edge each.
--    WHERE EXISTS + stale-name guard keeps this safe if re-run after drift.
-- ─────────────────────────────────────────────────────────────────────────────
-- R-1: expire the has_dependency edge ...
UPDATE resolution.concept_relationship
   SET expired_at = now(),
       notes      = coalesce(notes || ' | ', '')
                    || 'R-1 fold (V182): has_dependency retired; superseded by depends_on with from/to flipped — see freeze draft df6b70c4'
 WHERE relationship_type = 'has_dependency'
   AND expired_at IS NULL
   AND EXISTS (SELECT 1 FROM resolution.concept_relationship live
               WHERE live.relationship_type = 'has_dependency' AND live.expired_at IS NULL);

-- ... and re-insert flipped as depends_on (append-only house style).
INSERT INTO resolution.concept_relationship
       (from_concept_id, to_concept_id, relationship_type, notes)
SELECT to_concept_id, from_concept_id, 'depends_on',
       'R-1 fold (V182): re-insert of has_dependency edge ' || old.id
         || ' with direction flipped — freeze draft df6b70c4'
  FROM resolution.concept_relationship old
 WHERE old.relationship_type = 'has_dependency'
   AND old.expired_at IS NOT NULL
   AND old.notes LIKE '%R-1 fold (V182)%'
   AND NOT EXISTS (
         SELECT 1 FROM resolution.concept_relationship dup
          WHERE dup.relationship_type = 'depends_on'
            AND dup.expired_at IS NULL
            AND dup.from_concept_id = old.to_concept_id
            AND dup.to_concept_id   = old.from_concept_id);

-- R-2: expire the candidate_has_state_record edge (instance-level lineage fact;
-- re-homed per the T22 lineage decision, to-do 692548fd).
UPDATE resolution.concept_relationship
   SET expired_at = now(),
       notes      = coalesce(notes || ' | ', '')
                    || 'R-2 (V182): instance-level lineage fact, not class-level vocabulary — re-homed per T22 (todo 692548fd)'
 WHERE relationship_type = 'candidate_has_state_record'
   AND expired_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. FKs (R-7) — by name, added NOT VALID here; VALIDATE CONSTRAINT is an
--    explicit follow-up after soak (kept out of this migration on purpose).
--    Naming per system convention: <table>_<column>_fk.
--    concept_relationship: with the legacy spellings reconciled in §3, every
--    active row satisfies the constraint. representation_relationship is NOT
--    reconciled here — its 3 active edges (derived/partial/legacy) are not in
--    the seeded vocabulary; NOT VALID keeps the pre-stage mergeable, and the
--    VALIDATE follow-up stays BLOCKED pending ontologist disposition of those
--    edges (architect inspection a81dc5f1; census corroborated 2026-09-18).
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE resolution.concept_relationship
  ADD CONSTRAINT concept_relationship_relationship_type_fk
  FOREIGN KEY (relationship_type)
  REFERENCES resolution.relation_vocabulary(name)
  NOT VALID;

ALTER TABLE resolution.representation_relationship
  ADD CONSTRAINT representation_relationship_relationship_type_fk
  FOREIGN KEY (relationship_type)
  REFERENCES resolution.relation_vocabulary(name)
  NOT VALID;

COMMENT ON CONSTRAINT concept_relationship_relationship_type_fk ON resolution.concept_relationship IS
  'R-7 (V182): only ratified vocabulary types are capturable as concept edges. NOT VALID at pre-stage — VALIDATE after soak.';
COMMENT ON CONSTRAINT representation_relationship_relationship_type_fk ON resolution.representation_relationship IS
  'R-7 (V182): only ratified vocabulary types are capturable as representation edges. NOT VALID at pre-stage — VALIDATE after soak.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Summary
-- ─────────────────────────────────────────────────────────────────────────────
DO $block$
DECLARE
  v_vocab    integer;
  v_legacy   integer;
  v_reinsert integer;
  v_expired  integer;
  v_folds    integer;
BEGIN
  SELECT count(*) INTO v_vocab FROM resolution.relation_vocabulary WHERE expired_at IS NULL;
  SELECT count(*) INTO v_legacy FROM resolution.concept_relationship WHERE relationship_type = 'has_dependency' AND expired_at IS NULL;
  SELECT count(*) INTO v_reinsert FROM resolution.concept_relationship
   WHERE relationship_type = 'depends_on' AND notes LIKE '%R-1 fold (V182)%' AND expired_at IS NULL;
  SELECT count(*) INTO v_expired FROM resolution.concept_relationship WHERE relationship_type = 'candidate_has_state_record' AND expired_at IS NULL;
  -- Folds available for re-insert (expired legacy rows marked by this migration).
  -- On a fresh bootstrap DB this is 0 (has_dependency never existed) — legitimate.
  SELECT count(*) INTO v_folds FROM resolution.concept_relationship old
   WHERE old.relationship_type = 'has_dependency'
     AND old.expired_at IS NOT NULL
     AND old.notes LIKE '%R-1 fold (V182)%';

  RAISE NOTICE 'V182 summary: % active vocabulary rows (expect 23); active has_dependency rows (expect 0): %; legacy folds: %; re-inserted depends_on rows: %; active candidate_has_state_record rows (expect 0): %',
    v_vocab, v_legacy, v_folds, v_reinsert, v_expired;

  -- Hard assertions: seed complete, both legacy types fully reconciled.
  -- reinsert < folds is LEGITIMATE when the flipped pair already exists as an
  -- active depends_on edge (dedupe guard): e.g. the live WR/WorkRequestEdge
  -- pair was double-entered in both directions; the fold collapses it to one.
  IF v_vocab <> 23 OR v_legacy <> 0 OR v_expired <> 0 THEN
    RAISE EXCEPTION 'V182 verify failed: vocabulary=% has_dependency=% candidate_state=%',
      v_vocab, v_legacy, v_expired;
  END IF;
  IF v_folds > 0 AND v_reinsert < v_folds THEN
    RAISE NOTICE 'V182: % fold(s) deduped against existing active depends_on edges (mirror already present) — no re-insert needed', v_folds - v_reinsert;
  END IF;
END
$block$;

-- ─────────────────────────────────────────────────────────────────────────────
-- APPENDIX (2026-09-18, engineer — append-only, non-executable commentary):
-- representation_relationship VALIDATE gate — pre-agreed disposition statements
-- for the 3 active non-vocabulary edges. §3 deliberately leaves
-- representation_relationship untouched (see §4 note); the binding disposition
-- of these edges is the ontologist's (governance I2 — architect inspection
-- a81dc5f1, ruling 46d45c77 condition 5). This appendix records the exact
-- statements to run in the V182 window ONCE the ontologist confirms the
-- mappings, so VALIDATE becomes one command with zero further engineering.
--
-- Live census (verified 2026-09-18, titanium AND vanadium — identical):
--   derived  1 edge : work_request table (resolution) -> vision.work_requests
--                     (LOSM satellite; edge notes: "tracking satellite, not a
--                     second source of truth")              -> PROJECTS
--   partial  1 edge : implementation_plan table (resolution) -> WRP DAG node
--                     (conduit; edge notes: "not full DAG structure; WRP is
--                     the fuller representation")            -> PROJECTS
--   legacy   1 edge : harvest.candidates embedded jsonb (dropped source,
--                     nebula.harvests_history) -> candidate table
--                     (resolution; notes: "Superseded by resolution.candidate
--                     as the single source of truth")        -> EXPIRE
--
-- (1) derived -> projects (X is a derived, non-authoritative view of Y):
--   UPDATE resolution.representation_relationship
--      SET relationship_type = 'projects',
--          notes = coalesce(notes || ' | ', '')
--                  || 'V182 appendix disposition: derived->projects (ontologist freeze df6b70c4)'
--    WHERE relationship_type = 'derived' AND expired_at IS NULL;
--
-- (2) partial -> projects (WRP is the fuller representation of the same
--     planning surface — the table is the narrower projection of it):
--   UPDATE resolution.representation_relationship
--      SET relationship_type = 'projects',
--          notes = coalesce(notes || ' | ', '')
--                  || 'V182 appendix disposition: partial->projects (ontologist freeze df6b70c4)'
--    WHERE relationship_type = 'partial' AND expired_at IS NULL;
--
-- (3) legacy -> expire (superseded source dropped; expire-not-delete):
--   UPDATE resolution.representation_relationship
--      SET expired_at = now(),
--          notes = coalesce(notes || ' | ', '')
--                  || 'V182 appendix disposition: legacy edge expired (ontologist freeze df6b70c4)'
--    WHERE relationship_type = 'legacy' AND expired_at IS NULL;
--
-- (4) then the gate this appendix unblocks:
--   ALTER TABLE resolution.representation_relationship
--     VALIDATE CONSTRAINT representation_relationship_relationship_type_fk;
--
-- IF THE ONTOLOGIST ALTERS A MAPPING: edit these recorded statements — do NOT
-- reconcile in §3 (R-1/R-2 scope is concept_relationship only).
-- ─────────────────────────────────────────────────────────────────────────────
