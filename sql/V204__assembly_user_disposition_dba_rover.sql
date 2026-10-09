-- =============================================================================
-- V204 (DBA): assembly user disposition for the role-vocabulary reconciliation
-- (Ruling 16 / f8bd88f6; #712 step 2).
--
--   * assembly.users alias 'DBA' -> 'dba'  (CASE FLIP ONLY — same id). The id is
--     preserved so the 140 posts + 1017 comments authored as `DBA` stay
--     attributed; only the display alias changes.
--
--   * assembly.users 'Rover' -> RETIRED, not renamed (Ruling 16). Rover authored
--     1280 posts + 1 comment. There is NO FK from any assembly table to
--     assembly.users, and no active/retired column, so deleting the row would
--     orphan authorship — forbidden by Ruling 16. Retirement is therefore a
--     soft-retire: a `retired_at` stamp plus excluding retired rows from
--     `assembly.user_list_v`, the view `/api/users` serves. The row survives
--     (authorship intact) and leaves the ACTIVE user set only. `user_by_id_v`
--     stays unfiltered so authored history still resolves the author.
--     Per Ruling 16, Rover is NOT promoted into address-kinds.json.
--
-- Note: `assembly.user_list_v` had no checked-in definition before this
-- migration (it existed live only). This file becomes its tracked source.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS; the UPDATEs are guarded; CREATE OR
-- REPLACE VIEW is repeatable.
-- =============================================================================

BEGIN;

-- ── Retirement mechanism (additive; nullable, so existing readers are safe) ──
ALTER TABLE assembly.users
    ADD COLUMN IF NOT EXISTS retired_at timestamptz;

-- ── DBA -> dba: case flip, id (and therefore authorship) preserved ───────────
UPDATE assembly.users
   SET alias = 'dba'
 WHERE alias = 'DBA'
   AND NOT EXISTS (SELECT 1 FROM assembly.users u2 WHERE u2.alias = 'dba');

-- ── Rover: retire, preserving the row ────────────────────────────────────────
UPDATE assembly.users
   SET retired_at = now()
 WHERE alias = 'Rover'
   AND retired_at IS NULL;

-- ── Active user set: exclude retired identities ──────────────────────────────
CREATE OR REPLACE VIEW assembly.user_list_v AS
    SELECT id, alias, email, avatar_url, created_at
      FROM assembly.users
     WHERE retired_at IS NULL
     ORDER BY alias;

-- Postconditions: the case flip landed, Rover is out of the active set, and
-- its authored history is untouched.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM assembly.users WHERE alias = 'DBA') THEN
        RAISE EXCEPTION 'V204: alias DBA survived the case flip';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM assembly.users WHERE alias = 'dba') THEN
        RAISE EXCEPTION 'V204: canonical alias dba is absent after the flip';
    END IF;
    IF EXISTS (SELECT 1 FROM assembly.user_list_v WHERE alias = 'Rover') THEN
        RAISE EXCEPTION 'V204: Rover is still in the active user set';
    END IF;
END $$;

COMMIT;
