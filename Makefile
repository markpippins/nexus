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
	@echo "[mesh-test] running mesh-register probe tests..."
	@python3 -m pytest bin/tests/test_mesh_register_probe.py bin/tests/test_sonar_preflight.py -v

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
