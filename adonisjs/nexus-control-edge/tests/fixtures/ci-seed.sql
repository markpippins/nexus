-- ci-seed.sql — preconditions the functional specs assert but that a
-- schema-only database does not provide.
--
-- Applied AFTER sql/ci-bootstrap/nexus-ci-bootstrap.sql (the house CI
-- snapshot) and BEFORE `npm test`. Same file locally and in CI, so what CI
-- exercises is what a developer exercises.
--
-- Why this file exists at all: the snapshot is the authority on schema, but it
-- is (a) schema-only by design and (b) scoped to a fixed schema list. Two
-- things the specs need are therefore missing, and without them the suite
-- reports 17 passed / 3 failed:
--
--   * /api/links         500  — `throttler` is NOT one of the schemas the
--                               snapshot covers (refresh.sh SCHEMAS), so the
--                               table is simply absent.
--   * /config/failure-recovery/conduit          — conduit_controller falls
--     and its POST round-trip                      back to retry_after 1800
--                                                  when no id=1 row exists,
--                                                  which is the SAME number
--                                                  the tackle table serves.
--
-- That second one is worth stating plainly: on an unseeded database the two
-- "distinct breaker paths" collapse into identical values, so the R2-3 spec
-- is not testing what its name claims until the conduit row exists. The row
-- below is the governed production value (verified on titanium
-- 2026-11-02: conduit.circuit_breaker id=1 retry_after=3600).

-- ── throttler.links ───────────────────────────────────────────────────────
-- DDL is a faithful copy of the live table (pg_dump --schema-only
-- --table=throttler.links, titanium 2026-11-02), kept verbatim rather than
-- paraphrased so a column/constraint change in production shows up here as a
-- real difference. Rows are NOT copied — the specs only require the table.
CREATE SCHEMA IF NOT EXISTS throttler;

CREATE TABLE IF NOT EXISTS throttler.links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    address text NOT NULL,
    imagename text NOT NULL,
    text text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    type text DEFAULT 'link'::text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    CONSTRAINT chk_links_type CHECK ((type = ANY (ARRAY['link'::text, 'separator'::text]))),
    CONSTRAINT links_pkey PRIMARY KEY (id)
);

-- ── conduit.circuit_breaker ───────────────────────────────────────────────
-- The table itself comes from the snapshot; only the singleton row is seeded.
-- retry_after 3600 is the conduit value the edge serves and the spec asserts;
-- tackle's table serves 1800 and must stay distinct.
INSERT INTO conduit.circuit_breaker
    (id, max_retries_per_model, retry_delay_seconds, max_fallbacks,
     push_back_to_pending, retry_after, tripped, paused)
VALUES
    (1, 3, 120, 3, 1, 3600, 0, 0)
ON CONFLICT (id) DO UPDATE SET
    max_retries_per_model = EXCLUDED.max_retries_per_model,
    retry_delay_seconds   = EXCLUDED.retry_delay_seconds,
    max_fallbacks         = EXCLUDED.max_fallbacks,
    push_back_to_pending  = EXCLUDED.push_back_to_pending,
    retry_after           = EXCLUDED.retry_after;
