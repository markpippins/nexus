-- =============================================================================
--  V172 — Capability / Adapter registry pre-stage (DBA)
--
--  Implements the adapter-based capability satisfaction semantics from the
--  operator ruling (DBA decision 4a71a56d): a CapabilitiesRequirement names
--  a protocol capability (e.g. "has-active-shrapnel-protocol"); an
--  installation satisfies it if ANY adapter provides that protocol over
--  whatever backend it has — MySQL instead of PostgreSQL, Elasticsearch,
--  any provider-API store. Provider identity is DATA, not doctrine. If
--  unsatisfied, the installation starts looking for and creating
--  alternatives (a discovery/remediation loop over the capability surface).
--
--  Demand side of the analyst's execution-profiles lattice (discussions):
--  profiles measure, requirements demand, adapters satisfy.
--
--  STAGED INERT — pending the ontologist's Adapter ruling (discussions
--  thread 3fce57ce): is an Adapter a Representation of the Capability
--  Concept, or its own Concept? Until that freeze lands, this migration
--  builds the registry surfaces with an explicit Concept-linkage SEAM
--  (nullable concept_id, no FK into resolution) so the ruling can be wired
--  without a follow-up schema migration.
--
--  House patterns mirrored: bitemporal pairs (V081 sentinels),
--  lease-provenance guards (CON0001/0002 per V167/V169), NEBULA_AUDIT
--  statement triggers (V156/V167/V169), sanity + verification gates.
-- =============================================================================

BEGIN;

-- ── 1. nebula.capabilities — the demand-side atom ───────────────────────────
CREATE TABLE IF NOT EXISTS nebula.capabilities (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL UNIQUE,
    -- kebab-case protocol name, e.g. 'has-active-shrapnel-protocol'
    description     text,
    -- the abstract protocol contract, refs-only JSONB (spec URIs, doc refs,
    -- TypeSpec contract refs). Never instance data, never credentials.
    protocol_spec   jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- ── Concept-linkage SEAM (inert until the ontologist ruling) ──────────
    -- When the roundtable freezes where Capability lives in resolution
    -- (own Concept vs stereotyped attribute), link it here. Nullable, no FK
    -- across schemas yet: the freeze decides the FK target.
    concept_id      uuid,
    -- provenance
    registered_by   text,
    registered_lease uuid,
    -- house bitemporal pair
    created_at      timestamptz NOT NULL DEFAULT now(),
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_until     timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt  timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);

COMMENT ON TABLE nebula.capabilities IS
'V172 demand-side atom: a named protocol capability an installation may be required to satisfy. Provider-agnostic by design — the capability names the contract; adapters (nebula.adapters) bind it to concrete providers. concept_id is a SEAM for the pending ontologist ruling (thread 3fce57ce), not a live FK.';

COMMENT ON COLUMN nebula.capabilities.protocol_spec IS
'Abstract protocol contract, refs-only (TypeSpec refs, spec URIs). Instance data and credentials are forbidden here per the Asset-envelopes doctrine: instance data projects, authority does not.';

-- ── 2. nebula.adapters — protocol x provider x status bindings ──────────────
CREATE TABLE IF NOT EXISTS nebula.adapters (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    capability_id    uuid NOT NULL REFERENCES nebula.capabilities(id),
    provider         text NOT NULL,
    -- DATA not doctrine: 'postgresql', 'mysql', 'elasticsearch', 'mongodb',
    -- 'convex', 'shrapnel-pg', ... — free vocabulary, no CHECK whitelist.
    -- A capability is satisfied by ANY active adapter over ANY provider.
    provider_endpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- refs-only endpoint descriptor (host refs, store identifiers, port
    -- refs). Credentials are forbidden: keychains will own secrets.
    adapter_status   text NOT NULL DEFAULT 'declared'
                     CHECK (adapter_status IN
                       ('declared','active','degraded','retired')),
    -- evidence discipline: 'active' is a drifting CLAIM, not a state of
    -- nature. What observed the last transition, and when — same reflex as
    -- lease liveness (soak) and the boot census.
    evidence         jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_checked_at  timestamptz,
    registered_by    text,
    registered_lease uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    valid_from       timestamptz NOT NULL DEFAULT now(),
    valid_until      timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt   timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);

CREATE INDEX IF NOT EXISTS idx_adapters_capability
    ON nebula.adapters (capability_id)
    WHERE recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_adapters_status
    ON nebula.adapters (adapter_status)
    WHERE recorded_until_dt = 'infinity'::timestamptz;

COMMENT ON TABLE nebula.adapters IS
'V172 supply-side binding: one adapter = one capability realized over one provider. Satisfaction = ANY adapter with adapter_status=active for the capability. Provider identity is data, not doctrine (operator ruling 4a71a56d: MySQL/Elasticsearch/etc. satisfy the same protocol).';

COMMENT ON COLUMN nebula.adapters.adapter_status IS
'declared = registered but unverified; active = an observation evidenced satisfaction; degraded = active with qualification; retired = no longer provided. Transitions carry evidence JSONB — status without evidence is exactly what this registry exists to prevent.';

COMMENT ON COLUMN nebula.adapters.provider_endpoint IS
'Refs-only endpoint descriptor (host refs, store ids). Never credentials — keychains own secrets.';

-- ── 3. Lease provenance guard (CON0001/0002 mirror) ─────────────────────────
CREATE OR REPLACE FUNCTION nebula.trg_capability_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.registered_lease IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM tackle.role_leases l
                       WHERE l.id = NEW.registered_lease) THEN
        RAISE EXCEPTION 'CAP0001: registered_lease % does not reference an open lease',
            NEW.registered_lease USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_capabilities_provenance ON nebula.capabilities;
