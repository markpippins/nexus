.PHONY: cir1 cir1-scan cir1-lint cir1-fix cir1-validate \
        cir2-lint cir2-validate cir3-lint cir3-validate cir4-lint cir4-validate \
        cir5-lint cir5-validate \
        cir-arl authority-check jsonld-check jsonld-map cir-verify contract-audit install-hooks \
        import-boundaries \
        test-db-setup test-db-reset test \
        mesh-test seed-guard-bootstrap seed-guard-test \
        apidocs-extract apidocs-gen apidocs-validate apidocs-regen \
        mcp-start mcp-stop mcp-restart mcp-status mcp-watch

# ─── CIR-1: Full pipeline (read-only) ─────────────────────────────────────────

cir1:
	@echo "Running CIR-1 full pipeline (read-only)..."
	@$(MAKE) cir1-scan
	@$(MAKE) cir1-lint
	@$(MAKE) cir1-validate

cir1-scan:
	@echo "[CIR-1] scanning references..."
	@rg -n "intent_source|\.pipeline/|PIPELINE_|ExecutionState|ExecutorRegistry" . -g '!.git' -g '!*.lock' || true

cir1-lint:
	@echo "[CIR-1] AST linting JSON (full CIR-1..5 -- default path includes CIR-5)..."
	@python3 tools/cir1/lint.py --all

cir1-fix:
	@echo "[CIR-1] WARNING: patch.py is opt-in. Use 'make cir1-apply' or run patch.py --apply directly."
	@python3 tools/cir1/patch.py --dry-run

cir1-apply:
	@echo "[CIR-1] applying deterministic patches..."
	@python3 tools/cir1/patch.py --apply

cir1-validate:
	@echo "[CIR-1] validation gate (full CIR-1..5)..."
	@python3 tools/cir1/lint.py --all --strict

# ─── CIR-2: Cross-layer isolation ────────────────────────────────────────────

cir2-lint:
	@echo "[CIR-2] checking cross-layer isolation..."
	@python3 tools/cir1/lint.py --cir2

cir2-validate:
	@echo "[CIR-2] strict gate..."
	@python3 tools/cir1/lint.py --cir2 --strict

# ─── CIR-3: Execution semantics contract ─────────────────────────────────────

cir3-lint:
	@echo "[CIR-3] execution semantics check..."
	@python3 tools/cir1/lint.py --cir3

cir3-validate:
	@echo "[CIR-3] strict execution gate..."
	@python3 tools/cir1/lint.py --cir3 --strict

# ─── CIR-4: Static derived state ─────────────────────────────────────────────

cir4-lint:
	@echo "[CIR-4] derived state integrity check..."
	@python3 tools/cir1/lint.py --cir4

cir4-validate:
	@echo "[CIR-4] strict derived-state gate..."
	@python3 tools/cir1/lint.py --cir4 --strict

# ─── CIR-5: Single Canonical Authority Rule ──────────────────────────────────

cir5-lint:
	@echo "[CIR-5] authority consistency check..."
	@python3 tools/cir1/lint.py --cir5

cir5-validate:
	@echo "[CIR-5] strict authority gate..."
	@python3 tools/cir1/lint.py --cir5 --strict

# ─── Authority matrix: single-canonical-authority validator ─────────────────

authority-check:
	@echo "[AUTHORITY] single-canonical-authority check..."
	@python3 tools/authority/check_authority.py

# ─── Import boundaries: forbidden reverse-dependency enforcement (T05) ───────

import-boundaries:
	@echo "[IMPORT-BOUNDARIES] named import-boundary / forbidden reverse-dependency check..."
	@python3 tools/authority/check_import_boundaries.py

# ─── CIR v2: Anti-Recursion Linter ────────────────────────────────────────────

cir-arl:
	@echo "[CIR-ARL] running Anti-Recursion Linter..."
	@python3 tools/arl_linter.py

cir-arl-json:
	@python3 tools/arl_linter.py --json

# ─── JSON-LD resolver + validator (contract-stack Step 11) ──────────────────

jsonld-check:
	@echo "[JSONLD] resolving nexus.local @context URLs + validating vocabulary..."
	@python3 tools/authority/check_jsonld.py

jsonld-map:
	@python3 tools/authority/check_jsonld.py --map

# ─── Contract audit: one entrypoint for the whole contract stack ────────────

contract-audit:
	@echo "Running full contract audit (arl + cir1-5 + authority + projection-ir + graph + jsonld + apidocs)..."
	@python3 tools/contract_audit.py

contract-audit-json:
	@python3 tools/contract_audit.py --json

