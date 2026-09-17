-- V178: tackle.role_memory integrity — no overlapping validity intervals
-- per (memory_id, role), with duplicate-drift repair and hard gates.
--
-- Motivation (2026-09-17, DBA R2 78a891ff + sql-pitfalls gotcha #8):
-- inserting the pr-protocol card's role assignments failed informatively —
-- ON CONFLICT was unusable because role_memory carries NO unique constraint
-- on (memory_id, role). The table's PK is a surrogate uuid; duplicates are
-- accepted silently (the same anti-pattern family as the V155 registry and
-- V175 roles_history incidents). Live does carry a PARTIAL unique
-- (uq_role_memory_active, WHERE expiration_dt IS NULL) — but it is absent
-- from the ci-bootstrap, covers only open rows, and says nothing about
-- overlapping closed/active intervals.
--
-- Design (V175 pattern adapted to interval semantics): role_memory IS an
-- assignment history — a role's assignment may expire and a successor may
-- begin. So the integrity rule is not "one row per (memory_id, role)" but
-- "no two rows for the same (memory_id, role) whose validity intervals
-- overlap". An EXCLUSION constraint (btree_gist over a tstzrange) enforces
-- exactly that, subsumes the partial unique, and still permits legitimate
-- close-then-reassign chains.
--
-- Steps: gate on target (GATE-001) and on unrepairable anomalous rows
-- (GATE-002: expiration_dt <= as_of_dt — an empty/inverted interval that
-- the constraint cannot see), dedupe drift (keep-oldest per overlapping
-- group), install the exclusion constraint, hard verify gate (GATE-003).
-- Idempotent. No data movement beyond dedupe, no grants, no restart.
--
-- Out of scope (separate candidate V179): reconciling tackle.roles with
-- nebula.roles (parallel registries, casing drift, residue rows).

BEGIN;

-- ── 1. Gate ─────────────────────────────────────────────────────────────
DO $gate$
BEGIN
  IF to_regclass('tackle.role_memory') IS NULL THEN
    RAISE EXCEPTION 'V178-GATE-001: tackle.role_memory does not exist';
  END IF;

  IF EXISTS (
    SELECT 1 FROM tackle.role_memory
     WHERE expiration_dt IS NOT NULL AND expiration_dt <= as_of_dt
  ) THEN
    RAISE EXCEPTION 'V178-GATE-002: rows with expiration_dt <= as_of_dt (inverted/empty intervals) exist — repair semantics are ambiguous, resolve manually before applying V178';
  END IF;
END
$gate$;

-- ── 2. Duplicate-drift repair: keep-oldest per overlapping group ────────
-- Two rows "overlap" when same (memory_id, role) AND their [as_of, exp)
-- intervals intersect (NULL exp = open-ended = infinity). For each
-- overlapping pair, delete the newer row (created_at, then id tiebreak).
-- Loop-free formulation: delete a when some OLDER b overlaps it.
DELETE FROM tackle.role_memory a
 USING tackle.role_memory b
 WHERE a.memory_id = b.memory_id
   AND a.role      = b.role
   AND a.id       <> b.id
   AND (b.created_at < a.created_at
        OR (b.created_at = a.created_at AND b.id < a.id))
   AND tstzrange(b.as_of_dt, COALESCE(b.expiration_dt, 'infinity'::timestamptz), '[)')
       &&
       tstzrange(a.as_of_dt, COALESCE(a.expiration_dt, 'infinity'::timestamptz), '[)');

-- ── 3. The constraint ───────────────────────────────────────────────────
-- btree_gist is a trusted extension (PG13+): a database owner can create
-- it. IF NOT EXISTS keeps this idempotent.
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE tackle.role_memory
  DROP CONSTRAINT IF EXISTS uq_role_memory_validity;

ALTER TABLE tackle.role_memory
  ADD CONSTRAINT uq_role_memory_validity
  EXCLUDE USING gist (
    memory_id WITH =,
    role      WITH =,
    tstzrange(as_of_dt,
              COALESCE(expiration_dt, 'infinity'::timestamptz),
              '[)') WITH &&
  );

-- ── 4. Postcondition ────────────────────────────────────────────────────
DO $verify$
DECLARE v_count integer;
BEGIN
  SELECT count(*) INTO v_count FROM pg_constraint
   WHERE conrelid = 'tackle.role_memory'::regclass
     AND conname  = 'uq_role_memory_validity'
     AND contype  = 'x';
  IF v_count <> 1 THEN
    RAISE EXCEPTION 'V178-GATE-003: exclusion constraint uq_role_memory_validity not present after apply';
  END IF;
  RAISE NOTICE 'V178: tackle.role_memory now forbids overlapping validity intervals per (memory_id, role) — duplicates and double-assignments refused at the door';
END
$verify$;

COMMIT;