CREATE TRIGGER trg_capabilities_provenance
BEFORE INSERT OR UPDATE ON nebula.capabilities
FOR EACH ROW EXECUTE FUNCTION nebula.trg_capability_provenance();

DROP TRIGGER IF EXISTS trg_adapters_provenance ON nebula.adapters;
CREATE TRIGGER trg_adapters_provenance
BEFORE INSERT OR UPDATE ON nebula.adapters
FOR EACH ROW EXECUTE FUNCTION nebula.trg_capability_provenance();

-- ── 4. Attributable trail: NEBULA_AUDIT on both surfaces ────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'tackle' AND p.proname = 'fn_nebula_audit_log') THEN
        CREATE FUNCTION tackle.fn_nebula_audit_log(
          p_table text, p_op text, p_rows bigint, p_keys text) RETURNS void
        LANGUAGE sql AS $fn$
          INSERT INTO tackle.system_logs
            (id, timestamp, level, category, message, source, details)
          VALUES (
            gen_random_uuid()::text, now(), 'INFO', 'NEBULA_AUDIT',
            format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
                   COALESCE(' [' || left(p_keys, 900) || ']', '')),
            'nebula-audit-trigger',
            jsonb_build_object('table', p_table, 'op', p_op, 'row_count', p_rows,
                               'keys', p_keys)
          );
        $fn$;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION nebula.trg_caps_audit()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        TG_TABLE_NAME, TG_OP, 1,
        CASE WHEN TG_OP = 'DELETE' THEN COALESCE(OLD.name, OLD.id::text)
             ELSE COALESCE(NEW.name, NEW.id::text) END);
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION nebula.trg_adapters_audit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_cap text;
BEGIN
    SELECT c.name INTO v_cap FROM nebula.capabilities c
    WHERE c.id = COALESCE(NEW.capability_id, OLD.capability_id);
    PERFORM tackle.fn_nebula_audit_log(
        TG_TABLE_NAME, TG_OP, 1,
        COALESCE(v_cap, '?') || '/' ||
        COALESCE(CASE WHEN TG_OP = 'DELETE' THEN OLD.provider ELSE NEW.provider END, '?'));
    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS trg_capabilities_audit ON nebula.capabilities;
CREATE TRIGGER trg_capabilities_audit
AFTER INSERT OR UPDATE OR DELETE ON nebula.capabilities
FOR EACH ROW EXECUTE FUNCTION nebula.trg_caps_audit();

DROP TRIGGER IF EXISTS trg_adapters_audit ON nebula.adapters;
CREATE TRIGGER trg_adapters_audit
AFTER INSERT OR UPDATE OR DELETE ON nebula.adapters
FOR EACH ROW EXECUTE FUNCTION nebula.trg_adapters_audit();

-- ── 5. Satisfaction view — supply layers, demand seam ───────────────────────
--    Layer 1 (live): capability inventory with active-adapter counts —
--    the remediation loop reads 'zero active adapters' as the gap list.
--    Layer 2 (SEAM, commented): the requirements-side demand join. Live
--    requirements carry no structured capability refs yet (req_type is
--    free text; 25 rows) and the WR collapse will reshape requirements —
--    pre-guessing that shape here would be the megatable anti-pattern.
--    When demand lands, join on capability name.
CREATE OR REPLACE VIEW nebula.v_capability_satisfaction AS
SELECT
    c.id              AS capability_id,
    c.name            AS capability,
    c.description,
    count(a.id) FILTER (WHERE a.adapter_status = 'active')    AS active_adapters,
    count(a.id) FILTER (WHERE a.adapter_status = 'degraded')  AS degraded_adapters,
    count(a.id) FILTER (WHERE a.adapter_status = 'declared')  AS declared_adapters,
    count(a.id) FILTER (WHERE a.adapter_status = 'retired')   AS retired_adapters,
    -- the verdict the remediation loop queries
    (count(a.id) FILTER (WHERE a.adapter_status = 'active') > 0) AS satisfied,
    array_remove(array_agg(DISTINCT a.provider) FILTER (
        WHERE a.adapter_status = 'active'), NULL)                 AS satisfying_providers,
    c.concept_id    -- SEAM passthrough: visible so the freeze is a link, not a hunt
FROM nebula.capabilities c
LEFT JOIN nebula.adapters a
       ON a.capability_id = c.id
      AND a.recorded_until_dt = 'infinity'::timestamptz
      AND a.valid_until = 'infinity'::timestamptz
WHERE c.recorded_until_dt = 'infinity'::timestamptz
  AND c.valid_until = 'infinity'::timestamptz
GROUP BY c.id, c.name, c.description, c.concept_id;

COMMENT ON VIEW nebula.v_capability_satisfaction IS
'Capability satisfaction lattice, supply side (V172): per capability, adapter counts by status and the satisfied verdict (any active adapter over any provider). Demand-side join to requirements is a SEAM deferred until the WR collapse reshapes requirements — see migration comments.';

-- ── 6. Post-apply verification gates ────────────────────────────────────────
DO $$
DECLARE
    v_missing text;
BEGIN
    -- no active status without evidence recorded (the discipline starts at zero)
    SELECT string_agg(DISTINCT provider, ',') INTO v_missing
    FROM nebula.adapters WHERE adapter_status = 'active'
      AND (evidence IS NULL OR evidence = '{}'::jsonb) HAVING count(*) > 0;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'V172 verify: active adapters without evidence for providers: %', v_missing
            USING ERRCODE = 'P0001';
    END IF;
    RAISE NOTICE '✅ V172 applied — capability/adapter registry live; satisfaction view available; Concept seams inert.';
END $$;

COMMIT;
