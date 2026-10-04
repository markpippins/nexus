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
itself gains a CI-visible guard.

Ruling 22 (950c761e, thread 38da87a6): this guard's invariant is cross-surface
file<->DB, so it belongs in the DB-capable tier `guards-db` (services:
postgres), NOT a runtime skip. It no longer calls `skipTest`: with the
dependency absent it goes RED. A skipped check is not a pass. The manifest
entry in bin/tests-ci-manifest.json records the DB-tier debt until `guards-db`
lands; the exclusion is non-terminal and must be dropped once that job runs it.

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
    # The assembly surface (assembly-srv :3107) is NOT part of the guards-db
    # tier's contract -- that tier provides PostgreSQL, per Ruling 22 §7. When
    # assembly-srv is unreachable, verify-roles reports the alias criterion as
    # a WARN skip ("assembly unreachable — skipped", verify-roles.py's
    # unreachable branch -- keep this string in sync with it). That is an
    # environmental condition of the CI runner, not a provisioning state.
    # Tolerating it here is scoped to EXACTLY that marker: any provisioning
    # warn or fail (persona, cards, grants, registry shape) still goes red,
    # and a REACHABLE assembly missing the alias is a FAIL, not a warn.
    # Found on the tier's first real CI run (PR #719): the dev machine's live
    # assembly-srv had been silently satisfying this criterion.
    ASSEMBLY_UNREACHABLE_WARN = "assembly unreachable — skipped"

    def setUp(self):
        self.data, self.available = _verify_roles_json()
        if not self.available:
            # Ruling 22 (950c761e): fail closed. This guard's invariant is
            # cross-surface file<->DB, so its home is the DB tier `guards-db`,
            # not a runtime skip. Reporting green while asserting nothing is the
            # exact defect the ruling ends.
            self.fail(
                "verify-roles.py JSON not available (no psql / DB unreachable). "
                "Ruling 22: this guard runs in the `guards-db` tier — it may not "
                "runtime-skip. See bin/tests-ci-manifest.json and Ruling 22 "
                "(record 950c761e)."
            )

    def _fail_roles(self):
        """Results that are not fully green, EXCLUDING the one environmental warn.

        The assembly-unreachable skip is filtered here -- the single choke point
        both the delta test and the closure test read -- so the tolerance cannot
        drift between them. Everything else non-PASS is returned untouched.
        """
        return {
            r["role"]: r["detail"]
            for r in self.data["results"]
            if r["status"] != "PASS"
            and not (
                r["status"] == "WARN"
                and (r.get("detail") or "").strip() == self.ASSEMBLY_UNREACHABLE_WARN
            )
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

        One environmental exception, scoped as tightly as it can be: on the
        guards-db CI runner there is no assembly-srv, so the alias criterion
        comes back as the assembly-unreachable WARN skip (see
        ASSEMBLY_UNREACHABLE_WARN). A closed role is accepted as PASS, or as a
        WARN carrying ONLY that marker -- every provisioning warn or fail still
        goes red here, and on any machine where assembly-srv IS reachable, a
        missing alias is a FAIL (verify-roles' unreachable branch), so the
        closure keeps its teeth on dev machines too.
        """
        by_role = {r["role"]: r for r in self.data["results"]}
        for role in CLOSED_ROLES_MUST_PASS:
            self.assertIn(
                role,
                by_role,
                f"'{role}' is declared closed but verify-roles does not even "
                f"evaluate it — the registry key may have been removed",
            )
            status = by_role[role]["status"]
            detail = (by_role[role].get("detail") or "").strip()
            environmental_skip = (
                status == "WARN" and detail == self.ASSEMBLY_UNREACHABLE_WARN
            )
            self.assertTrue(
                status == "PASS" or environmental_skip,
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


def _load_verify_roles_module():
    """Import bin/verify-roles.py (hyphenated name) without running main()."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_roles", VERIFY_ROLES)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestUnknownRolesExclusionIsPinned(unittest.TestCase):
    """DB-FREE pin for the Ruling 19 address-kinds exclusion.

    This pins the predicate itself so a future edit cannot silently
    reintroduce a registered alias (e.g. `big-pickle`) as unknown role
    vocabulary — the exact state that made combined #715+#712 red. It needs no
    database and never skips, so it runs in every environment.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_verify_roles_module()

    def test_registered_nonrole_address_is_not_unknown(self):
        got = self.mod.compute_unknown_roles(
            ["architect", "big-pickle"], {"architect"}, {"big-pickle"}
        )
        self.assertEqual(got, [])

    def test_unregistered_name_is_still_unknown(self):
        # The exclusion must not be vacuously permissive.
        got = self.mod.compute_unknown_roles(
            ["architect", "Phantom"], {"architect"}, {"big-pickle"}
        )
        self.assertEqual(got, ["Phantom"])

    def test_removing_the_registration_reexposes_the_name(self):
        # Proves the exclusion is load-bearing: without the address-kinds entry
        # the same name is reported again.
        got = self.mod.compute_unknown_roles(["architect", "big-pickle"], {"architect"})
        self.assertEqual(got, ["big-pickle"])

    def test_expected_role_is_never_unknown(self):
        got = self.mod.compute_unknown_roles(
            ["architect", "dba"], {"architect", "dba"}, set()
        )
        self.assertEqual(got, [])

    def test_ci_ephemeral_roles_are_excluded(self):
        got = self.mod.compute_unknown_roles(
            ["architect", "wr-conf-016-abc"], {"architect"}, set()
        )
        self.assertEqual(got, [])

    def test_registered_addresses_default_to_empty_set(self):
        # The absent-file path: no address-kinds registration -> unchanged
        # behaviour (the forward-compatible no-op on #712 alone).
        got = self.mod.compute_unknown_roles(["architect", "big-pickle"], {"architect"})
        self.assertEqual(got, ["big-pickle"])

    def test_repo_wiring_registry_shape(self):
        """Integration pin across the real repo files — never vacuous.

        Post-#715 (address-kinds.json present): big-pickle is registered as an
        address-kind, absent from roles.json, and yields no unknownRoles.
        Pre-#715: the registry does not exist yet, so big-pickle is still a
        roles.json entry. Either way this asserts a real branch shape — it does
        NOT runtime-skip (Ruling 22 forbids silent skips).
        """
        roles = json.load(
            open(os.path.join(REPO, "config", "roles", "roles.json"))
        )["roles"]
        ak_path = os.path.join(REPO, "config", "roles", "address-kinds.json")
        if os.path.exists(ak_path):
            addresses = json.load(open(ak_path))["addresses"]
            self.assertIn("big-pickle", addresses)
            self.assertNotIn("big-pickle", roles)
            self.assertEqual(
                self.mod.compute_unknown_roles(
                    ["architect", "big-pickle"], roles, set(addresses)
                ),
                [],
            )
        else:
            self.assertIn(
                "big-pickle",
                roles,
                "pre-#715 branch must still declare big-pickle in roles.json",
            )


if __name__ == "__main__":
    unittest.main()
