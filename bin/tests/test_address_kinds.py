#!/usr/bin/env python3
"""Guard: config/roles/address-kinds.json — non-role address registry (Ruling 13).

Ruling 13 (de5d538f, 2026-10-02, thread f60b8eb4) gives addresses a KIND:
`role` (delivery obligation; reject only if unregistered — #693's scope),
`alias` (expand at write, never reject), `telemetry` (recorded, never
delivered). Non-role addresses must not live in roles.json.roles (keys there
must match tackle.roles exactly), so they register in address-kinds.json.

This guard locks the R13-mandated registrations:

- wr-conf-observer = telemetry: the archival ×299 stream, registered with the
  completion marker for the 2026-09-21 close, never delivered (decision 1).
  Retagging was explicitly forbidden by the ruling; this entry is the
  registration the ruling ordered — it is not a role and must never be
  mistaken for one.
- the nine broadcast/legacy alias addresses (decisions 2 and 4).

Ruling 17 C5 — rejectable IFF in NEITHER registry. Everything registered here
is therefore NOT rejectable, and the two registries must stay disjoint so the
"neither" test is well-defined.

Architect ff407906 decided the Phase C expansion targets, so `mechanism` is
recorded per alias (`tag-fanout` vs `service-route`) rather than left implicit:
without it an implementer could reasonably build forum-ingest as a tag copy.
Addresses the architect did NOT decide keep mechanism=null — recorded as
undecided rather than guessed.

Evidence base: DBA scoping record ab18534c (2026-10-02); corpus census via
live probe (wr-conf-observer ×299; alias occurrences ×41 across 9 addresses).

Run: python3 -m pytest bin/tests/test_address_kinds.py
"""
import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KINDS = os.path.join(REPO, "config", "roles", "address-kinds.json")
ROLES = os.path.join(REPO, "config", "roles", "roles.json")

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
VALID_KINDS = ("alias", "telemetry")

# Ruling 13 decisions 2 + 4.
R13_ALIASES = (
    "all", "all-roles", "assembly", "self", "user",
    "admin", "leader", "designer", "watchdog",
)