# ─── CIR v2: Full verification suite ─────────────────────────────────────────

cir-verify:
	@echo "Running full CIR verification suite..."
	@$(MAKE) cir1-lint
	@$(MAKE) cir2-lint
	@$(MAKE) cir3-lint
	@$(MAKE) cir4-lint
	@$(MAKE) cir5-lint
	@$(MAKE) authority-check
	@$(MAKE) cir-arl

cir-validate:
	@echo "Running full CIR validation suite..."
	@$(MAKE) cir1-validate
	@$(MAKE) cir2-validate
	@$(MAKE) cir3-validate
	@$(MAKE) cir4-validate
	@$(MAKE) cir5-validate
	@$(MAKE) authority-check
	@$(MAKE) cir-arl

# ─── Test database ────────────────────────────────────────────────────────────

test-db-setup:
	@echo "Setting up conduit test database..."
	@CONDUIT_PG_DSN="$$(grep '^CONDUIT_PG_DSN=' legacy/python/conduit/.env | cut -d= -f2-)" ; \
		export CONDUIT_PG_DSN ; \
		cd legacy/python/conduit && python3 setup_test_db.py

test-db-reset:
	@echo "Resetting conduit test database..."
	@CONDUIT_PG_DSN="$$(grep '^CONDUIT_PG_DSN=' legacy/python/conduit/.env | cut -d= -f2-)" ; \
		export CONDUIT_PG_DSN ; \
		cd legacy/python/conduit && python3 setup_test_db.py --drop

test:
	@echo "Running conduit tests..."
	@cd legacy/python/conduit && \
		test -f .env.test && \
			CONDUIT_PG_DSN="$$(grep '^CONDUIT_PG_DSN=' .env.test | cut -d= -f2-)" \
			CONDUIT_PG_SCHEMA="$$(grep '^CONDUIT_PG_SCHEMA=' .env.test | cut -d= -f2-)" \
		|| CONDUIT_PG_DSN="$$(grep '^CONDUIT_PG_DSN=' .env | cut -d= -f2-)" \
			CONDUIT_PG_SCHEMA="$$(grep '^CONDUIT_PG_SCHEMA=' .env | cut -d= -f2-)" ; \
		export CONDUIT_PG_DSN CONDUIT_PG_SCHEMA ; \
		python3 -m pytest test_guard.py tests/test_lifecycle.py \
			tests/test_dispatch_integration.py tests/test_db_adapter_pg_init.py \
			tests/test_e2e_pipeline.py tests/test_e2e_pipeline_v2.py -v

# ─── MCP Server Management ───────────────────────────────────────────────────
# The daemon auto-restarts the server on crash.  Use `mcp-restart` after
# making code changes to pick up the new build.  Use `mcp-watch` during
# development to auto-restart on every file save.

MCP_DAEMON := typescript/conduit-mcp/scripts/mcp-daemon.sh

mcp-start:
	@bash $(MCP_DAEMON) start

mcp-stop:
	@bash $(MCP_DAEMON) stop

mcp-restart:
	@bash $(MCP_DAEMON) restart

mcp-status:
	@bash $(MCP_DAEMON) status

mcp-watch:
	@echo "Starting MCP server in watch mode — auto-restarts on file changes."
	@echo "Kill with Ctrl-C."
	@cd typescript/conduit-mcp && npx tsx watch src/index.ts

# ─── mesh-register probe tests (Python pytest) ────────────────────────────────
# Backed by nexus/.github/workflows/mesh-pytest.yml — same command locally
# and in CI. Keeps the vendor-extension regression locked in (see commit
# history for context).
#
# `mesh-test` only RUNS pytest; it does NOT install it. First-time setup
# is `pip install -r requirements-dev.txt` (the same file CI uses).

mesh-test:
	@echo "[mesh-test] running mesh-register probe + drive-guard tests..."
	@python3 -m pytest bin/tests/test_mesh_register_probe.py bin/tests/test_sonar_preflight.py \
		bin/tests/test_drive_guard.py bin/tests/test_vdci_backup.py \
		bin/tests/test_pg_escape_hatch.py bin/tests/test_adapter_health_probe.py \
		bin/tests/test_boot_attest.py \
	bin/tests/test_migration_commit_lint.py \
	bin/tests/test_resolver_soak_report.py \
		bin/tests/test_mark_operator_go_applied.py \
		bin/tests/test_flow_recorder_v0.py \
		bin/tests/test_flow_emit_aegis.py \
		bin/tests/test_calendar_typespec.py \
		bin/tests/test_calendar_emit.py \
		bin/tests/test_consolidate_wiring.py \
		bin/tests/test_session1_minutes.py \
		bin/tests/test_calendar_fleet_rollout.py \
		bin/tests/test_apply_v184_package.py \
		bin/tests/test_calendar_consolidate.py \
		bin/tests/test_boot_digest.py bin/tests/test_boot_conn_record.py \
		bin/tests/test_boot_attest.py \
		bin/tests/test_endpoint_register.py \
		bin/tests/test_fleet_registry_sync.py \
		bin/tests/test_fleet_manifest_from_ansible.py \
		bin/tests/test_terrain_drift_check.py \
		bin/tests/test_terrain_status_sync.py \
		bin/tests/test_drift_check_run.py -v

