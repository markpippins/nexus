-- ═══════════════════════════════════════════════════════════════════════
-- V183 (was V039__open_question_entities.sql) — provenance renumber, 2026-09-18
--
-- NOT a new migration: this is the SAME file, renamed to clear the V039
-- duplicate-number collision (it collided with V039__architect_specs_and_dco).
-- RENUMBER-SAFE because: (1) the table nebula.open_question_entities was
-- created by the original V039 apply AND later deliberately dropped by
-- nebula-srv migration 042 (approved disposition D4: 134 rows, 100%
-- duplicated in direct columns, zero net data loss) — this file is
-- applied-then-retired history and can never be applied again; (2) no
-- non-projection references exist (ci-bootstrap carries the successor
-- resolution.open_question pattern, not this table).
-- Audit: DBA collision audit (R1 793679f8, 2026-09-18). Renumber executed
-- in the mig-renumber PR.
-- ═══════════════════════════════════════════════════════════════════════

-- V039: Generic linking table for open questions to any entity
--
-- Replaces the per-entity column approach (requirement_id, candidate_id)
-- with a normalized many-to-many link so open questions can be attached to
-- work requests, specifications, agendas, harvests, conversations, intents,
-- assessments, observations, reports, agent records, agents, and any future
-- entity without further schema changes.

CREATE TABLE nebula.open_question_entities (
    open_question_id UUID NOT NULL REFERENCES nebula.open_questions(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    PRIMARY KEY (open_question_id, entity_type, entity_id)
);

CREATE INDEX idx_oq_entities_type_id
    ON nebula.open_question_entities (entity_type, entity_id);

-- Seed the new table with existing requirement/candidate links.
-- Legacy requirement_id and candidate_id columns are left in place so
-- existing views and functions continue to work.
INSERT INTO nebula.open_question_entities (open_question_id, entity_type, entity_id)
SELECT id, 'requirement', requirement_id FROM nebula.open_questions WHERE requirement_id IS NOT NULL;

INSERT INTO nebula.open_question_entities (open_question_id, entity_type, entity_id)
SELECT id, 'candidate', candidate_id FROM nebula.open_questions WHERE candidate_id IS NOT NULL;

COMMIT;
