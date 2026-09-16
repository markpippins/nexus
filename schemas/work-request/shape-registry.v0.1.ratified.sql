-- =============================================================================
-- Ontologist registry filing: vision.work_request_shape_registry v0.1 row
--
-- Gating row for V163 canonical WR landing (PR #251, merged pre-stage
-- f7c76b13). Live apply of V163 is gated on this row existing as the single
-- ratified shape (P1030 refuses a second concurrent ratified shape; P1021
-- refuses canonical-column writes until a ratified shape exists).
--
-- Per the migration contract, ratification_state = 'ratified' is set here
-- because architect zero-unmapped acceptance was GRANTED (record 41cc9b23,
-- verdict 2026-09-16, conditional on this ontologist filing) and the analyst
-- supported merge as pre-stage (record c8313641). WP2 mapping is delivered.
--
-- Filename: shape-registry.v0.1.ratified.sql
-- Artifact: schemas/work-request/canonical-shape.v0.1.json
-- sha256:   88a23ec5f0025af0d20922d7181b4e30dcb35c9e93ae0221d6a72f7258cbe424
-- Owner:    ontologist (registry filing), architect (ratification)
-- =============================================================================

-- Insert the v0.1 ratified shape. Idempotent guard: P1030 prevents a second
-- concurrent ratified row; ON CONFLICT (shape_version) DO UPDATE is safe here
-- because v0.1 is the single current shape.
INSERT INTO vision.work_request_shape_registry (
    shape_version,
    artifact_path,
    artifact_sha256,
    ratification_state,
    ratified_at,
    ratified_by,
    notes
) VALUES (
    'v0.1',
    'schemas/work-request/canonical-shape.v0.1.json',
    '88a23ec5f0025af0d20922d7181b4e30dcb35c9e93ae0221d6a72f7258cbe424',
    'ratified',
    now(),
    'architect (zero-unmapped acceptance, record 41cc9b23)',
    jsonb_build_object(
        'wp2_mapping', 'delivered (analyst 69aa28b8)',
        'pre_stage_merge', 'PR #251 merged 2026-09-16T12:41Z head f7c76b13',
        'analyst_position', 'support merge as pre-stage (c8313641)',
        'five_blockers', 'closed and verified (51462bfd)',
        'filing_owner', 'ontologist'
    )
)
ON CONFLICT (shape_version) DO UPDATE SET
    artifact_path   = EXCLUDED.artifact_path,
    artifact_sha256 = EXCLUDED.artifact_sha256,
    ratification_state = EXCLUDED.ratification_state,
    ratified_at     = COALESCE(vision.work_request_shape_registry.ratified_at, EXCLUDED.ratified_at),
    ratified_by     = EXCLUDED.ratified_by,
    notes           = EXCLUDED.notes;
