#!/usr/bin/env python3
"""Hermetic tests for the DBA charter deliverables (draft, per 2dca56d4).

No database, no network. Three targets:

1. sql/grants/dba-grant-v0.1.sql — the ratification guard is structural:
   refuses to run without a session ratification UUID, refuses malformed
   UUIDs, verifies the exact placeholder shape before closing it, no-ops
   on the chartered shape, and never grants can_verify_work_requests.
2. config/harnesses/opencode/agents/dba.md — the charter text carries the
   governance load: authority boundary (no self-ratification, no
   verification power), the audit lens, and the ratification path.
3. bin/seed_dba_persona.py — pure logic: frontmatter strip + start anchor,
   version planner (fresh insert / no-op on identical body / bump on
   changed body / duplicate-version rejection).
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # bin/tests/ -> bin -> worktree root
GRANT = REPO / "sql" / "grants" / "dba-grant-v0.1.sql"
CHARTER = REPO / "config" / "harnesses" / "opencode" / "agents" / "dba.md"
LOADER = REPO / "bin" / "seed_dba_persona.py"


# ---------------------------------------------------------------------------
# 1. The grant SQL: ratification guard + capability shape
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def grant_sql() -> str:
    return GRANT.read_text(encoding="utf-8")


class TestGrantSQL:
    def test_refuses_without_ratification_variable(self, grant_sql):
        assert "current_setting('app.dba_grant_ratified', true)" in grant_sql
        assert "NOT RATIFIED" in grant_sql

    def test_rejects_malformed_ratification_uuid(self, grant_sql):
        assert "v_ratification !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}" in grant_sql

    def test_apply_instruction_documents_the_variable(self, grant_sql):
        assert "SET app.dba_grant_ratified" in grant_sql

    def test_guard_executes_before_any_write(self, grant_sql):
        guard_pos = grant_sql.index("NOT RATIFIED")
        first_write = grant_sql.index("UPDATE nebula.roles_history")
        assert guard_pos < first_write, "guard must run before the close"

    def test_placeholder_shape_verified_before_close(self, grant_sql):
        assert "capabilities unassigned" in grant_sql
        assert "owns_domains = '{}'::text[]" in grant_sql
        assert "closing generic placeholder snapshot" in grant_sql

    def test_chartered_shape_noops(self, grant_sql):
        assert "chartered row already open" in grant_sql

    def test_never_grants_verification_power(self, grant_sql):
        assert not re.search(
            r"can_verify_work_requests\s+boolean NOT NULL DEFAULT (true|TRUE)",
            grant_sql,
        )
        assert "false, true, false, true, false," in grant_sql, (
            "insert values must keep can_verify_work_requests false "
            "(greenlight=f, questions=t, agendas=f, resolve=t, verify=f)"
        )

    def test_chartered_domains_and_escalations_declared(self, grant_sql):
        assert "ARRAY['database_integrity', 'backup_replication', 'migration_ledger']" in grant_sql
        for trigger in (
            "schema_guarantee_broken",
            "green_ledger_drift",
            "backup_capture_unverified",
            "reconstruction_surface_drift",
        ):
            assert trigger in grant_sql

    def test_is_a_single_guarded_transaction(self, grant_sql):
        assert len(re.findall(r"^BEGIN;$", grant_sql, re.M)) == 1
        assert len(re.findall(r"^COMMIT;$", grant_sql, re.M)) == 1


# ---------------------------------------------------------------------------
# 2. The charter text: governance load-bearing language
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def charter() -> str:
    return CHARTER.read_text(encoding="utf-8")


class TestCharter:
    def test_no_self_ratification(self, charter):
        assert "no self-ratification" in charter.lower()
        assert "grant, widen, or ratify your own" in charter

    def test_verification_power_explicitly_refused(self, charter):
        assert "can_verify_work_requests" in charter

    def test_ratification_path_named(self, charter):
        assert "Supervisor validates" in charter
        assert "Architect" in charter
        assert "2dca56d4" in charter

    def test_owned_domains_match_grant(self, charter):
        for domain in ("database_integrity", "backup_replication", "migration_ledger"):
            assert domain in charter

    def test_audit_lens_present(self, charter):
        assert "Do not audit tables" in charter
        assert "Audit *promises*" in charter

    def test_timer_cluster_named(self, charter):
        for slot in ("06:10", "06:20", "06:30", "06:40", "06:50"):
            assert slot in charter, f"timer slot {slot} missing from charter"

    def test_populated_db_contract_present(self, charter):
        assert "Populated-DB boot safety" in charter

    def test_does_not_claim_greenlight(self, charter):
        assert "**can_greenlight** | **TRUE**" not in charter

    def test_frontmatter_and_anchor(self, charter):
        assert charter.startswith("---\n")
        assert "assumes_role: dba" in charter.split("---\n")[1]
        body = charter.split("---\n", 2)[2].lstrip("\n")
        assert body.startswith("# Role: DBA")


# ---------------------------------------------------------------------------
# 3. The persona loader: pure logic only
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def loader():
    spec = importlib.util.spec_from_file_location("seed_dba_persona", LOADER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPersonaLoader:
    def test_load_strips_frontmatter_and_requires_anchor(self, loader, tmp_path):
        good = tmp_path / "persona.md"
        good.write_text("---\nassumes_role: dba\n---\n\n# Role: DBA\n\nbody here\n")
        body = loader.load_persona_body(good)
        assert body.startswith("# Role: DBA")
        assert "assumes_role" not in body

        bad = tmp_path / "bad.md"
        bad.write_text("---\nassumes_role: dba\n---\n\n# Something Else\n")
        with pytest.raises(ValueError, match="must start with"):
            loader.load_persona_body(bad)

    def test_plan_version_fresh_insert(self, loader):
        plan = loader.plan_version([], "body")
        assert plan == {"action": "insert", "version": 1, "reason": "no prior persona"}

    def test_plan_version_noop_on_identical_body(self, loader):
        body = "# Role: DBA\n"
        h = hashlib.sha256(body.encode()).hexdigest()
        assert loader.plan_version([(1, h)], body) is None

    def test_plan_version_bumps_on_changed_body(self, loader):
        old = hashlib.sha256(b"old").hexdigest()
        plan = loader.plan_version([(2, old)], "new body")
        assert plan["version"] == 3
        assert "differs" in plan["reason"]

    def test_plan_version_rejects_duplicate_versions(self, loader):
        h = hashlib.sha256(b"x").hexdigest()
        with pytest.raises(ValueError, match="duplicate versions"):
            loader.plan_version([(1, h), (1, h)], "body")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
