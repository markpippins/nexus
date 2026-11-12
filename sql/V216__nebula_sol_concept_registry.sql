-- V216 — Nebula → SOL concept/representation registry (staged inert).
--
-- DATA ONLY. This migration inserts registry rows and nothing else: no
-- CREATE/ALTER/DROP, no change to any domain table, no row moved, no writer
-- changed, and no binding added. Because SOL resolves execution through
-- resolution.concept_attribute_binding / concept_relationship_binding, the
-- rows inserted here make concepts *describable*, not evaluable. That is the
-- point: the registry stops lying about where a concept lives before the
-- evaluation side learns to read it (Phase 3, separately gated).
--
-- NOT APPLIED BY THIS CHANGE. Live application is operator-gated after DBA
-- review, in keeping with sql/V215. The migration is proven by double apply
-- and rollback on a throwaway database in
-- bin/tests/test_nebula_sol_concept_registry.py.
--
-- Plan and measurements: docs/nebula-sol-absorption.md.
--
-- Idempotent: every INSERT is guarded by NOT EXISTS on the registry's own
-- natural key, or by ON CONFLICT on the declared unique constraint. A second
-- apply changes zero rows.

BEGIN;

-- ── 1. Concepts ────────────────────────────────────────────────────────────
-- Assessment already exists; it is extended in place, never recreated.

INSERT INTO resolution.concept (name, description)
SELECT v.name, v.description
FROM (VALUES
    ('Ticket',    'Executable work item with a workflow role, position and status lifecycle. Physical home: resolution.ticket.'),
    ('Receipt',   'Producer-registered, kind-discriminated, append-only record of a completed transition. Physical home: resolution.receipt.'),
    ('AgentRecord', 'Bitemporal narrative record of agent work. record_type selects the intent; lifecycle and envelope are shared. Physical home: nebula.agent_records.'),
    ('Decision',  'Binding architecture/governance ruling, cited and superseded. Authority home: peb.decisions. Narrative representation: nebula.agent_records with record_type = decision.'),
    ('Agenda',    'Deliberation surface grouping items that are still open. Physical home: nebula.agendas. Distinct from assembly.forum_agendas, which is UI grouping.'),
    ('AgendaItem','One item on a deliberation agenda, with source reference, decisions and open questions. Physical home: nebula.agenda_items.')
) AS v(name, description)
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.concept c WHERE c.name = v.name
);

-- ── 2. Identity strategies ─────────────────────────────────────────────────
-- One per concept (identity_strategy.concept_id is UNIQUE). Assessment had
-- none, which is why its existing representation could not be identified.

INSERT INTO resolution.identity_strategy (concept_id, canonical_key_description, notes)
SELECT c.id, v.key_description, v.notes
FROM (VALUES
    ('Ticket',      'resolution.ticket.id (uuid)', 'Workflow identity (workflow_ref, role, position) is scoped to the ticket id.'),
    ('Receipt',     'resolution.receipt.id (uuid)', 'Append-only: the id identifies one emitted receipt, never a state.'),
    ('AgentRecord', 'nebula.agent_records.id (uuid)', 'Bitemporal row identity; the same id may have more than one recorded version.'),
    ('Decision',    'peb.decisions.id (uuid)', 'adr_number is the stable human key; id is the canonical identity.'),
    ('Agenda',      'nebula.agendas.id (uuid)', 'Agenda identity is the deliberation, not the forum thread that surfaced it.'),
    ('AgendaItem',  'nebula.agenda_items.id (uuid)', 'Items are identified independently of their source_type/source_id reference.'),
    ('Assessment',  'nebula.assessments.id (uuid)', 'Outcome plus confidence attach to the assessment id, not to the observation it explains.')
) AS v(concept_name, key_description, notes)
JOIN resolution.concept c ON c.name = v.concept_name
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.identity_strategy s WHERE s.concept_id = c.id
);

-- ── 3. Representations ─────────────────────────────────────────────────────
-- Labels are the stable natural key within a concept (representation has no
-- unique constraint of its own, so NOT EXISTS carries idempotency).
-- resolution.assessment is NOT inserted: it already exists and is linked in
-- step 4. The Decision narrative representation carries a filter in
-- raw_metadata because it is a partial view of nebula.agent_records.

INSERT INTO resolution.representation
    (concept_id, label, schema_name, table_name, owner, raw_metadata)
