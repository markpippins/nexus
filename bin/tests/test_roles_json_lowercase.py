#!/usr/bin/env python3
"""Guard: config/roles/roles.json role keys MUST be lowercase (Ruling 8).

Ruling 8 (dda5d9f7, 2026-10-01) orders vocabulary normalization BEFORE
reject-enforcement: roles.json carried a capitalized `DBA` stub and legacy
`Rover` key. The corpus, every durable inbox pointer, and the harness file
for the database-admin role were already lowercase — the config keys were the
last capitalized artifacts. This guard locks the end-state so the
capitalized keys cannot silently return, and pins the alignment between the
registry and post-agent-record.py's writable-role allowlist.

Evidence base: DBA tag-vocabulary audit (record 331ac2f3, 2026-10-02).
Covers Ruling 8 steps 1-2; enabling #693's reject path remains a separate,
later step owned by the write-side PR.

Run: python3 -m pytest bin/tests/test_roles_json_lowercase.py
"""
import importlib.util
import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROLES = os.path.join(REPO, "config", "roles", "roles.json")
POST_AGENT_RECORD = os.path.join(REPO, "bin", "post-agent-record.py")

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SURFACES = ("persona", "procedures", "assemblyAlias", "harnessFile", "nebulaCheck", "governance")


def _load_spec():
    with open(ROLES, encoding="utf-8") as fh:
        return json.load(fh)


class TestRolesJsonLowercase(unittest.TestCase):
    def setUp(self):
        self.spec = _load_spec()
        self.roles = self.spec["roles"]

    def test_every_role_key_is_lowercase_kebab(self):
        bad = [k for k in self.roles if not KEBAB.fullmatch(k)]
        self.assertEqual(
            bad, [], f"non-lowercase-kebab role keys (Ruling 8 end-state violated): {bad}"
        )

    def test_no_case_variant_collisions(self):
        lowered = {}
        for key in self.roles:
            lowered.setdefault(key.lower(), []).append(key)
        collisions = {low: keys for low, keys in lowered.items() if len(keys) > 1}
        self.assertEqual(
            collisions, {}, f"case-variant key collisions (delivery ambiguity): {collisions}"
        )

    def test_dba_block_is_canonical_and_honest(self):
        self.assertIn("dba", self.roles, "lowercase dba key is the canonical DBA registration")
        block = self.roles["dba"]
        self.assertIs(block.get("nebulaCheck"), True)
        self.assertIs(block.get("persona"), True)
        self.assertIs(block.get("harnessFile"), True)
        # Flipped to True by #712 step 2: V203 moved the 17 uppercase
        # role_memory cards onto `dba` (procedures), and V204 case-flipped the
        # assembly alias DBA -> dba (assemblyAlias). Leaving these False would
        # make verify-roles WARN "present but not expected".
        self.assertIs(block.get("procedures"), True)
        self.assertIs(block.get("assemblyAlias"), True)
        self.assertIs(block.get("governance"), False)

    def test_legacy_capitalized_keys_stay_retired(self):
        self.assertNotIn("DBA", self.roles, "capitalized DBA stub was retired (Ruling 8); must not return")
        self.assertNotIn("Rover", self.roles, "legacy Rover was retired (0 corpus records); must not return")

    def test_surface_values_are_boolean(self):
        for name, block in self.roles.items():
            for surface, value in block.items():
                if surface in SURFACES:
                    self.assertIsInstance(
                        value, bool, f"{name}.{surface} must be boolean, got {value!r}"
                    )


class TestWritableRolesAlignment(unittest.TestCase):
    """post-agent-record.py derives its allowlist from nebulaCheck=true keys.

    After normalization the allowlist must contain lowercase `dba` and never
    again contain a capitalized `DBA` entry derived from the registry."""

    def _load_module(self):
        spec = importlib.util.spec_from_file_location("post_agent_record", POST_AGENT_RECORD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_registry_writable_roles_present_in_allowlist(self):
        spec = _load_spec()
        expected = {
            name
            for name, block in spec["roles"].items()
            if isinstance(block, dict)
            and block.get("nebulaCheck", spec["roleDefaults"].get("nebulaCheck", False))
        }
        writable = self._load_module()._writable_roles()
        missing = expected - writable
        self.assertEqual(
            missing, set(), f"registry says writable but allowlist lacks: {sorted(missing)}"
        )

    def test_dba_writable_capitalized_dba_not(self):
        writable = self._load_module()._writable_roles()
        self.assertIn("dba", writable)
        self.assertNotIn("DBA", writable, "capitalized DBA must not be a writable role identity")


if __name__ == "__main__":
    unittest.main()
