"""Decision 32 part (b) — registry-closure tests for the resolution twin.

Decision 32 (record 4c9afc09-c591-4938-ae88-23ceb3dde284, Option 3) makes the
contract of record for the resolution twin a UNION:

  (a) the COMPILED TypeSpec contract
      (typespec/v1/resolution-srv/generated/schema/openapi.yaml) — fixed
      routes only, sdk-drift-guarded (check_drift.py side, Ruling 4); plus
  (b) the tables.ts enumeration — per-table GET pairs.

The twin generates ONE LITERAL alias per (method, path) from its verbatim
tables.ts copy — never a `:table` param route. These tests pin the (b) half
at the repo level:

  1. the twin's src/ tree is a byte-identical copy of the incumbent's
     (tables.ts registry, the per-table route loop, the health route);
  2. every table literal has BOTH generated aliases (list + :id) in the
     twin's gateway;
  3. the alias census is exact: 2 per table + 2 fixed GET literals
     (/health, /api/meta) + 2 method-wildcard catch-alls;
  4. no param-style table route exists (Decision 32: surface stays
     enumerable — a `:table` route would shadow /api/meta and hide the
     registry from extract_routes.py).

The drift half of the union equality (twin surface == compiled-fixed ∪
tables-enumerated) lives in tools/api-docs/check_drift.py; the two guards
complement each other and both fail closed.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TWIN = os.path.join(ROOT, "moleculer", "resolution")
INCUMBENT = os.path.join(ROOT, "typescript", "resolution-srv")

FIXED_LITERALS = ['"GET /health"', '"GET /api/meta"']
CATCH_ALLS = ['"* /api"', '"* /api/(.*)"']


def read(rel):
    with open(os.path.join(TWIN, rel)) as f:
        return f.read()


def incumbent_read(rel):
    with open(os.path.join(INCUMBENT, rel)) as f:
        return f.read()


def twin_tables():
    src = read("src/tables.ts")
    return re.findall(r'table:\s*"([a-z0-9_]+)"', src)


def test_twin_tables_ts_byte_identical_to_incumbent():
    assert read("src/tables.ts") == incumbent_read("src/tables.ts"), (
        "moleculer/resolution/src/tables.ts drifted from "
        "typescript/resolution-srv/src/tables.ts — the twin's registry IS the "
        "incumbent's registry; copy verbatim, never fork"
    )


def test_twin_route_stack_byte_identical_to_incumbent():
    for rel in ("src/routes/resolution.ts", "src/routes/health.ts"):
        assert read(rel) == incumbent_read(rel), (
            f"moleculer/resolution/{rel} drifted from "
            f"typescript/resolution-srv/{rel} — dispatch-through-Express "
            f"requires the verbatim incumbent route stack"
        )


def test_every_table_has_both_literal_aliases():
    aliases = read("services/api.service.ts")
    missing = []
    for table in twin_tables():
        if f'"GET /api/{table}":' not in aliases:
            missing.append(f"GET /api/{table}")
        if f'"GET /api/{table}/:id":' not in aliases:
            missing.append(f"GET /api/{table}/:id")
    assert not missing, (
        f"tables.ts entries without a generated literal alias: {missing} — "
        f"regenerate the alias block from tables.ts (Decision 32: one literal "
        f"alias per table, no :table param route)"
    )


def test_alias_census_is_exact():
    aliases = read("services/api.service.ts")
    tables = twin_tables()
    get_api = re.findall(r'"GET /api/[a-z0-9_]+(?:/:id)?":', aliases)
    # This pattern counts exactly: 2 per table + the /api/meta fixed
    # literal. (GET /health and the * catch-alls live outside the pattern
    # and are pinned by test_fixed_and_catchall_aliases_present.) Note
    # /api/health is deliberately NOT aliased — parity case (the incumbent
    # 404s it via the method guard's unknown_table; the TypeSpec fixed
    # routes are /health + /api/meta only).
    expected = 2 * len(tables) + 1
    assert len(get_api) == expected, (
        f"alias census {len(get_api)} != 2*tables({len(tables)}) + 1 fixed "
        f"(GET /api/meta) — an alias was added or removed without the "
        f"registry moving (or vice versa)"
    )


def test_no_param_table_route_in_twin():
    aliases = read("services/api.service.ts")
    assert '"GET /api/:table"' not in aliases, (
        "twin carries a :table param route — Decision 32 mandates LITERAL "
        "per-table aliases (enumerable surface, no /api/meta shadowing)"
    )


def test_fixed_and_catchall_aliases_present():
    aliases = read("services/api.service.ts")
    for literal in FIXED_LITERALS + CATCH_ALLS:
        assert f"{literal}: " in aliases or f"{literal}:" in aliases, (
            f"missing {literal} alias — the catch-alls forward unmatched /api "
            f"traffic into the verbatim Express stack (405 boundary + "
            f"Express-default 404s); removing them reverts the twin to the "
            f"moleculer-web JSON 404 envelope (live-canary-provided drift)"
        )
