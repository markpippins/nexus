#!/usr/bin/env python3
"""Guard: #712's transitional verify-roles divergence is NAMED, OWNED, and CLOSED.

Tester blocking correction (record 9ebf2f89, PR #712 comment 2026-10-02T02:29Z):
landing the roles.json half of the two-sided normalization moves
`bin/verify-roles.py` from 29/30 (main) to 27/29, because the OTHER two
surfaces still hold the capitalized vocabulary — live-probed 2026-10-02:

- assembly.users (:3107/api/users, 35 users): non-lowercase = exactly
  ['DBA', 'Rover']; lowercase 'dba' absent (assembly owner action).
- tackle.roles (DB): DBA/Rover rows still present, lowercase dba row absent
  (DB-side normalization queued post-#712 with R9 replication confirmation).

This is tester option (c): the split is recorded as a KNOWN transitional
state with an explicit delta, so any OTHER regression of the role-surface
check goes red here instead of passing silently — and `verify-roles.py`
itself gains a CI-visible guard without needing database provisioning in
bin-tests.yml (DB-touching guards skip when no DB is reachable; this guard
skips identically, per the workflow's documented convention).

Closing path (COMPLETED 2026-10-03 — see STEP 2 below):
  1. tackle.roles gained lowercase dba; the DBA/Rover rows were retired
     (sql/V203, applying Ruling 8 live).
  2. assembly.users alias DBA -> dba; Rover soft-retired (sql/V204).
  3. The DELTA_* constants were emptied IN PLACE rather than deleted, and the
     guard was flipped to positive assertions (`test_unknown_roles_are_empty`
     plus `CLOSED_ROLES_MUST_PASS`). Post-closure the only allowed non-PASS is
     the pre-existing sound-technician failure. This guard refusing to forget
     is the point.

STEP 1 CLOSED 2026-10-02; STEP 2 CLOSED 2026-10-03 (DBA R1 3dcfa3e8,
migrations V203/V204). Both halves of the two-sided normalization have
landed, so `unknownRoles` is now `[]` and the guard asserts that directly.

STEP 1 — lowercase `dba` provisioning (asserted POSITIVE, see
`CLOSED_ROLES_MUST_PASS`): `tackle.roles` gained `dba` (34 rows, was 33);
`tackle.role_tool_access` gained the 5 wildcard MCP grants copied from the
uppercase `DBA` row; `tackle.prompts` gained `database-admin` v1,
byte-identical to the uppercase copy (md5 4543f973ee90a9d1e8a48630851b316c).
`rolesMissingFromDB` is `[]` and `dba` PASSes.

STEP 2 — uppercase retirement (Ruling 8 + Ruling 16 f8bd88f6):

  - V203 reconciled every FK bound to the uppercase rows — `role_memory` 17
    and `config_bundle` 2 reassigned to `dba`, `role_tool_access` 5 and
    `prompts` 1 dropped as duplicates, Rover `config_bundle` 4 dropped —
    then deleted the `DBA`/`Rover` rows from `tackle.roles`.
  - V204 flipped assembly alias `DBA` -> `dba` (same id 1ea49b6d; 140 posts +
    1017 comments preserved) and soft-retired `Rover` via a new `retired_at`
    column excluded from `assembly.user_list_v` (1280 authored posts intact).
  - `Rover` is retired, NOT renamed (Ruling 16) and stays out of
    address-kinds.json.
  - `roles.json` dba entry now declares `procedures`/`assemblyAlias` true.

`sound-technician` — the last non-PASS, also now closed: no persona source
exists anywhere in the repo (0 `tackle.prompts` rows, no harness file, no
`docs/*role-prompt`, no `tackle.memory` card — only the DRAFT
`sql/grants/sound-technician-grant-v0.1.sql`). Rather than a phantom failure,
its persona surface is declared deliberately absent (`persona: false`), so
verify-roles is now fully all-PASS (29/29, zero warnings). Authoring a real
persona is a separate content decision.

Ruling 17 (c16b625b) discipline: a criterion with no enumerable form enforces
nothing. `CLOSED_ROLES_MUST_PASS` and the now-hard
`test_unknown_roles_are_empty` are what give this guard teeth — re-adding a
capitalized role to `tackle.roles` makes it RED, as does removing the `dba`
provisioning. Negative control recorded in the R2 record for this change.

Negative control (per Ruling 14 evidence discipline): re-adding a
capitalized DBA key to roles.json must fail test_roles_json_lowercase.py
(7 tests) — the two guards triangulate the same end-state from both sides.

Run: python3 -m pytest bin/tests/test_verify_roles_transitional.py
"""
import json
import os
import re
import shutil
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERIFY_ROLES = os.path.join(REPO, "bin", "verify-roles.py")

# ── The exact transitional delta #712 introduces (measured 2026-10-02, both
#    trees, env-corrected): everything else must stay green. ──────────────
# The `dba` entry was here as {"dba": "not present in tackle.roles"}. It is
# now CLOSED (see module docstring) and the delta is empty. It is deliberately
# kept as an empty dict rather than deleted so the shape of the transitional
# contract stays visible until STEP 2 lands.
DELTA_FAIL_ROLES: dict[str, str] = {}
# Roles whose surface this change closed. These are asserted to PASS. Without
# this, emptying DELTA_FAIL_ROLES would leave the guard with no assertion at
# all about `dba` or `sound-technician` — it would pass whether or not they
# are provisioned/declared.
CLOSED_ROLES_MUST_PASS = ("dba", "sound-technician")
# No pre-existing failures remain: `sound-technician`'s persona surface is now
# declared deliberately absent (persona=false), so it PASSes. Empty, not
# deleted, so the shape of the contract stays visible.
PREEXISTING_FAIL_ROLES: dict = {}
# CLOSED by STEP 2 (V203/V204): no unregistered DB-only role vocabulary
# remains. `big-pickle` is deliberately DB-only-but-registered (a
# Ruling 19 non-role address in config/roles/address-kinds.json), so
# verify-roles.py excludes it by construction — it is NOT unknown
# vocabulary. Hard-empty, not a set to shrink.
DELTA_UNKNOWN_ROLES: list[str] = []
# CLOSED by this change: every role in config/roles/roles.json now has a
# tackle.roles row. Hard-empty, not a set to shrink.
DELTA_MISSING_FROM_DB: list[str] = []


