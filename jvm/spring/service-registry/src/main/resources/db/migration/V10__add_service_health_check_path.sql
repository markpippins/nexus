-- V10: add real health_check_path column to registry.services
--
-- Audit thread 70d507dc ("Audit all services for health-check path
-- conformance"): Service.getHealthCheckPath() synthesized
-- "<apiBasePath>/actuator/health" and the setter was a no-op, so every
-- caller-supplied health path was silently discarded. Express-style
-- services that actually serve /health (aegis-srv, nebula-srv,
-- cascade-srv, ...) were registered with a fabricated
-- /actuator/health value.
--
-- The column itself is created by hibernate ddl-auto=update on deploy
-- (this repo has no Flyway runtime); this migration is the canonical
-- SQL record. Apply by hand when repairing data ahead of deploy:
--   psql "$DSN" -f V10__add_service_health_check_path.sql
--
-- Idempotent: safe to re-run.

ALTER TABLE registry.services
    ADD COLUMN IF NOT EXISTS health_check_path VARCHAR(500);

COMMENT ON COLUMN registry.services.health_check_path IS
    'Verified per-service health-check path (audit 70d507dc). Null = derive from api_base_path + ''/actuator/health'' (legacy behavior).';

-- No data backfill here on purpose: the fabricated "<apiBasePath>/actuator/health"
-- values were never truthful, so they must not be copied into the new column.
-- Repair goes through the gated backfill script (bin/repair_registry_health_paths.py)
-- with per-service verified values after the truth-table audit is reviewed.
