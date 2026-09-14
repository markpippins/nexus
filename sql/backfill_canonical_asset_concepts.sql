-- ============================================================================
-- Data backfill (no DDL): semantics.canonical_asset concept-kind rows for the
-- 310 shrapnel import-state identities, plus binding reconciliation.
--
-- Context: architect-cleared prerequisite from Assembly thread b214eba8
-- ("Import-state pattern... canonical_asset backfill is the load-bearing
-- dependency"). DBA plan: agent record add9fb27. Identity authority is the
-- rebuilt knowledge graph: knowledge.graph_entities, section='concepts',
-- entity_id = 'resolution:concept:<Name>' (312 rows, 0 duplicates). The 2
-- KG concepts without an import-state record (ShrapnelFact,
-- PromotionCandidate) are the 0003 meta-concepts — disposition EXCLUDED,
-- enforced as a drift guard below.
--
-- Idempotent: NOT EXISTS-guarded inserts; safe to re-run. Fail-closed: every
-- reconciliation assertion raises on violation, aborting the transaction.
-- Verified conventions mirrored from live rows: lowercase asset_kind, rich
-- canonical_key jsonb, bare-hex64 content_hash, validity_start set,
-- source_hash NULL (KG concept checksums are empty).
-- ============================================================================

BEGIN;

-- 1. Stage the 310 distinct import identities joined to their KG concept.
CREATE TEMP TABLE _concept_stage ON COMMIT DROP AS
SELECT DISTINCT
    x.aid                                              AS asset_id,
    substring(x.aid from 9)                            AS concept_name,
    ge.entity_id                                       AS kg_entity_id,
    ge.name                                            AS kg_name,
    ge.description                                     AS kg_description,
    ge.source_file                                     AS kg_source_file,
    ge.properties                                      AS kg_properties,
    encode(
      sha256(convert_to(
        concat_ws('|',
          ge.entity_id,
          coalesce(ge.name, ''),
          ge.section,
          coalesce(ge.description, ''),
          (SELECT string_agg(k || '=' || v, ';' ORDER BY k)
             FROM jsonb_each_text(ge.properties) t(k, v)),
          coalesce(ge.source_file, '')
        ), 'UTF8')), 'hex')                            AS content_hash
FROM (
  SELECT DISTINCT vs.value AS aid
  FROM shrapnel.object_attribute_value oav
  JOIN shrapnel.field f        ON f.id = oav.field_id AND f.property_name = 'asset_id'
  JOIN shrapnel.value v        ON v.id = oav.value_id
  JOIN shrapnel.value_string vs ON vs.id = v.id
  WHERE oav.object_id IN (
    SELECT oav2.object_id
    FROM shrapnel.object_attribute_value oav2
    JOIN shrapnel.field f2 ON f2.id = oav2.field_id
    WHERE f2.property_name = 'import_priority_tier'
  )
) x
JOIN knowledge.graph_entities ge
  ON ge.section = 'concepts'
 AND ge.entity_id = 'resolution:concept:' || substring(x.aid from 9);

-- 2. Fail-closed assertions before any write.
DO $guard$
DECLARE
  v_count integer;
BEGIN
  SELECT count(*) INTO v_count FROM _concept_stage;
  IF v_count <> 310 THEN
    RAISE EXCEPTION 'expected 310 import identities resolving to KG concepts, found %', v_count;
  END IF;

  -- KG display name is uniformly 'Concept <Name>' (verified 312/312); the
  -- canonical name lives in entity_id. Guard against drift from that shape.
  SELECT count(*) INTO v_count
  FROM _concept_stage
  WHERE kg_name IS DISTINCT FROM 'Concept ' || concept_name;
  IF v_count <> 0 THEN
    RAISE EXCEPTION 'name drift between import asset_id and KG concept display name: % rows', v_count;
  END IF;

  -- Exclusion disposition drift guard: exactly the 2 meta-concepts may lack
  -- import-state. If anything else appears, the documented reconciliation
  -- report is stale — abort and re-reconcile.
  SELECT count(*) INTO v_count
  FROM knowledge.graph_entities ge
  WHERE ge.section = 'concepts'
    AND NOT EXISTS (
      SELECT 1 FROM _concept_stage s WHERE s.kg_entity_id = ge.entity_id)
    AND ge.entity_id NOT IN (
      'resolution:concept:ShrapnelFact',
      'resolution:concept:PromotionCandidate');
  IF v_count <> 0 THEN
    RAISE EXCEPTION 'unexpected KG concepts without import-state: % (re-run reconciliation)', v_count;
  END IF;
END;
$guard$;

-- 3. Idempotent insert of concept-kind assets.
INSERT INTO semantics.canonical_asset
    (canonical_asset_id, asset_kind, canonical_key, source_hash,
     content_hash, validity_start)
SELECT
    s.asset_id,
    'concept',
    jsonb_build_object(
      'type',           'concept',
      'name',           s.concept_name,
      'kg_entity_id',   s.kg_entity_id,
      'kg_section',     'concepts',
      'source_file',    s.kg_source_file,
      'import_batch_id', 'bulk-poc-001'
    ),
    NULL,
    s.content_hash,
    now()
FROM _concept_stage s
WHERE NOT EXISTS (
  SELECT 1 FROM semantics.canonical_asset ca
  WHERE ca.canonical_asset_id = s.asset_id          -- any row, live or expired
);

-- 4. Reconciliation report.
DO $report$
DECLARE
  v_total        integer;
  v_distinct     integer;
  v_resolved     integer;
  v_ambiguous    integer;
  v_repoint      integer;
BEGIN
  SELECT count(*), count(DISTINCT canonical_asset_id)
    INTO v_total, v_distinct
    FROM semantics.canonical_asset WHERE asset_kind = 'concept' AND expired_at IS NULL;

  SELECT count(*) INTO v_resolved
  FROM _concept_stage s
  JOIN semantics.canonical_asset ca
    ON ca.canonical_asset_id = s.asset_id AND ca.expired_at IS NULL;

  SELECT count(*) INTO v_ambiguous
  FROM (
    SELECT ca.canonical_asset_id
    FROM _concept_stage s
    JOIN semantics.canonical_asset ca
      ON ca.canonical_asset_id = s.asset_id AND ca.expired_at IS NULL
    GROUP BY ca.canonical_asset_id HAVING count(*) > 1
  ) d;

  SELECT count(*) INTO v_repoint
  FROM shrapnel.object_attribute_value oav
  JOIN shrapnel.field f         ON f.id = oav.field_id AND f.property_name = 'asset_id'
  JOIN shrapnel.value v         ON v.id = oav.value_id
  JOIN shrapnel.value_string vs ON vs.id = v.id
  WHERE oav.object_id IN (
      SELECT oav2.object_id
      FROM shrapnel.object_attribute_value oav2
      JOIN shrapnel.field f2 ON f2.id = oav2.field_id
      WHERE f2.property_name = 'import_priority_tier')
    AND vs.value NOT LIKE 'concept:%';

  RAISE NOTICE 'concept-kind live rows: % (distinct ids: %)', v_total, v_distinct;
  RAISE NOTICE 'import bindings resolved to exactly one live asset: % / 310', v_resolved;
  RAISE NOTICE 'ambiguous bindings: %', v_ambiguous;
  RAISE NOTICE 'import asset_id values not matching canonical convention (re-point needed): %', v_repoint;

  IF v_total <> 310 OR v_distinct <> 310
     OR v_resolved <> 310 OR v_ambiguous <> 0 OR v_repoint <> 0 THEN
    RAISE EXCEPTION 'reconciliation assertions failed (total=%, distinct=%, resolved=%, ambiguous=%, repoint=%)',
      v_total, v_distinct, v_resolved, v_ambiguous, v_repoint;
  END IF;
END;
$report$;

COMMIT;