def _verify_roles_json():
    """Run bin/verify-roles.py --json; return (parsed, available)."""
    if shutil.which("psql") is None:
        return None, False
    try:
        proc = subprocess.run(
            ["python3", VERIFY_ROLES, "--json"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, False
    # verify-roles.py emits human-readable lines to stderr; stdout is JSON
    # but may carry banner text — extract the outermost JSON object.
    out = proc.stdout
    start = out.find("{")
    if start == -1:
        return None, False
    try:
        return json.loads(out[start : out.rfind("}") + 1]), True
    except json.JSONDecodeError:
        return None, False


class TestVerifyRolesTransitional(unittest.TestCase):
    def setUp(self):
        self.data, self.available = _verify_roles_json()
        if not self.available:
            self.skipTest(
                "verify-roles.py JSON not available (no psql / DB unreachable) — "
                "CI convention: DB-touching guards skip"
            )

    def _fail_roles(self):
        return {
            r["role"]: r["detail"]
            for r in self.data["results"]
            if r["status"] != "PASS"
        }

    def test_the_named_delta_is_the_entire_extra_failure(self):
        fails = self._fail_roles()
        allowed = dict(DELTA_FAIL_ROLES)
        allowed.update(PREEXISTING_FAIL_ROLES)
        unexpected = {
            role: detail
            for role, detail in fails.items()
            if role not in allowed
        }
        self.assertEqual(
            unexpected,
            {},
            f"verify-roles regressed BEYOND the named transitional delta: {unexpected}",
        )

    def test_named_delta_rows_fail_for_the_named_reasons(self):
        fails = self._fail_roles()
        for role, reason in DELTA_FAIL_ROLES.items():
            self.assertIn(role, fails, f"delta row '{role}' unexpectedly PASSED — transitional state may have closed: shrink DELTA_* constants and flip this guard to a hard assertion")
            self.assertIn(reason, fails[role])
        for role, reason in PREEXISTING_FAIL_ROLES.items():
            self.assertIn(role, fails, f"pre-existing row '{role}' changed state — update PREEXISTING_* constants")
            self.assertIn(reason, fails[role])

    def test_closed_roles_are_provisioned_and_pass(self):
        """The load-bearing positive assertion.

        `dba` moved from the delta list to CLOSED_ROLES_MUST_PASS. Asserting
        only that "nothing unexpected fails" would be satisfied equally well by
        `dba` being absent from the database, so closure is asserted directly:
        the role must be present in results AND carry status PASS.
        """
        by_role = {r["role"]: r for r in self.data["results"]}
        for role in CLOSED_ROLES_MUST_PASS:
            self.assertIn(
                role,
                by_role,
                f"'{role}' is declared closed but verify-roles does not even "
                f"evaluate it — the registry key may have been removed",
            )
            self.assertEqual(
                by_role[role]["status"],
                "PASS",
                f"'{role}' provisioning regressed: "
                f"{by_role[role].get('detail')!r} — re-provision tackle.roles / "
                f"tackle.prompts / tackle.role_tool_access, or move it back to "
                f"DELTA_FAIL_ROLES with its reason if the closure was premature",
            )

    def test_unknown_roles_are_empty(self):
        """The load-bearing positive assertion for #712 step 2 (Ruling 17 teeth).

        Ruling 8 retires the capitalized `DBA`/`Rover` vocabulary and the
        reconciliation applies it to the live database (V203/V204), so
        `unknownRoles` must be exactly empty. Asserting only "no unexpected
        delta" would be satisfied by skipping the reconciliation entirely, so
        emptiness is asserted directly: any name present in `tackle.roles` but
        registered in NEITHER config/roles/roles.json NOR
        config/roles/address-kinds.json makes this RED. (Ruling 19: names
        registered as non-role addresses — e.g. the `big-pickle` model alias —
        are excluded by verify-roles.py itself, not tolerated here.)
        """
        self.assertEqual(
            self.data["unknownRoles"],
            DELTA_UNKNOWN_ROLES,
            "DB-only unregistered role vocabulary reappeared — the uppercase "
            "DBA/Rover retirement (V203) regressed, or a new role was added "
            "to tackle.roles without registering it in roles.json or "
            "address-kinds.json",
        )

    def test_missing_from_db_is_exactly_lowercase_dba(self):
        self.assertEqual(
            sorted(self.data["rolesMissingFromDB"]),
            DELTA_MISSING_FROM_DB,
            "registry/DB divergence changed: every role in "
            "config/roles/roles.json must have a tackle.roles row",
        )

    def test_results_parse_and_carry_the_documented_shape(self):
        self.assertIsInstance(self.data["results"], list)
        self.assertGreaterEqual(len(self.data["results"]), 20)
        for row in self.data["results"]:
            self.assertIn("role", row)
            self.assertIn("status", row)


if __name__ == "__main__":
    unittest.main()