SELECT c.id, v.label, v.schema_name, v.table_name, v.owner, v.raw_metadata::jsonb
FROM (VALUES
    ('Ticket',      'canonical ticket table',        'resolution', 'ticket',        'lilac',  '{"plane":"execution","authority":"resolution","note":"canonical persisted ticket"}'),
    ('Receipt',     'canonical receipt table',       'resolution', 'receipt',       'lilac',  '{"plane":"execution","authority":"resolution","note":"producer-registered append-only receipt"}'),
    ('AgentRecord', 'bitemporal narrative record',   'nebula',     'agent_records', 'nebula', '{"plane":"knowledge","authority":"nebula","note":"view over agent_records_history"}'),
    ('Decision',    'ADR authority ledger',          'peb',        'decisions',     'peb',    '{"plane":"governance","authority":"peb","note":"authority home per ADR-015"}'),
    ('Decision',    'narrative decision record',     'nebula',     'agent_records', 'nebula', '{"plane":"knowledge","authority":"nebula","filter":"record_type = decision","note":"narrative representation of the same ruling; no shared id yet"}'),
    ('Agenda',      'deliberation agenda',           'nebula',     'agendas',       'nebula', '{"plane":"knowledge","authority":"nebula","note":"bitemporal view over agendas_history"}'),
    ('AgendaItem',  'deliberation agenda item',      'nebula',     'agenda_items',  'nebula', '{"plane":"knowledge","authority":"nebula","note":"bitemporal view over agenda_items_history"}'),
    ('Assessment',  'operational assessment table',  'nebula',     'assessments',   'nebula', '{"plane":"knowledge","authority":"nebula","note":"JSONB + FK operational shape; absorbs inspection as a kind"}')
) AS v(concept_name, label, schema_name, table_name, owner, raw_metadata)
JOIN resolution.concept c ON c.name = v.concept_name
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.representation r
    WHERE r.concept_id = c.id AND r.label = v.label
);

-- ── 4. Representation identities ───────────────────────────────────────────
-- identity_expression is the accessor inside the representation. It is NOT
-- executed by today's loader for non-resolution schemas (see the design note,
-- section 4); it is registered so the expression is explicit rather than
-- implied by the column name.

INSERT INTO resolution.representation_identity
    (representation_id, identity_strategy_id, identity_expression, notes)
SELECT r.id, s.id, v.identity_expression, v.notes
FROM (VALUES
    ('Ticket',      'canonical ticket table',       'id', 'resolution.ticket.id'),
    ('Receipt',     'canonical receipt table',      'id', 'resolution.receipt.id'),
    ('AgentRecord', 'bitemporal narrative record',  'id', 'nebula.agent_records.id'),
    ('Decision',    'ADR authority ledger',         'id', 'peb.decisions.id'),
    ('Decision',    'narrative decision record',    'id', 'nebula.agent_records.id'),
    ('Agenda',      'deliberation agenda',          'id', 'nebula.agendas.id'),
    ('AgendaItem',  'deliberation agenda item',     'id', 'nebula.agenda_items.id'),
    ('Assessment',  'assessment table',             'id', 'existing representation; identity had never been registered'),
    ('Assessment',  'operational assessment table', 'id', 'nebula.assessments.id')
) AS v(concept_name, label, identity_expression, notes)
JOIN resolution.concept c ON c.name = v.concept_name
JOIN resolution.representation r ON r.concept_id = c.id AND r.label = v.label
JOIN resolution.identity_strategy s ON s.concept_id = c.id
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.representation_identity ri
    WHERE ri.representation_id = r.id
);

-- ── 5. Governed attributes ─────────────────────────────────────────────────
-- Only closed vocabularies the repository already enforces are registered:
-- the nine agent-record types (CHECK on nebula.agent_records_history) and the
-- Phase 4 assessment kinds. No state attribute is claimed here, so the
-- one-state-attribute-per-concept index is untouched.

INSERT INTO resolution.concept_attribute
    (concept_id, name, description, value_type, is_state_attribute)
SELECT c.id, v.name, v.description, v.value_type, false
FROM (VALUES
    ('AgentRecord', 'record_type',     'Intent label of a narrative record. Shared envelope; the value selects the intent, not a table.', 'enum'),
    ('Assessment',  'assessment_kind', 'Kind of assessment. inspection is a kind of assessment, not a separate table.',              'enum')
) AS v(concept_name, name, description, value_type)
JOIN resolution.concept c ON c.name = v.concept_name
ON CONFLICT (concept_id, name) DO NOTHING;

-- ── 6. Governed values ─────────────────────────────────────────────────────

INSERT INTO resolution.concept_attribute_value (attribute_id, value, description)
SELECT a.id, v.value, v.description
FROM resolution.concept_attribute a
JOIN resolution.concept c ON c.id = a.concept_id
JOIN (VALUES
    ('AgentRecord', 'record_type', 'report',            'Narrative report of work or findings.'),
    ('AgentRecord', 'record_type', 'analysis',          'Reasoning over evidence; may support a decision or assessment.'),
    ('AgentRecord', 'record_type', 'assessment',        'Evaluated outcome with confidence.'),
    ('AgentRecord', 'record_type', 'inspection',        'Compliance finding; assessment_kind = inspection.'),
    ('AgentRecord', 'record_type', 'prompt',            'Immutable provenance: the instruction given. Never keychained.'),
    ('AgentRecord', 'record_type', 'response',          'Immutable provenance: the answer given. Never keychained.'),
    ('AgentRecord', 'record_type', 'engineering_log',   'Operational narrative. Never keychained.'),
    ('AgentRecord', 'record_type', 'architecture_note', 'Design rationale supporting a ruling.'),
    ('AgentRecord', 'record_type', 'decision',          'Narrative representation of a peb.decisions ruling.'),
    ('Assessment',  'assessment_kind', 'assessment',    'General evaluated outcome.'),
    ('Assessment',  'assessment_kind', 'inspection',    'Compliance inspection recorded as an assessment kind.')
) AS v(concept_name, attribute_name, value, description)
    ON v.concept_name = c.name AND v.attribute_name = a.name
ON CONFLICT (attribute_id, value) DO NOTHING;

COMMIT;
