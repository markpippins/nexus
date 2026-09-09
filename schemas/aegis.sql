--
-- State Machine Registry Schema
-- Bridges TLA+ formal methods with the Resolution schema
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: aegis; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA IF NOT EXISTS aegis;

COMMENT ON SCHEMA aegis IS 'State Machine Registry for TLA+ formal methods bridging to Resolution schema';

-- ============================================================
-- CORE REGISTRY TABLES
-- ============================================================

--
-- Name: registry; Type: TABLE; Schema: aegis; Owner: -
--

CREATE TABLE aegis.registry (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    version VARCHAR(50) DEFAULT '1.0.0' NOT NULL,
    tla_plus_source TEXT,
    tla_plus_module VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    tags TEXT[] DEFAULT '{}'::text[] NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    
    -- Resolution bridge references
    main_concept_id UUID,
    
    CONSTRAINT registry_pkey PRIMARY KEY (id),
    CONSTRAINT registry_main_concept_fkey FOREIGN KEY (main_concept_id)
        REFERENCES resolution.concept(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.registry IS 'Main registry for TLA+ state machines';

CREATE INDEX idx_registry_name ON aegis.registry USING btree (name);
CREATE UNIQUE INDEX registry_active_name_unique
    ON aegis.registry USING btree (name) WHERE (is_active = true);
CREATE INDEX idx_registry_updated ON aegis.registry USING btree (updated_at DESC);

-- ============================================================
-- TLA+ CONSTANTS
-- ============================================================

CREATE TABLE aegis.constant (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL, -- Set, Tuple, Function, etc.
    value JSONB,
    description TEXT,
    constraints TEXT[] DEFAULT '{}'::text[] NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT constant_pkey PRIMARY KEY (id),
    CONSTRAINT constant_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT constant_registry_name_unique UNIQUE (registry_id, name)
);

COMMENT ON TABLE aegis.constant IS 'TLA+ constant definitions';

-- ============================================================
-- TLA+ VARIABLES
-- ============================================================

CREATE TABLE aegis.variable (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,
    initial_value JSONB,
    domain JSONB, -- Allowed values
    description TEXT,
    constraints TEXT[] DEFAULT '{}'::text[] NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    -- Resolution bridge
    attribute_id UUID,
    
    CONSTRAINT variable_pkey PRIMARY KEY (id),
    CONSTRAINT variable_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT variable_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT variable_attribute_fkey FOREIGN KEY (attribute_id)
        REFERENCES resolution.concept_attribute(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.variable IS 'TLA+ variable definitions';

CREATE INDEX idx_variable_registry ON aegis.variable USING btree (registry_id);

-- ============================================================
-- STATES
-- ============================================================

CREATE TABLE aegis.state (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    variable_assignments JSONB DEFAULT '{}'::jsonb NOT NULL, -- variable_name -> value
    constraints TEXT[] DEFAULT '{}'::text[] NOT NULL,
    is_initial BOOLEAN DEFAULT false NOT NULL,
    is_terminal BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    -- Resolution bridge
    concept_id UUID,
    attribute_value_id UUID,
    
    CONSTRAINT state_pkey PRIMARY KEY (id),
    CONSTRAINT state_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT state_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT state_concept_fkey FOREIGN KEY (concept_id)
        REFERENCES resolution.concept(id) ON DELETE SET NULL,
    CONSTRAINT state_attr_value_fkey FOREIGN KEY (attribute_value_id)
        REFERENCES resolution.concept_attribute_value(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.state IS 'TLA+ state definitions';

CREATE INDEX idx_state_registry ON aegis.state USING btree (registry_id);
CREATE INDEX idx_state_initial ON aegis.state USING btree (is_initial) WHERE (is_initial = true);

-- ============================================================
-- TRANSITIONS
-- ============================================================

CREATE TABLE aegis.transition (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    guard_expression TEXT, -- TLA+ guard condition
    action JSONB DEFAULT '{}'::jsonb NOT NULL, -- variable -> new value
    weak_fairness BOOLEAN DEFAULT false NOT NULL,
    strong_fairness BOOLEAN DEFAULT false NOT NULL,
    temporal_conditions TEXT[] DEFAULT '{}'::text[] NOT NULL,
    priority INTEGER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    -- State references
    from_state_id UUID,
    to_state_id UUID,
    
    -- Resolution bridge
    guard_rule_id UUID,
    transition_rule_id UUID,
    state_transition_id UUID,
    
    CONSTRAINT transition_pkey PRIMARY KEY (id),
    CONSTRAINT transition_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT transition_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT transition_from_state_fkey FOREIGN KEY (from_state_id)
        REFERENCES aegis.state(id) ON DELETE SET NULL,
    CONSTRAINT transition_to_state_fkey FOREIGN KEY (to_state_id)
        REFERENCES aegis.state(id) ON DELETE SET NULL,
    CONSTRAINT transition_guard_rule_fkey FOREIGN KEY (guard_rule_id)
        REFERENCES resolution.rule(id) ON DELETE SET NULL,
    CONSTRAINT transition_transition_rule_fkey FOREIGN KEY (transition_rule_id)
        REFERENCES resolution.rule(id) ON DELETE SET NULL,
    CONSTRAINT transition_state_transition_fkey FOREIGN KEY (state_transition_id)
        REFERENCES resolution.concept_state_transition(id) ON DELETE SET NULL,
    CONSTRAINT transition_states_different CHECK (from_state_id IS NULL OR to_state_id IS NULL OR from_state_id <> to_state_id)
);

COMMENT ON TABLE aegis.transition IS 'TLA+ transition definitions';

CREATE INDEX idx_transition_registry ON aegis.transition USING btree (registry_id);
CREATE INDEX idx_transition_from_state ON aegis.transition USING btree (from_state_id);
CREATE INDEX idx_transition_to_state ON aegis.transition USING btree (to_state_id);
CREATE INDEX idx_transition_priority ON aegis.transition USING btree (priority DESC);

-- ============================================================
-- INVARIANTS
-- ============================================================

CREATE TABLE aegis.invariant (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    expression TEXT NOT NULL, -- TLA+ expression
    description TEXT,
    is_type_invariant BOOLEAN DEFAULT false NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    -- Resolution bridge
    rule_id UUID,
    expression_id UUID,
    
    CONSTRAINT invariant_pkey PRIMARY KEY (id),
    CONSTRAINT invariant_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT invariant_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT invariant_rule_fkey FOREIGN KEY (rule_id)
        REFERENCES resolution.rule(id) ON DELETE SET NULL,
    CONSTRAINT invariant_expression_fkey FOREIGN KEY (expression_id)
        REFERENCES resolution.expression(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.invariant IS 'TLA+ invariant definitions';

CREATE INDEX idx_invariant_registry ON aegis.invariant USING btree (registry_id);

-- ============================================================
-- PROPERTIES (Safety/Liveness)
-- ============================================================

CREATE TABLE aegis.property (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL, -- safety, liveness, fairness
    expression TEXT NOT NULL,
    description TEXT,
    is_verified BOOLEAN DEFAULT false NOT NULL,
    verified_at TIMESTAMP WITH TIME ZONE,
    verified_by UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT property_pkey PRIMARY KEY (id),
    CONSTRAINT property_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT property_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT property_type_check CHECK (type IN ('safety', 'liveness', 'fairness'))
);

COMMENT ON TABLE aegis.property IS 'TLA+ property definitions (safety/liveness)';

CREATE INDEX idx_property_registry ON aegis.property USING btree (registry_id);
CREATE INDEX idx_property_verified ON aegis.property USING btree (is_verified);

-- ============================================================
-- TEMPORAL PROPERTIES
-- ============================================================

CREATE TABLE aegis.temporal_property (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    operator VARCHAR(10) NOT NULL, -- [], <>, ->, etc.
    expression TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT temporal_property_pkey PRIMARY KEY (id),
    CONSTRAINT temporal_property_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT temporal_property_registry_name_unique UNIQUE (registry_id, name),
    CONSTRAINT temporal_property_operator_check CHECK (operator IN ('[]', '<>', '->', '~>', '=>'))
);

COMMENT ON TABLE aegis.temporal_property IS 'TLA+ temporal logic properties';

CREATE INDEX idx_temporal_property_registry ON aegis.temporal_property USING btree (registry_id);

-- ============================================================
-- CONCEPT MAPPINGS (TLA+ -> Resolution)
-- ============================================================

CREATE TABLE aegis.concept_mapping (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    tla_name VARCHAR(255) NOT NULL,
    concept_id UUID NOT NULL,
    mapping_type VARCHAR(50) NOT NULL, -- direct, derived, composite
    mapping_expression TEXT,
    cardinality VARCHAR(50) DEFAULT 'one_to_one' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT concept_mapping_pkey PRIMARY KEY (id),
    CONSTRAINT concept_mapping_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT concept_mapping_concept_fkey FOREIGN KEY (concept_id)
        REFERENCES resolution.concept(id) ON DELETE CASCADE,
    CONSTRAINT concept_mapping_unique UNIQUE (registry_id, tla_name),
    CONSTRAINT concept_mapping_cardinality_check CHECK (cardinality IN ('one_to_one', 'one_to_many', 'many_to_one'))
);

COMMENT ON TABLE aegis.concept_mapping IS 'Maps TLA+ concepts to Resolution concepts';

CREATE INDEX idx_concept_mapping_registry ON aegis.concept_mapping USING btree (registry_id);
CREATE INDEX idx_concept_mapping_concept ON aegis.concept_mapping USING btree (concept_id);

-- ============================================================
-- ATTRIBUTE MAPPINGS (TLA+ Variables -> Resolution Attributes)
-- ============================================================

CREATE TABLE aegis.attribute_mapping (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    tla_variable VARCHAR(255) NOT NULL,
    attribute_id UUID NOT NULL,
    conversion_function TEXT,
    default_value JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT attribute_mapping_pkey PRIMARY KEY (id),
    CONSTRAINT attribute_mapping_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT attribute_mapping_attribute_fkey FOREIGN KEY (attribute_id)
        REFERENCES resolution.concept_attribute(id) ON DELETE CASCADE,
    CONSTRAINT attribute_mapping_unique UNIQUE (registry_id, tla_variable)
);

COMMENT ON TABLE aegis.attribute_mapping IS 'Maps TLA+ variables to Resolution attributes';

CREATE INDEX idx_attribute_mapping_registry ON aegis.attribute_mapping USING btree (registry_id);
CREATE INDEX idx_attribute_mapping_attribute ON aegis.attribute_mapping USING btree (attribute_id);

-- ============================================================
-- RELATIONSHIP MAPPINGS
-- ============================================================

CREATE TABLE aegis.relationship_mapping (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    tla_relationship VARCHAR(255) NOT NULL,
    relationship_id UUID NOT NULL,
    mapping_type VARCHAR(50) NOT NULL, -- direct, inverse, transitive
    constraints TEXT[] DEFAULT '{}'::text[] NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT relationship_mapping_pkey PRIMARY KEY (id),
    CONSTRAINT relationship_mapping_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT relationship_mapping_relationship_fkey FOREIGN KEY (relationship_id)
        REFERENCES resolution.concept_relationship(id) ON DELETE CASCADE,
    CONSTRAINT relationship_mapping_unique UNIQUE (registry_id, tla_relationship)
);

COMMENT ON TABLE aegis.relationship_mapping IS 'Maps TLA+ relationships to Resolution relationships';

CREATE INDEX idx_relationship_mapping_registry ON aegis.relationship_mapping USING btree (registry_id);
CREATE INDEX idx_relationship_mapping_relationship ON aegis.relationship_mapping USING btree (relationship_id);

-- ============================================================
-- VALIDATION RESULTS
-- ============================================================

CREATE TABLE aegis.validation_result (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    is_valid BOOLEAN NOT NULL,
    errors JSONB DEFAULT '[]'::jsonb NOT NULL,
    warnings JSONB DEFAULT '[]'::jsonb NOT NULL,
    suggestions JSONB DEFAULT '[]'::jsonb NOT NULL,
    validated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    validated_by UUID,
    
    CONSTRAINT validation_result_pkey PRIMARY KEY (id),
    CONSTRAINT validation_result_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE
);

COMMENT ON TABLE aegis.validation_result IS 'Results of state machine validation';

CREATE INDEX idx_validation_result_registry ON aegis.validation_result USING btree (registry_id);
CREATE INDEX idx_validation_result_valid ON aegis.validation_result USING btree (is_valid);

-- ============================================================
-- MODEL CHECKING RESULTS
-- ============================================================

CREATE TABLE aegis.model_check_result (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    property_id UUID,
    status VARCHAR(50) NOT NULL, -- PASSED, FAILED, UNKNOWN
    trace JSONB, -- Counterexample trace if failed
    checked_properties TEXT[] DEFAULT '{}'::text[] NOT NULL,
    execution_time_ms INTEGER,
    checked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    checked_by UUID,
    
    CONSTRAINT model_check_result_pkey PRIMARY KEY (id),
    CONSTRAINT model_check_result_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT model_check_result_property_fkey FOREIGN KEY (property_id)
        REFERENCES aegis.property(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.model_check_result IS 'Results of TLA+ model checking';

CREATE INDEX idx_model_check_registry ON aegis.model_check_result USING btree (registry_id);
CREATE INDEX idx_model_check_status ON aegis.model_check_result USING btree (status);
CREATE INDEX idx_model_check_time ON aegis.model_check_result USING btree (checked_at DESC);

-- ============================================================
-- STATE MACHINE EXECUTION LOG
-- ============================================================

CREATE TABLE aegis.execution_log (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    registry_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    from_state_id UUID,
    to_state_id UUID,
    transition_id UUID,
    trigger_event TEXT,
    trigger_user UUID,
    context JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    
    CONSTRAINT execution_log_pkey PRIMARY KEY (id),
    CONSTRAINT execution_log_registry_fkey FOREIGN KEY (registry_id)
        REFERENCES aegis.registry(id) ON DELETE CASCADE,
    CONSTRAINT execution_log_from_state_fkey FOREIGN KEY (from_state_id)
        REFERENCES aegis.state(id) ON DELETE SET NULL,
    CONSTRAINT execution_log_to_state_fkey FOREIGN KEY (to_state_id)
        REFERENCES aegis.state(id) ON DELETE SET NULL,
    CONSTRAINT execution_log_transition_fkey FOREIGN KEY (transition_id)
        REFERENCES aegis.transition(id) ON DELETE SET NULL
);

COMMENT ON TABLE aegis.execution_log IS 'Log of state machine executions';

CREATE INDEX idx_execution_log_registry ON aegis.execution_log USING btree (registry_id);
CREATE INDEX idx_execution_log_entity ON aegis.execution_log USING btree (entity_id);
CREATE INDEX idx_execution_log_created ON aegis.execution_log USING btree (created_at DESC);

-- ============================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================

--
-- Name: vw_registry_details; Type: VIEW; Schema: aegis; Owner: -
--

CREATE OR REPLACE VIEW aegis.vw_registry_details AS
SELECT 
    r.id AS registry_id,
    r.name AS registry_name,
    r.description,
    r.version,
    r.tla_plus_module,
    r.created_at,
    r.updated_at,
    r.is_active,
    r.main_concept_id,
    c.name AS main_concept_name,
    COUNT(DISTINCT s.id) AS state_count,
    COUNT(DISTINCT t.id) AS transition_count,
    COUNT(DISTINCT i.id) AS invariant_count,
    COUNT(DISTINCT p.id) AS property_count
FROM aegis.registry r
LEFT JOIN resolution.concept c ON c.id = r.main_concept_id
LEFT JOIN aegis.state s ON s.registry_id = r.id
LEFT JOIN aegis.transition t ON t.registry_id = r.id
LEFT JOIN aegis.invariant i ON i.registry_id = r.id
LEFT JOIN aegis.property p ON p.registry_id = r.id
GROUP BY r.id, c.name;

COMMENT ON VIEW aegis.vw_registry_details IS 'Comprehensive view of registry details';

--
-- Name: vw_transition_flow; Type: VIEW; Schema: aegis; Owner: -
--

CREATE OR REPLACE VIEW aegis.vw_transition_flow AS
SELECT 
    t.id AS transition_id,
    t.name AS transition_name,
    t.registry_id,
    r.name AS registry_name,
    fs.name AS from_state_name,
    ts.name AS to_state_name,
    t.guard_expression,
    t.priority,
    t.weak_fairness,
    t.strong_fairness,
    EXISTS (
        SELECT 1 FROM aegis.invariant i
        WHERE i.registry_id = r.id 
        AND i.expression LIKE '%' || t.name || '%'
    ) AS has_invariant,
    CASE 
        WHEN t.from_state_id IS NOT NULL AND t.to_state_id IS NOT NULL 
        THEN 'defined' 
        ELSE 'partial' 
    END AS completeness
FROM aegis.transition t
JOIN aegis.registry r ON r.id = t.registry_id
LEFT JOIN aegis.state fs ON fs.id = t.from_state_id
LEFT JOIN aegis.state ts ON ts.id = t.to_state_id;

COMMENT ON VIEW aegis.vw_transition_flow IS 'View of transition flow between states';

-- ============================================================
-- FUNCTIONS
-- ============================================================

--
-- Name: validate_registry; Type: FUNCTION; Schema: aegis; Owner: -
--

CREATE OR REPLACE FUNCTION aegis.validate_registry(
    p_registry_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_result JSONB;
    v_errors JSONB[];
    v_warnings JSONB[];
    v_suggestions JSONB[];
    v_state_count INTEGER;
    v_transition_count INTEGER;
    v_start_state_exists BOOLEAN;
    v_end_state_exists BOOLEAN;
BEGIN
    v_errors := ARRAY[]::JSONB[];
    v_warnings := ARRAY[]::JSONB[];
    v_suggestions := ARRAY[]::JSONB[];
    
    -- Check if registry exists
    IF NOT EXISTS (SELECT 1 FROM aegis.registry WHERE id = p_registry_id) THEN
        RETURN jsonb_build_object(
            'is_valid', false,
            'errors', jsonb_build_array('Registry not found'),
            'warnings', '[]'::jsonb,
            'suggestions', '[]'::jsonb
        );
    END IF;
    
    -- Check for states
    SELECT COUNT(*) INTO v_state_count 
    FROM aegis.state
    WHERE registry_id = p_registry_id;
    
    IF v_state_count = 0 THEN
        v_warnings := v_warnings || jsonb_build_object(
            'type', 'no_states',
            'message', 'No states defined for this registry'
        );
        v_suggestions := v_suggestions || jsonb_build_object(
            'type', 'add_states',
            'message', 'Consider adding at least one initial state'
        );
    END IF;
    
    -- Check for initial state
    SELECT EXISTS (
        SELECT 1 FROM aegis.state
        WHERE registry_id = p_registry_id AND is_initial = true
    ) INTO v_start_state_exists;
    
    IF NOT v_start_state_exists AND v_state_count > 0 THEN
        v_warnings := v_warnings || jsonb_build_object(
            'type', 'no_initial_state',
            'message', 'No initial state marked'
        );
        v_suggestions := v_suggestions || jsonb_build_object(
            'type', 'mark_initial',
            'message', 'Mark one state as initial'
        );
    END IF;
    
    -- Check for terminal state
    SELECT EXISTS (
        SELECT 1 FROM aegis.state
        WHERE registry_id = p_registry_id AND is_terminal = true
    ) INTO v_end_state_exists;
    
    IF NOT v_end_state_exists AND v_state_count > 1 THEN
        v_warnings := v_warnings || jsonb_build_object(
            'type', 'no_terminal_state',
            'message', 'No terminal state marked'
        );
        v_suggestions := v_suggestions || jsonb_build_object(
            'type', 'mark_terminal',
            'message', 'Consider marking a terminal state'
        );
    END IF;
    
    -- Check for transitions
    SELECT COUNT(*) INTO v_transition_count 
    FROM aegis.transition
    WHERE registry_id = p_registry_id;
    
    IF v_transition_count = 0 AND v_state_count > 1 THEN
        v_warnings := v_warnings || jsonb_build_object(
            'type', 'no_transitions',
            'message', 'No transitions defined between states'
        );
        v_suggestions := v_suggestions || jsonb_build_object(
            'type', 'add_transitions',
            'message', 'Add transitions between states to enable state changes'
        );
    END IF;
    
    -- Check for unreachable states
    IF v_state_count > 1 AND v_transition_count > 0 THEN
        -- This is a simplified check; in production, would do reachability analysis
        v_warnings := v_warnings || jsonb_build_object(
            'type', 'reachability',
            'message', 'Consider performing full reachability analysis'
        );
    END IF;
    
    -- Build result
    v_result := jsonb_build_object(
        'is_valid', true,
        'errors', COALESCE(array_to_json(v_errors)::jsonb, '[]'::jsonb),
        'warnings', COALESCE(array_to_json(v_warnings)::jsonb, '[]'::jsonb),
        'suggestions', COALESCE(array_to_json(v_suggestions)::jsonb, '[]'::jsonb),
        'summary', jsonb_build_object(
            'state_count', v_state_count,
            'transition_count', v_transition_count,
            'has_initial_state', v_start_state_exists,
            'has_terminal_state', v_end_state_exists
        )
    );
    
    -- Store validation result
    INSERT INTO aegis.validation_result (
        registry_id, is_valid, errors, warnings, suggestions
    ) VALUES (
        p_registry_id,
        (v_result->>'is_valid')::BOOLEAN,
        v_result->'errors',
        v_result->'warnings',
        v_result->'suggestions'
    );
    
    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION aegis.validate_registry(UUID) IS 'Validates a state machine registry and returns validation results';

--
-- Name: generate_tla_plus; Type: FUNCTION; Schema: aegis; Owner: -
--

CREATE OR REPLACE FUNCTION aegis.generate_tla_plus(
    p_registry_id UUID
) RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_registry_name TEXT;
    v_tla_text TEXT;
    v_constants TEXT;
    v_variables TEXT;
    v_states TEXT;
    v_transitions TEXT;
    v_invariants TEXT;
    v_properties TEXT;
BEGIN
    -- Get registry name
    SELECT name INTO v_registry_name
    FROM aegis.registry
    WHERE id = p_registry_id;
    
    IF v_registry_name IS NULL THEN
        RETURN '-- Registry not found';
    END IF;
    
    -- Build TLA+ constants section
    WITH constants_agg AS (
        SELECT string_agg(
            '    ' || name || ' : ' || type || 
            CASE WHEN value IS NOT NULL THEN ' = ' || value::text ELSE '' END,
            E'\n'
        ) AS constants_text
        FROM aegis.constant
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(constants_text, '') INTO v_constants FROM constants_agg;
    
    -- Build TLA+ variables section
    WITH vars_agg AS (
        SELECT string_agg(
            '    ' || name || ' : ' || type ||
            CASE WHEN initial_value IS NOT NULL THEN ' = ' || initial_value::text ELSE '' END,
            E'\n'
        ) AS variables_text
        FROM aegis.variable
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(variables_text, '') INTO v_variables FROM vars_agg;
    
    -- Build TLA+ states section
    WITH states_agg AS (
        SELECT string_agg(
            '    /\\ ' || name || E'\n' ||
            COALESCE(array_to_string(
                (SELECT array_agg('    /\\ ' || key || ' = ' || value::text)
                 FROM jsonb_each_text(variable_assignments)), E'\n'), ''),
            E'\n'
        ) AS states_text
        FROM aegis.state
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(states_text, '') INTO v_states FROM states_agg;
    
    -- Build TLA+ transitions section
    WITH transitions_agg AS (
        SELECT string_agg(
            '    ' || name || ' == ' || COALESCE(guard_expression, 'TRUE'),
            E'\n    \\/\n'
        ) AS transitions_text
        FROM aegis.transition
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(transitions_text, '') INTO v_transitions FROM transitions_agg;
    
    -- Build TLA+ invariants section
    WITH invariants_agg AS (
        SELECT string_agg(
            '    /\\ ' || expression,
            E'\n'
        ) AS invariants_text
        FROM aegis.invariant
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(invariants_text, '') INTO v_invariants FROM invariants_agg;
    
    -- Build TLA+ properties section
    WITH properties_agg AS (
        SELECT string_agg(
            '    ' || name || ' == ' || 
            CASE WHEN operator IS NOT NULL THEN operator || expression ELSE expression END,
            E'\n'
        ) AS properties_text
        FROM aegis.temporal_property
        WHERE registry_id = p_registry_id
    )
    SELECT COALESCE(properties_text, '') INTO v_properties FROM properties_agg;
    
    -- Assemble TLA+ specification
    v_tla_text := '---- MODULE ' || v_registry_name || ' ----' || E'\n\n';
    
    IF v_constants != '' THEN
        v_tla_text := v_tla_text || 'CONSTANTS' || E'\n' || v_constants || E'\n\n';
    END IF;
    
    IF v_variables != '' THEN
        v_tla_text := v_tla_text || 'VARIABLES' || E'\n' || v_variables || E'\n\n';
    END IF;
    
    IF v_states != '' THEN
        v_tla_text := v_tla_text || 'States ==' || E'\n' || v_states || E'\n\n';
    END IF;
    
    IF v_transitions != '' THEN
        v_tla_text := v_tla_text || 'Next ==' || E'\n' || v_transitions || E'\n\n';
    END IF;
    
    IF v_invariants != '' THEN
        v_tla_text := v_tla_text || 'Invariant ==' || E'\n' || v_invariants || E'\n\n';
    END IF;
    
    IF v_properties != '' THEN
        v_tla_text := v_tla_text || 'Properties ==' || E'\n' || v_properties || E'\n\n';
    END IF;
    
    v_tla_text := v_tla_text || '=========================================' || E'\n';
    
    RETURN v_tla_text;
END;
$$;

COMMENT ON FUNCTION aegis.generate_tla_plus(UUID) IS 'Generates TLA+ specification from registry data';

-- ============================================================
-- TRIGGERS
-- ============================================================

--
-- Name: trg_registry_update_timestamp; Type: TRIGGER; Schema: aegis; Owner: -
--

CREATE OR REPLACE FUNCTION aegis.update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_registry_update_timestamp
    BEFORE UPDATE ON aegis.registry
    FOR EACH ROW
    EXECUTE FUNCTION aegis.update_timestamp();

-- ============================================================
-- USAGE EXAMPLE
-- ============================================================

-- Example: Create a state machine registry from TLA+ spec
DO $$
DECLARE
    v_registry_id UUID;
    v_state_id UUID;
    v_transition_id UUID;
BEGIN
    -- Create registry
    INSERT INTO aegis.registry (
        name, description, version, tla_plus_module, tla_plus_source, is_active
    ) VALUES (
        'WorkRequestLifecycle',
        'State machine for work request lifecycle management',
        '1.0.0',
        'WorkRequestLifecycle',
        '---- MODULE WorkRequestLifecycle ----' || E'\n' ||
        'EXTENDS Integers' || E'\n\n' ||
        'CONSTANTS' || E'\n' ||
        '    DRAFT, APPROVED, DISPATCHED, COMPLETED, CANCELLED' || E'\n\n' ||
        'VARIABLES' || E'\n' ||
        '    state, priority, assignee' || E'\n\n' ||
        'States == {DRAFT, APPROVED, DISPATCHED, COMPLETED, CANCELLED}' || E'\n\n' ||
        'Init ==' || E'\n' ||
        '    /\\ state = DRAFT' || E'\n' ||
        '    /\\ priority = 0' || E'\n' ||
        '    /\\ assignee = NULL' || E'\n\n' ||
        'Approve ==' || E'\n' ||
        '    /\\ state = DRAFT' || E'\n' ||
        '    /\\ priority > 0' || E'\n' ||
        '    /\\ state'' = APPROVED' || E'\n\n' ||
        'Dispatch ==' || E'\n' ||
        '    /\\ state = APPROVED' || E'\n' ||
        '    /\\ assignee != NULL' || E'\n' ||
        '    /\\ state'' = DISPATCHED' || E'\n\n' ||
        'Complete ==