# Architect ff407906 §5.4 decided exactly these five; the rest stay undecided.
# `to:assembly` is a SERVICE ROUTE to assembly-srv, not a tag fan-out — building
# forum-ingest as a tag copy is the specific mistake this field prevents.
DECIDED_MECHANISMS = {
    "all": "tag-fanout",          # expand-and-replace, bounded by the predicate
    "all-roles": "tag-fanout",    # kind=role AND nebulaCheck=true
    "assembly": "service-route",  # assembly-srv, NOT tag fan-out
    "user": "service-route",      # operator identity
    "self": "deterministic",      # writer's own role
}
VALID_MECHANISMS = ("tag-fanout", "service-route", "deterministic")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestAddressKinds(unittest.TestCase):
    def setUp(self):
        self.spec = _load(KINDS)
        self.addresses = self.spec["addresses"]

    def test_every_key_is_lowercase_kebab(self):
        bad = [k for k in self.addresses if not KEBAB.fullmatch(k)]
        self.assertEqual(bad, [], f"non-lowercase-kebab address keys: {bad}")

    def test_kind_is_alias_or_telemetry_only(self):
        bad = {
            k: v.get("kind")
            for k, v in self.addresses.items()
            if v.get("kind") not in VALID_KINDS
        }
        self.assertEqual(
            bad, {}, f"kind must be alias|telemetry here (kind=role lives in roles.json): {bad}"
        )

    def test_no_collision_with_role_registry(self):
        roles = set(_load(ROLES)["roles"])
        overlap = sorted(set(self.addresses) & roles)
        self.assertEqual(
            overlap, [], f"address registered as both a role and a non-role kind: {overlap}"
        )

    def test_wr_conf_observer_registered_as_telemetry_with_completion_marker(self):
        self.assertIn(
            "wr-conf-observer",
            self.addresses,
            "R13 decision 1: wr-conf-observer must stay registered (archival ×299 stream)",
        )
        entry = self.addresses["wr-conf-observer"]
        self.assertEqual(entry.get("kind"), "telemetry")
        self.assertEqual(entry.get("delivery"), "never", "telemetry is never delivered")
        self.assertIn("completionMarker", entry, "R13 requires the 09-21 close completion marker")
        self.assertIn("ruling", entry)

    def test_telemetry_entries_carry_archival_evidence(self):
        for name, entry in self.addresses.items():
            if entry.get("kind") != "telemetry":
                continue
            for field in ("ruling", "registeredAt", "completionMarker", "delivery"):
                self.assertIn(field, entry, f"{name}.{field} is required for telemetry entries")
            self.assertEqual(entry["delivery"], "never", f"{name}: telemetry is never delivered")

    def test_r13_alias_set_is_registered(self):
        missing = sorted(set(R13_ALIASES) - set(self.addresses))
        self.assertEqual(
            missing, [], f"R13 decisions 2+4 alias addresses missing from the registry: {missing}"
        )
        for name in R13_ALIASES:
            entry = self.addresses[name]
            self.assertEqual(entry.get("kind"), "alias")
            self.assertIn("ruling", entry, f"{name}.ruling is required")
            self.assertIn("expansion", entry, f"{name}.expansion status is required")

    def test_decided_aliases_record_their_mechanism(self):
        """ff407906 §5.4: an alias's expansion mechanism must be recorded.

        Two mechanisms exist and they are not interchangeable: `tag-fanout`
        rewrites the tag, `service-route` hands off to another service.
        """
        for name, expected in DECIDED_MECHANISMS.items():
            entry = self.addresses[name]
            self.assertEqual(
                entry.get("mechanism"),
                expected,
                f"{name}: architect ff407906 decided mechanism={expected!r}, "
                f"got {entry.get('mechanism')!r}",
            )

    def test_undecided_aliases_do_not_guess_a_mechanism(self):
        """An address the architect did not rule on must record no mechanism.

        Guessing here is how forum-ingest gets built as a tag copy.
        """
        guessed = {
            name: self.addresses[name].get("mechanism")
            for name in R13_ALIASES
            if name not in DECIDED_MECHANISMS and self.addresses[name].get("mechanism")
        }
        self.assertEqual(
            guessed,
            {},
            f"mechanism is undecided for these; recording one is a guess: {guessed}",
        )

    def test_mechanism_values_are_valid(self):
        for name, entry in self.addresses.items():
            mech = entry.get("mechanism")
            if mech is None:
                continue
            self.assertIn(
                mech, VALID_MECHANISMS, f"{name}.mechanism={mech!r} is not a known mechanism"
            )

    def test_no_registered_non_role_address_is_rejectable(self):
        """Ruling 17 C5 — rejectable IFF in NEITHER registry.

        Everything registered here is in one registry, so nothing here may be
        rejectable. Aliases expand at write and telemetry is archival; rejecting
        either would drop a message the ruling says to keep.
        """
        offenders = {
            name: entry
            for name, entry in self.addresses.items()
            if entry.get("rejectable") is True
        }
        self.assertEqual(
            offenders,
            {},
            "a registered address is not rejectable (C5): aliases/telemetry must never reject",
        )

    def test_rejectability_test_is_well_defined(self):
        """C5 is a test over the UNION of both registries.

        If the registries overlap, "in neither" silently loses a member, so the
        two must be disjoint for the rule to mean what it says.
        """
        roles = set(_load(ROLES)["roles"])
        overlap = sorted(set(self.addresses) & roles)
        self.assertEqual(
            overlap,
            [],
            f"registry overlap makes C5 ill-defined (an address in both): {overlap}",
        )


if __name__ == "__main__":
    unittest.main()
