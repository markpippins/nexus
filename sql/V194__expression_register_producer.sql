-- V194 (Expression E6): register the Expression pipeline as a named
-- Resolution receipt producer.
--
-- Expression E5/E6 emit kind='expression_evaluation' receipts into the ONE
-- canonical receipt stream (resolution.receipt, V139 DDL). The producer
-- grant trigger (resolution.enforce_producer_grant) is the per-write
-- authority — without this registration, Expression writes are (correctly)
-- refused with P0004. This migration registers the authority the E6 writer
-- declares, scoped to exactly one kind; Expression cannot write lifecycle
-- or admission kinds.
--
-- R9 note: schema data change. Replicate to vanadium after the DBA applies
-- it locally (operator confirmation required per AGENTS.md R9).

INSERT INTO resolution.producer_registry
  (producer_id, name, allowed_kinds, contract_version_min, contract_version_max, registered_by)
VALUES
  ('expression-pipeline',
   'Expression transcript observation pipeline (python/expression, evaluation receipts)',
   ARRAY['expression_evaluation'],
   1, 1, 'V194')
ON CONFLICT (producer_id) DO NOTHING;

COMMENT ON TABLE resolution.producer_registry IS
  'Named write authorities with kind-scoped grants (R2/Q3). Unknown/ambiguous writer → refused. Registered: conduit-mcp (TS), nexus-execution-worker (worker lane), nexus-conduit-python (python-direct channel, V142), peb-srv (admission-only), expression-pipeline (evaluation receipts, V194).';
