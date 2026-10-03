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
- the nine broadcast/legacy alias addresses (decisions 2 and 4), expansion
  targets pending the architect (Phase C).

It also prevents category errors: an address cannot be both a role and a
non-role kind, and telemetry is never a delivery target.

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
R13_ALIASES = (
    "all", "all-roles", "assembly", "self", "user",
    "admin", "leader", "designer", "watchdog",
)


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
            self.assertIn("expansion", entry, f"{name}.expansion status is required (Phase C pending architect)")


if __name__ == "__main__":
    unittest.main()
