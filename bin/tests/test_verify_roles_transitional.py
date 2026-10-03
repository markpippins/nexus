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

Closing path (owned by the merge-train owner / architect):
  1. tackle.roles gains lowercase dba; DBA/Rover rows retired (R9-gated).
  2. assembly.users alias DBA -> dba (assembly owner).
  3. THEN delete the DELTA_* constants below and flip the guard
     to a hard equality assertion: post-closure the only allowed state is
     all-PASS with zero warnings. This guard refusing to forget is the point.

STEP 1 PARTIALLY CLOSED 2026-10-02 (DBA record d5c5b28e / R1 d5c5b28e).
The lowercase `dba` key is now fully provisioned in the database, so the
`dba` half of the delta is CLOSED and is now asserted as a POSITIVE:

  - `tackle.roles` gained `dba` (34 rows, was 33).
  - `tackle.role_tool_access` gained the 5 wildcard MCP grants copied from
    the uppercase `DBA` row, so the canonical key is not tool-less.
  - `tackle.prompts` gained `database-admin` v1, copied byte-identically from
    `DBA` (md5 4543f973ee90a9d1e8a48630851b316c on both rows).

`rolesMissingFromDB` is therefore now `[]` and `dba` PASSes.

WHAT IS STILL OPEN, and why this guard is not yet the final all-PASS form:

  - STEP 2 is NOT done. `assembly.users` still carries `DBA` (1ea49b6d) and
    `Rover`, so `unknownRoles` remains `['DBA','Rover']`. That is the assembly
    owner's action (retire Rover, do not rename — Ruling f8bd88f6).
  - The uppercase `DBA`/`Rover` ROWS remain in tackle.roles. They are heavily
    FK-referenced (role_memory 17, role_tool_access 5, config_bundle 2, prompts 1)
    under `NO ACTION` FKs, so neither rename nor delete is safe without an
    explicit migration. Recorded as supervisor/architect residue.
  - `sound-technician` still FAILs on `persona missing (tackle.prompts)` — a
    pre-existing main-branch failure, not #712's delta.

Ruling 17 (c16b625b) discipline: a criterion with no enumerable form enforces
nothing. `CLOSED_ROLES_MUST_PASS` exists precisely so that emptying
`DELTA_FAIL_ROLES` does NOT silently remove this guard's teeth — `dba` is now
asserted to PASS, so deleting the provisioning makes this guard go RED.
Negative control recorded in the R2 record for this change.

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
# Roles whose database provisioning this change completed. These are asserted
# to PASS. Without this, emptying DELTA_FAIL_ROLES would leave the guard with
# no assertion at all about `dba` — it would pass whether or not `dba` exists.
CLOSED_ROLES_MUST_PASS = ("dba",)
# Pre-existing on main (29/30 there): not #712's delta, but a permanent
# non-PASS would hide behind it, so it is asserted explicitly too.
PREEXISTING_FAIL_ROLES = {"sound-technician": "persona missing (tackle.prompts)"}
# STILL OPEN — step 2 of the closing path, assembly owner's action.
DELTA_UNKNOWN_ROLES = ["DBA", "Rover"]
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

    def test_unknown_roles_are_exactly_the_capitalized_pair(self):
        self.assertEqual(
            sorted(self.data["unknownRoles"]),
            DELTA_UNKNOWN_ROLES,
            "unexpected DB-only role vocabulary — two-sided normalization state changed",
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
