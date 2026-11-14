-- Reuses candidate B from the very first sandbox test: 'cb000000-0000-0000-0000-00000000000b'
-- (the on-call escalation candidate with no concept_relationship to express it)

INSERT INTO resolution.open_question (id, title, description, blocking, created_by, category_value_id, status_value_id)
SELECT
    '99999999-9999-9999-9999-999999999901',
    'Should WorkRequest escalation target a Role or a specific Person?',
    'Candidate proposes WorkRequest -> escalates_to -> OnCallEngineer, but no concept_relationship exists for either shape yet.',
    true, 'Planner',
    cat.id, st.id
FROM resolution.concept_attribute_value cat
JOIN resolution.concept_attribute ca1 ON ca1.id = cat.attribute_id AND ca1.name = 'category'
JOIN resolution.concept c1 ON c1.id = ca1.concept_id AND c1.name = 'OpenQuestion',
     resolution.concept_attribute_value st
JOIN resolution.concept_attribute ca2 ON ca2.id = st.attribute_id AND ca2.name = 'status'
JOIN resolution.concept c2 ON c2.id = ca2.concept_id AND c2.name = 'OpenQuestion'
WHERE cat.value = 'NEEDS_SPEC' AND st.value = 'OPEN';

INSERT INTO resolution.open_question_entity (open_question_id, asset_concept_id, entity_id)
SELECT '99999999-9999-9999-9999-999999999901', c.id, 'cb000000-0000-0000-0000-00000000000b'
FROM resolution.concept c WHERE c.name = 'Candidate';

-- two independent answers
INSERT INTO resolution.open_question_answer (id, question_id, role, answer, confidence, reasoning) VALUES
    ('88888888-8888-8888-8888-888888888801', '99999999-9999-9999-9999-999999999901',
     'architect', 'Escalate to the OnCall role, not a specific person', 'HIGH',
     'A named person leaves the org; the role concept survives org changes and matches how Wind already models responders.'),
    ('88888888-8888-8888-8888-888888888802', '99999999-9999-9999-9999-999999999901',
     'planner', 'Agree — model OnCallRole as a Role concept, WorkRequest.escalates_to should point at Role, not Person', 'MEDIUM',
     'Consistent with existing Role usage elsewhere in Nebula.');

-- move the question to IN_DELIBERATION now that answers exist
UPDATE resolution.open_question oq
SET status_value_id = st.id, updated_at = now()
FROM resolution.concept_attribute_value st
JOIN resolution.concept_attribute ca ON ca.id = st.attribute_id AND ca.name = 'status'
JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'OpenQuestion'
WHERE st.value = 'IN_DELIBERATION' AND oq.id = '99999999-9999-9999-9999-999999999901';

-- The Role-vs-Person decision is recorded; executable relationship semantics
-- remain unmodeled until a real concept_relationship and implementation exist.
UPDATE resolution.open_question oq
SET status_value_id = st.id, updated_at = now()
FROM resolution.concept_attribute_value st
JOIN resolution.concept_attribute ca ON ca.id = st.attribute_id AND ca.name = 'status'
JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'OpenQuestion'
WHERE st.value = 'RESOLVED' AND oq.id = '99999999-9999-9999-9999-999999999901';