close-code-g3-test:
	@echo "[close-code-g3-test] G3 remap (hermetic mapping + real-V185 E2E, wr-conf-038)..."
	@python3 -m pytest python/cascade/test_conformance_close_code_g3.py python/nexus_core/wrp/tests/test_v185_close_code_e2e.py -v

adapter-probe-test:
	@echo "[adapter-probe-test] running adapter health probe hermetic suite..."
	@python3 -m pytest bin/tests/test_adapter_health_probe.py -v

satisfaction-states-e2e-test:
	@echo "[satisfaction-states-e2e-test] V174 E2E (real V172+V173+V174, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v174_satisfaction_e2e.py -v

roles-history-e2e-test:
	@echo "[roles-history-e2e-test] V175 E2E (pre-V175 shape -> repair -> grant template; + born-repaired ci-bootstrap path, throwaway DBs)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v175_roles_history_e2e.py -v

wave1-grants-e2e-test:
	@echo "[wave1-grants-e2e-test] Wave-1 clone grants E2E (pinned baseline gate + analyst-ii/engineer-ii grant events, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_wave1_grants_e2e.py -v

wave2-grants-e2e-test:
	@echo "[wave2-grants-e2e-test] Wave-2 grants E2E (six ratified grant events: critic, epistemologist, devops, sysadmin, operator, sound-technician, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_wave2_grants_e2e.py -v

tester-grant-e2e-test:
	@echo "[tester-grant-e2e-test] tester capability grant v0.1 E2E (grant event on V175 shape, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_tester_grant_e2e.py -v

roles-audit-e2e-test:
	@echo "[roles-audit-e2e-test] V177 E2E (roles_history joins the V156 NEBULA_AUDIT family; grant events leave an attributable trail, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v177_roles_audit_e2e.py -v

role-memory-integrity-e2e-test:
	@echo "[role-memory-integrity-e2e-test] V178 E2E (role_memory overlapping-interval exclusion; keep-oldest repair + born-repaired bootstrap, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v178_role_memory_e2e.py -v

sonar-preflight-test:
	@echo "[sonar-preflight-test] running sonar pre-flight gate tests..."
	@python3 -m pytest bin/tests/test_sonar_preflight.py -v

# ─── Seed drift guard (wr-conf-006) ─────────────────────────────────────────
# Backed by nexus/.github/workflows/seed-guard.yml — same commands locally and
# in CI.
#
#   make seed-guard-bootstrap  reconstruct the tackle schema in a throwaway DB
#                              from the committed typescript/tackle-seeds/
#                              seed-manifest.json (refuses a schema that looks
#                              like the live DB)
#   make seed-guard-test       run the FULL wr-conf-006 suite: renders
#                              seedMemoryProcedures() from source (node),
#                              executes it against shadow + scratch schemas,
#                              and asserts card/role counts + per-card sha256 +
#                              role sets match both the live tables (AC1-AC3)
#                              and the committed manifest (AC5).
#
# Needs `node` and a reachable Postgres. For a schema-free DB, run
# `make seed-guard-bootstrap` first (CI does this); against the live DB the
# AC1-AC4 live-compare tests run directly.

seed-guard-bootstrap:
	@echo "[seed-guard] bootstrapping tackle schema from committed seed-manifest.json..."
	@python3 bin/bootstrap_seed_manifest.py

seed-guard-test:
	@echo "[seed-guard] running wr-conf-006 full suite (AC1-AC5)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_conformance_seed_guard.py -v

# ─── API docs (tools/api-docs) ───────────────────────────────────────────────
# Backed by nexus/.github/workflows/apidocs.yml — same commands locally and
# in CI. Extracts the live route inventory from source and verifies every
# committed *-srv openapi.yaml still matches it.
#
#   make apidocs-validate   # CI gate: exit 1 on drift
#   make apidocs-regen      # refresh openapi.yaml + API.md after route changes
#   make apidocs-gen        # generate only (SKIP_FASTAPI=1 avoids the live
#                           #   vision-srv /openapi.json fetch, for CI)

