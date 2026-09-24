-- 055: GIN index on agent-record tags (pr:<N> attestation lookups).
--
-- Merge-gate gate 3 (bin/merge_pr.py) previously bounded its attestation
-- search to the newest 200 agent records because no tag index existed;
-- with this index the /api/attestations lookup is exact and cheap
-- regardless of record volume. Engineer intent f0c372ce.
--
-- agent_records is a view over agent_records_history, so the index goes
-- on the base table. Additive-only; IF NOT EXISTS keeps re-application
-- (manual psql + startup runner) idempotent.

CREATE INDEX IF NOT EXISTS idx_agent_records_history_tags_gin
    ON nebula.agent_records_history USING gin (tags);
