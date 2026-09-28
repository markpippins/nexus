-- ============================================================================
-- 0009_stereotype_field_freeze_subtransaction.sql
--
-- Fixes a defect in 0005 §0c (forbid_stereotype_field_mutation): the freeze
-- check misidentifies a revision that the CURRENT transaction is still
-- building as already frozen, whenever the revision is created inside a
-- plpgsql subtransaction.
--
-- THE DEFECT
-- 0005 decides a revision is frozen with:
--
--   r.xmin::text::bigint <> (txid_current() % 4294967296)::bigint
--
-- That is xid arithmetic: it asks "is the row's inserting xid numerically
-- different from my transaction's xid?". A row written inside a plpgsql
-- subtransaction is stamped with the SUBtransaction's xid, while
-- txid_current() returns the TOP-LEVEL transaction's xid. They differ even
-- though both belong to the same, still-open transaction. Measured on a
-- postgres:17 container:
--
--   xmin = 782   txid_current() = 781   -> 0005 says "frozen"
--
-- Observable consequence: stereotype_create_revision CANNOT be called from
-- inside a plpgsql EXCEPTION block. It raises
--
--   stereotype_field rows for revision N are frozen (append-only contract)
--
-- and — because the caller is very likely inside an exception handler — the
-- revision is silently never created. Any server code wrapping a catalog write
-- in a retry or error handler loses the write. This was found while building
-- migration 0008 and verified against 0005's ORIGINAL, untouched function, so
-- it predates every change made since.
--
-- It is a hard prerequisite for the retire/tombstone work (plan 8261661): that
-- design's refuse/rollback logic is precisely where a plpgsql EXCEPTION block
-- would be used, so every function there would inherit this trap.
--
-- THE FIX
-- Ask the SEMANTIC question instead of doing arithmetic: is the revision's
-- inserting transaction still in progress? `pg_xact_status` reports the real
-- status and is correct for subtransaction xids by construction:
--
--   own top-level or subtransaction xid  -> 'in progress'   (not frozen)
--   any committed transaction            -> 'committed'    (frozen)
--   FrozenTransactionId (autovacuum)     -> 'committed'    (frozen, correct)
--
-- Fail-closed: the row is allowed ONLY on an explicit 'in progress'. NULL (an
-- xid outside the wraparound horizon) and every other value are treated as
-- frozen, so an unrecognised state rejects rather than permits. For a freeze
-- guard, that is the correct direction to be wrong in.
--
-- WHAT THIS DOES NOT WEAKEN
-- The append-only contract is unchanged. A revision that has been COMMITTED is
-- still frozen: post-commit required-flag flips, field-row deletion, and
-- post-commit contract growth are all still rejected. The error message and
-- SQLSTATE (23514) are byte-identical, so negative_path_check_stereotype.sql
-- Case 5b and any external grep on the message keep working.
--
-- It is a REPLACE, not an edit of 0005: 0005 is already applied wherever the
-- ledger has run, so a corrected 0005 would never re-execute.
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. The rule, named and reusable
--
-- Extracted because it is a contract in its own right, not an implementation
-- detail of a trigger: "is this revision still under construction?" is the
-- question the retire/tombstone design also needs (a live parent cannot be
-- retired; a revision retired at a given head must be pinnable to that head).
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.stereotype_revision_under_construction(
  p_revision_id bigint
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $function$
  -- Fail-closed: TRUE only on an explicit 'in progress'. A missing revision,
  -- a NULL status (xid outside the wraparound horizon), or any unrecognised
  -- value all answer FALSE, i.e. "treat it as frozen".
  SELECT EXISTS (
    SELECT 1
    FROM shrapnel.stereotype_revision r
    WHERE r.id = p_revision_id
      AND pg_xact_status(r.xmin::text::xid8) = 'in progress'
  );
$function$;

COMMENT ON FUNCTION shrapnel.stereotype_revision_under_construction(bigint) IS
  'TRUE only while p_revision_id''s inserting transaction is still in progress '
  '(top-level or subtransaction). FALSE for any committed revision, for a '
  'vacuum-frozen row, and for any unrecognised xid state (fail-closed).';


-- ────────────────────────────────────────────────────────────────────────────
-- 2. The corrected trigger function
-- ────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION shrapnel.forbid_stereotype_field_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  v_revision_id bigint;
BEGIN
  v_revision_id := COALESCE(OLD.stereotype_revision_id, NEW.stereotype_revision_id);

  -- Reject unless the revision is provably still under construction by us.
  -- Note that stereotype_field.stereotype_revision_id is a NOT NULL REFERENCES
  -- to stereotype_revision(id), so a field row can never exist without its
  -- revision: the "revision not found" case is unreachable, and the
  -- fail-closed reading of an unknown state costs nothing.
  IF NOT shrapnel.stereotype_revision_under_construction(v_revision_id) THEN
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

COMMENT ON FUNCTION shrapnel.forbid_stereotype_field_mutation() IS
  'Freezes stereotype_field rows of any revision whose inserting transaction '
  'has committed. 0005 used xid arithmetic, which falsely froze revisions '
  'created inside a plpgsql subtransaction (see 0009 header); the rule is now '
  'stated semantically via stereotype_revision_under_construction().';
