-- V199: decision attribution for aspects.tag_binding (governance approval surface).
--
-- The G2 lifecycle (engineer record 7923c595) stored only the proposer
-- (bound_by); approving or rejecting a binding had nowhere to record WHO
-- decided, and update_binding_status's COALESCE overwrote the proposer
-- with the decider. This migration adds explicit decision attribution:
--
--   decided_by    who made the approve/reject decision (text, may be NULL
--                 only for proposed/expired rows)
--   decided_at    when the decision was made
--   decision_note free-text rationale (optional)
--
-- Decision coherence is enforced fail-closed:
--   - approved/rejected rows MUST carry decided_by AND decided_at
--   - proposed/expired rows MUST NOT carry either (expiry is lifecycle,
--     not a decision)
--
-- The single pre-existing approved row (e2e binding exercise, engineer
-- record 457a2eea) predates attribution; it is backfilled honestly as
-- decided_by='unknown:pre-attribution' with decided_at=created_at BEFORE
-- the coherence CHECK is added, so the constraint validates truthfully
-- rather than silently discarding history. Additive-only; no existing
-- column is modified. Matches binding_port.py's queries and the
-- TagBinding contract (typespec/v1/aspects/main.tsp).

ALTER TABLE aspects.tag_binding
    ADD COLUMN IF NOT EXISTS decided_by text,
    ADD COLUMN IF NOT EXISTS decided_at timestamptz,
    ADD COLUMN IF NOT EXISTS decision_note text;

-- Honest backfill for pre-attribution approved/rejected rows (if any).
UPDATE aspects.tag_binding
SET decided_by = 'unknown:pre-attribution',
    decided_at = created_at
WHERE status IN ('approved', 'rejected')
  AND decided_by IS NULL;

-- Decision coherence: a decision requires an actor and a timestamp.
--   approved/rejected -> always attributed
--   proposed          -> never attributed (no decision made yet)
--   expired           -> free: carries attribution IFF it was approved or
--                        rejected before expiring (binding_port preserves
--                        decision history across expiry; expiry itself is
--                        lifecycle, not a decision). Live-verified: the
--                        strict "expired must be unattributed" variant of
--                        this CHECK rejected the port's expiry UPDATE with
--                        CheckViolationError — caught by the constraint,
--                        fixed here (engineer record 7923c595 wave).
ALTER TABLE aspects.tag_binding ADD CONSTRAINT tag_binding_decision_coherence
    CHECK (
        (status IN ('approved', 'rejected')
             AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
        OR (status = 'proposed'
             AND decided_by IS NULL AND decided_at IS NULL)
        OR (status = 'expired')
    );
