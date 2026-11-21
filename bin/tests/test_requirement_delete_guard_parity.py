#!/usr/bin/env python3
"""Parity guard: every requirement-delete writing surface answers the same way.

Three services delete requirements from the same database through the same
V222 guard: nebula-srv (`typescript/nebula-srv`), the moleculer twin
(`moleculer/nebula`) and the Adonis control edge
(`adonisjs/nexus-control-edge`). They are separate deployables with separate
HTTP stacks, so the guard logic is carried once per service — and that is
exactly how three implementations drift.

This guard pins the parts that must not drift:

  * the SQL every surface sends to the database is identical (guard probe,
    blocker enumeration, ordered set delete, pre-V222 fallback);
  * every surface exports the same guard API, and no guard module reads the
    canonical table directly (V222 is operator-gated; the transition surfaces
    are correct before and after the apply);
  * every delete route calls the shared pre-check (`requirementDeleteConflict`
    mapping), answers a refusal with the shared conflict code, and reports what
    it removed on success;
  * no route still deletes requirements directly, which would bypass the guard.

Behavioural coverage lives with each service: nebula-srv's vitest suite
(`requirement-delete-and-plan-refs.test.ts`), moleculer's jest suite
(`test/nebula.test.ts`), the control edge's japa unit suite
(`tests/unit/requirement-delete-guard.spec.ts`) and — because a stubbed Lucid
transaction cannot prove rollback — its Postgres-backed functional suite
(`tests/functional/requirement-delete-guard.spec.ts`), which drives the four
HTTP routes against a throwaway database with V220 + V222 applied.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GUARD_MODULES = {
    "nebula-srv": REPO_ROOT / "typescript" / "nebula-srv" / "src" / "requirement-delete.ts",
    "moleculer": REPO_ROOT / "moleculer" / "nebula" / "src" / "requirement-delete.ts",
    "adonis": (
        REPO_ROOT
        / "adonisjs"
        / "nexus-control-edge"
        / "app"
        / "services"
        / "requirement_delete_guard.ts"
    ),
}

SURFACES = {
    "nebula-srv": REPO_ROOT / "typescript" / "nebula-srv" / "src" / "routes.ts",
    "moleculer": REPO_ROOT / "moleculer" / "nebula" / "src" / "routes.ts",
    "adonis": (
        REPO_ROOT
        / "adonisjs"
        / "nexus-control-edge"
        / "app"
        / "controllers"
        / "nebula_hierarchy_controller.ts"
    ),
}

# The four delete routes each service must cover: a system, a subsystem, a
# feature and a single requirement.
DELETE_HANDLERS = 4

SQL_CONSTANTS = ("GUARD_PROBE_SQL", "BLOCKERS_SQL", "DELETE_SET_SQL", "FALLBACK_DELETE_SQL")

GUARD_API = (
    "REQUIREMENT_DELETE_BLOCKED_CODE",
    "RequirementDeleteBlocked",
    "requirementDeleteGuardInstalled",
    "requirementIdsInScope",
    "blockedRequirements",
    "deleteRequirements",
    "deleteRequirementSet",
    "requirementDeleteConflict",
    "requirementDeleteSuccess",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing guard surface: {path}"
    return path.read_text(encoding="utf-8")


def sql_of(path: Path) -> dict[str, str]:
    """The SQL constants, whitespace-normalized so quoting style cannot hide a
    semantic change but formatting cannot fail the guard either."""
    text = _read(path)
    out: dict[str, str] = {}
    for name in SQL_CONSTANTS:
        match = re.search(rf"export const {name}\s*=\s*([\s\S]*?)\n\s*\n", text)
        assert match, f"{path.name}: {name} is not declared as a module constant"
        fragments = re.findall(r"`([^`]*)`|'([^']*)'", match.group(1))
        sql = "".join(quoted or single for quoted, single in fragments)
        out[name] = re.sub(r"\s+", " ", sql).strip()
    return out


def test_all_guard_modules_send_identical_sql():
    by_service = {service: sql_of(path) for service, path in GUARD_MODULES.items()}
    reference_service, reference = next(iter(by_service.items()))
    for service, sql in by_service.items():
        assert sql == reference, (
            f"{service} and {reference_service} disagree on the requirement-delete SQL; "
            f"differences: "
            f"{ {name: (reference.get(name), sql.get(name)) for name in SQL_CONSTANTS if reference.get(name) != sql.get(name)} }"
        )
    # The SQL is the contract; name the load-bearing pieces so a rewrite cannot
    # quietly drop the guard, the ordered delete, or the pre-V222 fallback.
    assert "to_regprocedure('resolution.requirement_delete_blockers(uuid,uuid[])') IS NOT NULL" in reference["GUARD_PROBE_SQL"]
    assert "resolution.requirement_delete_blockers(wanted.id, $2::uuid[])" in reference["BLOCKERS_SQL"]
    assert "FROM unnest($1::uuid[])" in reference["BLOCKERS_SQL"]
    assert "resolution.delete_requirements($1::uuid[])" in reference["DELETE_SET_SQL"]
    assert "DELETE FROM requirements WHERE id = ANY($1::uuid[])" in reference["FALLBACK_DELETE_SQL"]


def test_every_guard_module_exposes_the_same_api():
    for service, path in GUARD_MODULES.items():
        text = _read(path)
        for symbol in GUARD_API:
            assert symbol in text, f"{service}: guard module is missing {symbol}"
        # A refusal is a conflict, never a server fault: both database spellings
        # of "still referenced" are mapped.
        assert "'P4A01'" in text, service
        assert "'23503'" in text, service
        assert "deletedRequirements" in text, service


def test_guard_modules_never_read_the_unported_canonical_table():
    """V222 is operator-gated, so a base-table read would serve nothing first."""
    for service, path in GUARD_MODULES.items():
        text = _read(path)
        offenders = re.findall(
            r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+resolution\.requirement\b",
            text,
            re.IGNORECASE,
        )
        assert offenders == [], f"{service} reads resolution.requirement directly"


@pytest.mark.parametrize("service", list(SURFACES))
def test_delete_routes_use_the_shared_guard(service: str):
    text = _read(SURFACES[service])

    # The shared module, not a local copy of the logic.
    assert re.search(r"from '\./requirement-delete'|from '\.\./services/requirement_delete_guard\.js'", text), (
        f"{service}: delete routes do not import the shared guard module"
    )

    # One refusal mapping and one removal report per delete route.
    assert text.count("requirementDeleteConflict") >= DELETE_HANDLERS + 1, service
    assert text.count("requirementDeleteSuccess(deleted)") == DELETE_HANDLERS, (
        f"{service}: every delete route must report what it removed"
    )
    assert text.count("REQUIREMENT_DELETE_BLOCKED_CODE") >= DELETE_HANDLERS, (
        f"{service}: every delete route must answer with the shared conflict code"
    )

    # No route deletes requirements behind the guard's back (the pre-V222
    # fallback lives inside the shared module only).
    assert "DELETE FROM requirements" not in text, (
        f"{service}: a delete route bypasses the requirement delete guard"
    )

    # Single-transaction deletes: begin/commit/rollback around the cascade.
    if service == "adonis":
        assert text.count("asQueryable(trx)") == DELETE_HANDLERS, service
        assert text.count("trx.commit()") >= DELETE_HANDLERS, service
        assert text.count("trx.rollback()") >= DELETE_HANDLERS, service
    else:
        assert text.count("client.query('BEGIN')") >= DELETE_HANDLERS, service
        assert text.count("client.query('COMMIT')") >= DELETE_HANDLERS, service
        assert text.count("client.query('ROLLBACK')") >= DELETE_HANDLERS, service
        assert text.count("pool.connect()") >= DELETE_HANDLERS, service


@pytest.mark.parametrize("service", list(SURFACES))
def test_every_delete_surface_has_behavioural_tests(service: str):
    """The parity above is structural; each service must also prove the answers."""
    tests = {
        "nebula-srv": REPO_ROOT
        / "typescript"
        / "nebula-srv"
        / "src"
        / "__tests__"
        / "requirement-delete-and-plan-refs.test.ts",
        "moleculer": REPO_ROOT / "moleculer" / "nebula" / "test" / "nebula.test.ts",
        "adonis": REPO_ROOT
        / "adonisjs"
        / "nexus-control-edge"
        / "tests"
        / "unit"
        / "requirement-delete-guard.spec.ts",
    }
    text = _read(tests[service])
    assert "requirement_delete_blocked" in text, f"{service}: no refusal assertion"
    assert "deletedRequirements" in text, f"{service}: no removal-report assertion"
    assert re.search(r"P4A01|racing guard refusal|race", text), (
        f"{service}: no coverage of the trigger's own refusal"
    )
    assert "23503" in text, f"{service}: no coverage of a residual FK violation"


ADONIS_INTEGRATION = (
    REPO_ROOT
    / "adonisjs"
    / "nexus-control-edge"
    / "tests"
    / "functional"
    / "requirement-delete-guard.spec.ts"
)

ADONIS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "adonis-control-edge.yml"


def test_adonis_delete_paths_are_proven_against_postgres():
    """The control edge's four delete paths must run against a real database.

    The unit suite stubs the Lucid transaction, so it can assert intent but not
    rollback, row survival or the guard's own refusal. The functional suite is
    the proof: a real pg connection, the real V220/V222 migration files, and the
    real HTTP routes. Pin those properties so the suite cannot quietly decay
    back into a mock.
    """
    text = _read(ADONIS_INTEGRATION)

    # A real driver, not a stub.
    assert "new pg.Client" in text, "the functional suite does not open a real connection"
    assert "db.transaction" not in text, "the functional suite must not stub the transaction"
    assert "fakeTransaction" not in text, "the functional suite must not reuse the unit stub"

    # All four delete surfaces, driven over HTTP.
    for route in ("/api/systems/", "/api/subsystems/", "/api/features/", "/api/requirements/"):
        assert f"client.delete(`{route}" in text, f"the functional suite never deletes via {route}"

    # The schema under test is the real migration, not a hand-built fixture.
    assert "V222__port_requirements_to_resolution.sql" in text
    assert "V220__work_request_requirement_and_artifact_links.sql" in text

    # The contract the suite exists to prove.
    assert "requirement_delete_blocked" in text
    assert re.search(r"assertStatus\(409\)", text), "no 409 assertion"
    assert "deletedRequirements" in text, "no removal-report assertion"
    assert "blockerIds" in text, "no blocker-id assertion"

    # Throwaway databases only: the suite skips anything that is not disposable,
    # and CI points it at the throwaway database it provisions.
    assert "PG_DB_NAME" in text
    assert "nexus" in text and "THROWAWAY" in text
    workflow = _read(ADONIS_WORKFLOW)
    assert "PG_DB_NAME: ci_edge" in workflow, "CI does not point the suite at a throwaway database"
    assert "npm test" in workflow, "CI does not run the adonis test command"
