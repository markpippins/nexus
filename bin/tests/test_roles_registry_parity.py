#!/usr/bin/env python3
"""Guard: config/roles/roles.json ↔ tackle.roles parity — and the *wrong table*
clarification from Ruling 19.

Ruling 19 (record f054ba36) reported: "roles.json<->tackle.roles parity invariant
is UNGATED and may name the wrong table (code syncs tackle.roles<->nebula.roles)."
This module gates it and names the correct surface explicitly.

WHY THE TABLE MATTERS
---------------------
The DELIVERY vocabulary is `tackle.roles`. That is the set the whole pipeline
keys on: `bin/check-inbox.sh` builds `to:` tags from it, nebula-mcp's
`normalizeRole()` mirrors it, and `bin/verify-roles.py` compares roles.json
against it. Ruling 8's undeliverable-address hazard is defined over THIS set.

`nebula.roles` is a DIFFERENT surface. It is a bitemporal capability VIEW over
`nebula.roles_history` (relkind `v`, not a table — see pitfall in the tackle
seed: a FK cannot reference it), read by harness admission for `owns_domains`
(`moleculer/harness/services/db.ts`). It is populated only for roles created
through the role-creation transaction (`moleculer/tackle/services/db.ts`,
"nebula.roles dual-registry sync, Gap 7"), so it is a strict SUBSET of
tackle.roles today. Asserting equality against nebula.roles would be asserting
the wrong table — the exact Ruling 19 failure mode.

WHAT IS ASSERTED (three directions, each named against its table)
-----------------------------------------------------------------
  A1. every `roles.json` key exists in `tackle.roles`  (registry ⊆ delivery)
  A2. every live `tackle.roles` name is registered SOMEWHERE — in `roles.json`
      or in `config/roles/address-kinds.json`  (delivery ⊆ registry)
  A3. every `nebula.roles` name exists in `tackle.roles`  (capability ⊆ delivery)

A2 is deliberately union-based rather than "== roles.json": Ruling 13/19 move
non-role addresses (`big-pickle` → kind=alias, future telemetry) OUT of
roles.json into address-kinds.json while they must remain live in tackle.roles.
An equality assertion would have broken under Ruling 19 by design; the union is
the invariant that actually holds across the change.

CI convention: DB-touching guards SKIP when no database is reachable
(bin/tests-ci-manifest.json's documented policy). The pure checkers below are
fixture-free so the guard cannot pass vacuously when the DB is absent — the
vacuity suite exercises each checker against synthetic drift.

Run: python3 -m pytest bin/tests/test_roles_registry_parity.py
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROLES_FILE = REPO / "config" / "roles" / "roles.json"
ADDRESS_KINDS_FILE = REPO / "config" / "roles" / "address-kinds.json"
PG_DSN = os.environ.get("NEXUS_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

# CI-ephemeral roles minted by the wr-conf E2E grant suites on throwaway plans.
# Excluded by the same convention as bin/verify-roles.py (CI_EPHEMERAL_PREFIXES).
EPHEMERAL_PREFIXES = ("wr-conf-",)

TACKLE_ROLES_SQL = "SELECT name FROM tackle.roles ORDER BY name"
NEBULA_ROLES_SQL = "SELECT name FROM nebula.roles ORDER BY name"


# ── pure checkers (fixture-free: synthetic vocabularies exercise them) ────────

def load_registry_roles(path=ROLES_FILE):
    return set(json.loads(Path(path).read_text())["roles"].keys())


def load_address_kinds(path=ADDRESS_KINDS_FILE):
    """Non-role registered addresses. Absent file == empty (pre-Ruling 13)."""
    p = Path(path)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()).get("addresses", {}).keys())


def is_ephemeral(name, prefixes=EPHEMERAL_PREFIXES):
    return any(name.startswith(p) for p in prefixes)


def registry_not_in_delivery(registry_keys, tackle_names):
    """A1 — declared roles that the delivery vocabulary lacks."""
    return sorted(set(registry_keys) - set(tackle_names))


def unregistered_live_roles(tackle_names, registry_keys, address_kind_keys,
                            prefixes=EPHEMERAL_PREFIXES):
    """A2 — live roles registered in neither registry."""
    registered = set(registry_keys) | set(address_kind_keys)
    return sorted(
        n for n in tackle_names
        if not is_ephemeral(n, prefixes) and n not in registered
    )


def capability_not_in_delivery(nebula_names, tackle_names):
    """A3 — capability-registry names absent from the delivery vocabulary."""
    return sorted(set(nebula_names) - set(tackle_names))


# ── live DB access (skips when unavailable) ──────────────────────────────────

def _psql(sql):
    out = subprocess.run(
        ["psql", PG_DSN, "-tA", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "psql failed")
    return [ln for ln in out.stdout.splitlines() if ln]


def _tables():
    if shutil.which("psql") is None:
        return None, None
    try:
        return _psql(TACKLE_ROLES_SQL), _psql(NEBULA_ROLES_SQL)
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return None, None


class _DbCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tackle, cls.nebula = _tables()
        if cls.tackle is None:
            raise unittest.SkipTest(
                "no reachable database (no psql / connection refused) — "
                "CI convention: DB-touching guards skip"
            )
        cls.registry = load_registry_roles()
        cls.address_kinds = load_address_kinds()


class TestRegistryDeliveryParity(_DbCase):
    def test_a1_every_declared_role_exists_in_tackle_roles(self):
        stray = registry_not_in_delivery(self.registry, self.tackle)
        self.assertEqual(
            stray, [],
            f"roles.json declares {stray} but tackle.roles lacks them — a "
            f"declared role with no delivery row; verify-roles.py would report "
            f"them in rolesMissingFromDB",
        )

    def test_a2_every_live_role_is_registered_somewhere(self):
        ghosts = unregistered_live_roles(
            self.tackle, self.registry, self.address_kinds
        )
        self.assertEqual(
            ghosts, [],
            f"tackle.roles carries {ghosts}, registered in neither roles.json "
            f"nor address-kinds.json — an unregistered delivery address "
            f"(the Ruling 8 / Ruling 13 hazard); verify-roles.py reports these "
            f"as unknownRoles",
        )


class TestNebulaIsNotTheDeliveryTable(_DbCase):
    def test_a3_capability_registry_is_a_subset_of_delivery(self):
        missing = capability_not_in_delivery(self.nebula, self.tackle)
        self.assertEqual(
            missing, [],
            f"nebula.roles names {missing} absent from tackle.roles — a role "
            f"holds capability metadata (owns_domains) but has no delivery row. "
            f"nebula.roles is a bitemporal VIEW (capability), NOT the delivery "
            f"vocabulary; only this direction must hold, not equality "
            f"(Ruling 19: do not compare roles.json against nebula.roles)",
        )

    def test_delivery_is_a_superset_not_an_equality(self):
        # Pins the Ruling 19 finding: the two tables are NOT equal, and a guard
        # asserting equality would be asserting the wrong invariant. If they
        # ever become equal that is fine, but the guard must not REQUIRE it.
        self.assertTrue(
            set(self.nebula) <= set(self.tackle),
            "capability registry escaped the delivery vocabulary",
        )


# ── meta: the checkers must detect drift, not merely pass ────────────────────

class TestCheckersAreNotVacuous(unittest.TestCase):
    def test_a1_detects_declared_role_missing_from_db(self):
        self.assertEqual(
            registry_not_in_delivery({"architect", "ghost"}, ["architect"]),
            ["ghost"],
        )

    def test_a2_detects_unregistered_live_role(self):
        got = unregistered_live_roles(
            ["architect", "Mystery"], {"architect"}, set()
        )
        self.assertEqual(got, ["Mystery"])

    def test_a2_excludes_ephemeral_and_honours_address_kinds(self):
        self.assertEqual(
            unregistered_live_roles(
                ["wr-conf-016-abc", "big-pickle"], set(), {"big-pickle"}
            ),
            [],
        )

    def test_a3_detects_capability_without_delivery_row(self):
        self.assertEqual(
            capability_not_in_delivery(["engineer", "phantom"], ["engineer"]),
            ["phantom"],
        )


if __name__ == "__main__":
    unittest.main()