APIDOCS_INV := /tmp/api_inventory.json

apidocs-extract:
	@echo "[apidocs] extracting route inventory..."
	@python3 tools/api-docs/extract_routes.py --out $(APIDOCS_INV)

apidocs-gen:
	@echo "[apidocs] generating openapi.yaml + API.md..."
	@python3 tools/api-docs/gen_openapi.py --inventory $(APIDOCS_INV) $(if $(SKIP_FASTAPI),--skip-fastapi,)

apidocs-validate:
	@$(MAKE) apidocs-extract
	@echo "[apidocs] drift check..."
	@python3 tools/api-docs/check_drift.py

apidocs-regen:
	@$(MAKE) apidocs-extract
	@$(MAKE) apidocs-gen

# ─── Git hooks ────────────────────────────────────────────────────────────────

install-hooks:
	@echo "Installing CIR pre-commit hook..."
	@cp .githooks/pre-commit .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "Done."

attestation-chain-test:
	@echo "[attestation-chain-test] running attestation-chain gate contract suite (hermetic)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_attestation_chain.py -v

attestations-e2e-test:
	@echo "[attestations-e2e-test] V179 E2E (real gate-contract DDL, throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v179_attestations_e2e.py -v
calendar-e2e-test:
	@echo "[calendar-e2e-test] V184 calendar primitive E2E (staged-inert DDL, throwaway DB, wr-conf-036)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v184_calendar_e2e.py -v
calendar-consolidate-test:
	@echo "[calendar-consolidate-test] consolidation intake (hermetic + real-V184 E2E, wr-conf-037)..."
	@python3 -m pytest bin/tests/test_calendar_consolidate.py python/nexus_core/wrp/tests/test_calendar_consolidate_e2e.py -v
wr-primitive-e2e-test:
	@echo "[wr-primitive-e2e-test] V186 WorkRequest absorb E2E (staged-inert DDL, throwaway DB, wr-conf-039)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v186_work_request_e2e.py -v
wr-view-demotion-e2e-test:
	@echo "[wr-view-demotion-e2e-test] V187 view demotion E2E (real V186+V187, throwaway DB, wr-conf-040)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v187_work_request_demotion_e2e.py -v

topology-spine-e2e-test:
	@echo "[topology-spine-e2e-test] V189 topology asset-spine E2E (staged-inert DDL, throwaway DB, wr-conf-041)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v189_topology_spine_e2e.py -v

v191-users-bcrypt-e2e-test:
	@echo "[v191-users-bcrypt-e2e-test] V191 bcrypt-at-rest E2E (backfill + round-trip + born-clean CHECKs, throwaway DB, wr-conf-043)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_v191_users_bcrypt_e2e.py -v

wr-repoint-verify-test:
	@echo "[wr-repoint-verify-test] W1-W4 repoint verification battery (hermetic, no DB/network)..."
	@python3 -m unittest bin.tests.test_wr_repoint_verify -v

wr-repoint-verify-live:
	@echo "[wr-repoint-verify-live] structural battery + optional tranche against LIVE PG (read-only)..."
	@python3 bin/wr-repoint-verify.py $(ARGS)

r9-verify-test:
	@echo "[r9-verify-test] R9 vanadium replication checklist battery (hermetic, no ssh/systemd/journal)..."
	@python3 -m unittest bin.tests.test_r9_replication_verify -v

r9-verify-live:
	@echo "[r9-verify-live] R9 replication checklist against LIVE hosts (read-only; V4 checksums vanadium — allow minutes)..."
	@python3 bin/r9-replication-verify.py $(ARGS)
attest-wiring-test:
	@echo "[attest-wiring-test] boot-shim attestation wiring (hermetic + real-DB E2E, wr-conf-034)..."
	@python3 -m pytest python/continuity/tests/test_attest.py python/nexus_core/wrp/tests/test_attest_wiring_e2e.py -v
lead-engineer-grant-e2e-test:
	@echo "[lead-engineer-grant-e2e-test] Wave-3 grant E2E (greenlight authority, ratified separation; throwaway DB)..."
	@python3 -m pytest python/nexus_core/wrp/tests/test_lead_engineer_grant_e2e.py -v
applied-grants-preflight-e2e-test:
	@echo "[applied-grants-preflight-e2e-test] V180 rediff-gate E2E (apply / refuse-reapply / changed-spec; throwaway DB)..."
	python3 -m pytest python/nexus_core/wrp/tests/test_v180_applied_grants_e2e.py -v
