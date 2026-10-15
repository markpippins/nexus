-- 072 — durable requirement compilation receipts and idempotency reservations
--
-- Receipt rows are immutable evidence. Mutable request coordination lives in a
-- separate table so an idempotency key can be reserved before any plan effect.

BEGIN;

CREATE TABLE IF NOT EXISTS nebula.compilation_idempotency (
    idempotency_key text PRIMARY KEY,
    input_digest    text NOT NULL,
    state           text NOT NULL DEFAULT 'in_progress'
                    CHECK (state IN ('in_progress', 'completed')),
    response        jsonb,
    response_status integer,
    receipt_id      uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    CHECK ((state = 'in_progress' AND response IS NULL AND response_status IS NULL AND completed_at IS NULL)
        OR (state = 'completed' AND response IS NOT NULL AND response_status IS NOT NULL
            AND response_status BETWEEN 200 AND 599 AND completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS nebula.compilation_receipts (
    receipt_id      uuid PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    input_digest    text NOT NULL,
    requirement_id  text NOT NULL,
    outcome         text NOT NULL CHECK (outcome IN ('compiled', 'plan_created', 'partial', 'rejected', 'failed')),
    receipt         jsonb NOT NULL,
    occurred_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compilation_receipts_requirement
    ON nebula.compilation_receipts(requirement_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION nebula.reject_compilation_receipt_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'nebula.compilation_receipts is append-only; % is not permitted', TG_OP
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_compilation_receipts_immutable ON nebula.compilation_receipts;
CREATE TRIGGER trg_compilation_receipts_immutable
    BEFORE UPDATE OR DELETE ON nebula.compilation_receipts
    FOR EACH ROW EXECUTE FUNCTION nebula.reject_compilation_receipt_mutation();

DROP TRIGGER IF EXISTS trg_compilation_receipts_no_truncate ON nebula.compilation_receipts;
CREATE TRIGGER trg_compilation_receipts_no_truncate
    BEFORE TRUNCATE ON nebula.compilation_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION nebula.reject_compilation_receipt_mutation();

COMMENT ON TABLE nebula.compilation_idempotency IS
    'Idempotency reservations for requirement compilation; reserve before effects to prevent duplicate plan creation.';
COMMENT ON TABLE nebula.compilation_receipts IS
    'Append-only versioned evidence for requirement compilation attempts.';

COMMIT;
