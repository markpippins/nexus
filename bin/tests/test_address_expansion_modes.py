"""Guard tests for bin/check_address_expansion_modes.py (Ruling 26 Phase C).

The load-bearing property, and the reason this guard is not written the obvious
way: **an alias that declares a mode which legitimately carries no `targets`
must produce NO finding.**

Ruling 26 §5 states "targets absent or empty => alias-unmapped". Read literally
that fires on `all`, `all-roles`, `assembly`, `user` and `self` — five aliases
that §1-§4 DECIDED, under modes that have no target list by design. A guard that
raised `alias-unmapped` against them would be wrong on day one and would train
the team to add exclusions.

So `alias-unmapped` is a property of ONE mode (`targets-expand`), and the guard
branches on the declared mode. `TestTheLiteralRuling26RuleWouldBeWrong` pins
that difference explicitly so it cannot be reintroduced by "simplification".

File-only, no database — so it belongs in bin-tests per Ruling 26 §6.2.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = REPO / "config" / "roles"
spec = importlib.util.spec_from_file_location(
    "check_address_expansion_modes", REPO / "bin" / "check_address_expansion_modes.py")
chk = importlib.util.module_from_spec(spec)
sys.modules["check_address_expansion_modes"] = chk
spec.loader.exec_module(chk)

ROLE_KEYS = {"architect", "dba", "devops", "lead-engineer", "tester"}


def addr(**kw):
    base = {"kind": "alias"}
    base.update(kw)
    return base


class TestDecidedAliasesCarryNoTargets(unittest.TestCase):
    """Ruling 26 §1-§4. These have NO targets by design and must be silent."""

    def test_all_fanout_without_targets(self):
        f = chk.check_registry(
            {"all": addr(expansion={"mode": "fan-out-all-roles"})}, ROLE_KEYS)
        self.assertEqual(f, [], f"§1 `all` derives targets; got {f}")

    def test_all_roles_fanout_without_targets(self):
        f = chk.check_registry(
            {"all-roles": addr(expansion={"mode": "fan-out-all-roles"})}, ROLE_KEYS)
        self.assertEqual(f, [])

    def test_assembly_forum_broadcast_without_targets(self):
        f = chk.check_registry(
            {"assembly": addr(expansion={"mode": "forum-broadcast"})}, ROLE_KEYS)
        self.assertEqual(f, [], f"§2 `assembly` must NOT fan out; got {f}")

    def test_user_operator_notification_without_targets(self):
        f = chk.check_registry(
            {"user": addr(expansion={"mode": "operator-notification"})}, ROLE_KEYS)
        self.assertEqual(f, [])

    def test_self_writer_role_without_targets(self):
        f = chk.check_registry(
            {"self": addr(expansion={"mode": "writer-role"})}, ROLE_KEYS)
        self.assertEqual(f, [])

    def test_big_pickle_none_without_targets(self):
        f = chk.check_registry(
            {"big-pickle": addr(expansion={"mode": "none"}, delivery="never")},
            ROLE_KEYS)
        self.assertEqual(f, [])


class TestTheLiteralRuling26RuleWouldBeWrong(unittest.TestCase):
    """The false positive this guard exists to avoid, stated as a test."""

    DECIDED = ("all", "all-roles", "assembly", "user", "self")

    def _naive_targets_presence_rule(self, addresses):
        """What §5 read literally would do: WARN when `targets` is absent."""
        return sorted(n for n, e in addresses.items()
                      if e.get("kind") == "alias" and not e.get("targets"))

    def test_naive_rule_would_false_fire_on_all_five(self):
        addresses = {n: addr(expansion={"mode": "fan-out-all-roles"
                                        if n in ("all", "all-roles")
                                        else "forum-broadcast" if n == "assembly"
                                        else "operator-notification" if n == "user"
                                        else "writer-role"})
                     for n in self.DECIDED}
        # The naive rule condemns all five decided aliases.
        self.assertEqual(self._naive_targets_presence_rule(addresses),
                         sorted(self.DECIDED))
        # The shipped guard condemns none of them.
        self.assertEqual(chk.check_registry(addresses, ROLE_KEYS), [])

    def test_only_targets_expand_is_ever_unmapped(self):
        """`alias-unmapped` must be reachable from exactly one mode."""
        self.assertEqual(chk.MODES_WITH_TARGETS, {"targets-expand"})
        self.assertEqual(chk.MODES_FORBIDDEN_TARGETS,
                         chk.MODES - chk.MODES_WITH_TARGETS)
        self.assertEqual(chk.MODES_WITH_TARGETS | chk.MODES_FORBIDDEN_TARGETS,
                         chk.MODES)


class TestTargetsExpandInvariants(unittest.TestCase):
    def test_valid_targets_are_clean(self):
        f = chk.check_registry(
            {"leader": addr(expansion={"mode": "targets-expand"},
                            targets=["lead-engineer"])}, ROLE_KEYS)
        self.assertEqual(f, [])

    def test_multiple_targets_allowed(self):
        f = chk.check_registry(
            {"designer": addr(expansion={"mode": "targets-expand"},
                              targets=["architect", "dba"])}, ROLE_KEYS)
        self.assertEqual(f, [], "Ruling 26 §5 permits multiple targets")

    def test_absent_targets_is_warn_not_error(self):
        """The DECLARED unmapped state. Warn, never reject (Ruling 26 §5)."""
        f = chk.check_registry(
            {"admin": addr(expansion={"mode": "targets-expand"})}, ROLE_KEYS)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, chk.WARN)
        self.assertEqual(f[0].rule, "alias-unmapped")

    def test_empty_list_is_also_alias_unmapped(self):
        f = chk.check_registry(
            {"admin": addr(expansion={"mode": "targets-expand"}, targets=[])}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["alias-unmapped"])
        self.assertEqual(f[0].severity, chk.WARN)

    def test_alias_as_target_is_an_error(self):
        """Single-hop: this is what makes alias cycles inexpressible."""
        addresses = {
            "leader": addr(expansion={"mode": "targets-expand"},
                           targets=["watchdog"]),
            "watchdog": addr(expansion={"mode": "targets-expand"},
                             targets=["devops"]),
        }
        f = chk.check_registry(addresses, ROLE_KEYS)
        rules = {(x.address, x.rule) for x in f}
        self.assertIn(("leader", "alias-as-target"), rules)

    def test_self_reference_is_an_error(self):
        addresses = {"loop": addr(expansion={"mode": "targets-expand"},
                                  targets=["loop"])}
        f = chk.check_registry(addresses, ROLE_KEYS)
        self.assertIn("alias-as-target", [x.rule for x in f])

    def test_target_not_in_roles_json_is_an_error(self):
        f = chk.check_registry(
            {"leader": addr(expansion={"mode": "targets-expand"},
                            targets=["not-a-role"])}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["target-not-a-role"])

    def test_duplicate_targets_are_an_error(self):
        f = chk.check_registry(
            {"designer": addr(expansion={"mode": "targets-expand"},
                              targets=["dba", "dba"])}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["duplicate-target"])

    def test_targets_not_a_list_is_an_error(self):
        f = chk.check_registry(
            {"designer": addr(expansion={"mode": "targets-expand"},
                              targets="dba")}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["targets-not-a-list"])


class TestTargetsForbiddenOnDerivedModes(unittest.TestCase):
    """A `targets` key on a derived mode would be a silent second truth."""

    def test_fanout_all_roles_with_targets(self):
        f = chk.check_registry(
            {"all": addr(expansion={"mode": "fan-out-all-roles"},
                         targets=["architect"])}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["targets-on-mode-without-targets"])

    def test_forum_broadcast_with_targets(self):
        f = chk.check_registry(
            {"assembly": addr(expansion={"mode": "forum-broadcast"},
                              targets=["architect"])}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["targets-on-mode-without-targets"])


class TestUndeclaredModes(unittest.TestCase):
    def test_legacy_free_text_is_an_error(self):
        for stale in ("pending-architect (Phase C)",
                      "deterministic: writer's role from the record",
                      "n/a — a model identity carries no delivery obligation"):
            f = chk.check_registry(
                {"x": addr(expansion=stale)}, ROLE_KEYS)
            self.assertEqual([x.rule for x in f], ["undeclared-expansion-mode"], stale)

    def test_bare_token_string_is_accepted(self):
        f = chk.check_registry({"x": addr(expansion="targets-expand",
                                          targets=["dba"])}, ROLE_KEYS)
        self.assertEqual(f, [])

    def test_unknown_mode_token_is_an_error(self):
        f = chk.check_registry({"x": addr(expansion="fan-out-everything")}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["undeclared-expansion-mode"])

    def test_targets_without_a_mode_is_reported(self):
        f = chk.check_registry({"x": addr(expansion="pending", targets=["dba"])},
                               ROLE_KEYS)
        self.assertEqual(
            sorted(x.rule for x in f),
            ["targets-without-mode", "undeclared-expansion-mode"])

    def test_missing_expansion_is_an_error(self):
        f = chk.check_registry({"x": addr()}, ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["undeclared-expansion-mode"])


class TestTelemetryIsOutOfScope(unittest.TestCase):
    def test_telemetry_needs_no_mode(self):
        f = chk.check_registry(
            {"wr-conf-observer": {"kind": "telemetry", "delivery": "never"}},
            ROLE_KEYS)
        self.assertEqual(f, [])

    def test_telemetry_must_be_delivery_never(self):
        f = chk.check_registry(
            {"wr-conf-observer": {"kind": "telemetry", "delivery": "sometimes"}},
            ROLE_KEYS)
        self.assertEqual([x.rule for x in f], ["telemetry-must-be-never"])


class TestShippedRegistry(unittest.TestCase):
    """Runs against the real config files on this branch."""

    def _check(self):
        return chk.check_registry(*chk.load_registry(CONFIG_DIR))

    def test_shipped_registry_has_no_errors(self):
        errors = [f for f in self._check() if f.severity == chk.ERROR]
        self.assertEqual(errors, [], "\n".join(
            f"  {f.address}: [{f.rule}] {f.detail}" for f in errors))

    def test_every_alias_declares_a_mode(self):
        addresses, _ = chk.load_registry(CONFIG_DIR)
        undeclared = [n for n, e in addresses.items()
                      if e.get("kind") == "alias" and chk._mode_of(e) is None]
        self.assertEqual(undeclared, [])

    def test_shipped_registry_actually_loaded(self):
        """Guards against the vacuous-pass trap: an empty registry must raise.

        A caller that got the two registry paths the wrong way round used to
        yield {} and make every shipped-registry assertion below pass without
        looking at anything. load_registry now refuses an empty `addresses`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)
            (bad / "roles.json").write_text(json.dumps({"roles": {}}))
            (bad / "address-kinds.json").write_text(json.dumps({"addresses": {}}))
            with self.assertRaises(ValueError):
                chk.load_registry(bad)

    def test_unmapped_aliases_are_exactly_the_declared_ones(self):
        """`admin` is the only alias allowed to be unmapped, and only with a note.

        Note the predicate is mode-aware, NOT `not e["targets"]`. An earlier
        draft of this test used the naive rule and reported 7 unmapped aliases
        -- admin plus the six that legitimately carry no targets by design. That
        is the exact misreading this guard exists to prevent, and it slipped
        into the test for the guard; it is pinned here so it cannot come back.
        """
        addresses, _ = chk.load_registry(CONFIG_DIR)
        unmapped = sorted(
            n for n, e in addresses.items()
            if e.get("kind") == "alias"
            and chk._mode_of(e) in chk.MODES_WITH_TARGETS
            and not e.get("targets"))
        self.assertEqual(unmapped, ["admin"])
        self.assertIn("dbaNote", addresses["admin"],
                      "an unmapped alias must say WHY it is unmapped")

    def test_naive_targets_predicate_would_report_seven(self):
        """Pins the difference between the two predicates on real data."""
        addresses, _ = chk.load_registry(CONFIG_DIR)
        naive = sorted(n for n, e in addresses.items()
                       if e.get("kind") == "alias" and not e.get("targets"))
        self.assertEqual(len(naive), 7)
        self.assertEqual(sorted(set(naive) - {"admin"}),
                         ["all", "all-roles", "assembly", "big-pickle", "self", "user"])


class TestCli(unittest.TestCase):
    def test_clean_registry_exits_zero(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            import pathlib
            root = pathlib.Path(tmp)
            (root / "config" / "roles").mkdir(parents=True)
            (root / "config" / "roles" / "roles.json").write_text(
                json.dumps({"roles": {"dba": {"kind": "role"}}}))
            (root / "config" / "roles" / "address-kinds.json").write_text(
                json.dumps({"addresses": {
                    "leader": addr(expansion="targets-expand", targets=["dba"])}}))
            self.assertEqual(chk.main(["--repo", str(root)]), 0)
            self.assertEqual(chk.main(["--repo", str(root), "--strict"]), 0)

    def test_strict_promotes_the_sanctioned_warn(self):
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "config" / "roles").mkdir(parents=True)
            (root / "config" / "roles" / "roles.json").write_text(
                json.dumps({"roles": {"dba": {"kind": "role"}}}))
            (root / "config" / "roles" / "address-kinds.json").write_text(
                json.dumps({"addresses": {
                    "admin": addr(expansion="targets-expand")}}))
            self.assertEqual(chk.main(["--repo", str(root)]), 0,
                             "declared-unmapped must not fail the build")
            self.assertEqual(chk.main(["--repo", str(root), "--strict"]), 1)

    def test_missing_registry_is_a_tool_error(self):
        self.assertEqual(chk.main(["--repo", "/nonexistent/repo"]), 2)