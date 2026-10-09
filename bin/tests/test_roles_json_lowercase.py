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

Ruling 13 (de5d538f) + architect ff407906 Amendments A/B: every entry here
carries an explicit, TYPE-VALIDATED `kind` drawn from
{role, harness-alias}. Two amendments changed this guard:

- Amendment B: the previous assertion was `kind == "role"` on every entry,
  which would have FORCED `big-pickle` — a model name that lives in
  tackle.roles and therefore must keep its key here — to be declared a role.
  The assertion is now membership in a set, never equality with "role".
- Amendment A: the previous surface check validated only the values present in
  each role block. `roleDefaults` was never validated, so a non-boolean could
  be smuggled in there and inherited by every role that omits the field —
  present-but-object defeats a presence check, which is exactly the failure
  mode Amendment A warns about. Surfaces are now validated BOTH raw AND
  effective (merged with roleDefaults).

Non-role addresses (alias/telemetry) belong in config/roles/address-kinds.json
(guarded by test_address_kinds.py), never under `roles` (keys must match
tackle.roles exactly).

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

# Ruling 17 C4: membership, NOT equality with "role". `big-pickle` is a model
# identifier that lives in tackle.roles; asserting kind == "role" everywhere
# would formally assert a model is a role (ff407906 Amendment B).
VALID_ROLE_KINDS = ("role", "harness-alias")

# Ruling 13 decision 4 / ff407906: entries that are harness aliases rather than
# roles. Pinned explicitly so a future editor cannot quietly promote one.
HARNESS_ALIAS_ENTRIES = ("big-pickle",)


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
        self.assertIs(block.get("procedures"), False)
        self.assertIs(block.get("assemblyAlias"), False)
        self.assertIs(block.get("governance"), False)

    def test_legacy_capitalized_keys_stay_retired(self):
        self.assertNotIn("DBA", self.roles, "capitalized DBA stub was retired (Ruling 8); must not return")
        self.assertNotIn("Rover", self.roles, "legacy Rover was retired (0 corpus records); must not return")

    def test_kind_is_type_validated_on_every_entry(self):
        """Ruling 17 C3 — validate TYPE, not presence.

        Consumers default `kind` to 'role' when it is ABSENT. A present but
        non-string `kind` (e.g. an object, which is truthy) bypasses that
        default and passes through invalid — reproducing the very defect the
        field is introduced to prevent. So `kind` must be a string.
        """
        bad = {}
        for name, block in self.roles.items():
            if not isinstance(block, dict):
                bad[name] = f"block is {type(block).__name__}, not an object"
                continue
            if "kind" not in block:
                bad[name] = "MISSING (the absent-kind default must never be relied on)"
            elif not isinstance(block["kind"], str):
                bad[name] = f"kind is {type(block['kind']).__name__} ({block['kind']!r}), not str"
        self.assertEqual(
            bad,
            {},
            f"every roles.json entry must carry a STRING kind (C3 type-validation): {bad}",
        )

    def test_kind_is_a_permitted_role_kind(self):
        """Ruling 17 C4 — membership in a set, never `== "role"`.

        `big-pickle` is opencode/big-pickle, a model identifier that lives in
        tackle.roles (so its key cannot move out of roles.json without breaking
        the registry invariant). An unconditional `kind == "role"` assertion
        would declare that model a role — the exact failure this registry
        exists to prevent (ff407906 Amendment B).
        """
        bad = {
            name: block.get("kind")
            for name, block in self.roles.items()
            if isinstance(block, dict) and block.get("kind") not in VALID_ROLE_KINDS
        }
        self.assertEqual(
            bad,
            {},
            f"roles.json kind must be one of {VALID_ROLE_KINDS} "
            f"(alias/telemetry belong in address-kinds.json): {bad}",
        )

    def test_harness_alias_entries_are_not_declared_roles(self):
        """The named instance of Amendment B, pinned so it cannot regress."""
        for name in HARNESS_ALIAS_ENTRIES:
            self.assertIn(name, self.roles, f"{name} lives in tackle.roles; its key must stay")
            kind = self.roles[name].get("kind")
            self.assertEqual(
                kind,
                "harness-alias",
                f"{name} is a model-name harness alias, not a role (got kind={kind!r})",
            )

    def test_only_role_kind_is_a_delivery_target(self):
        """The obligation `kind` exists to express.

        Only kind=role carries a delivery obligation. A harness-alias must never
        be counted as a deliverable role, or `kind` stops meaning anything.
        """
        wrong = [
            name
            for name, block in self.roles.items()
            if isinstance(block, dict)
            and block.get("kind") == "harness-alias"
            and block.get("nebulaCheck") is True
        ]
        self.assertEqual(
            wrong,
            [],
            f"harness-alias entries must not claim nebulaCheck (delivery obligation): {wrong}",
        )

    def test_surface_values_are_boolean(self):
        """Ruling 17 C3 / ff407906 Amendment A — type, not presence."""
        for name, block in self.roles.items():
            for surface, value in block.items():
                if surface in SURFACES:
                    self.assertIsInstance(
                        value, bool, f"{name}.{surface} must be boolean, got {value!r}"
                    )

    def test_role_defaults_surface_values_are_boolean(self):
        """Amendment A's actual hole: `roleDefaults` was never validated.

        Any role omitting a surface inherits it from roleDefaults. A non-boolean
        planted there is inherited by every such role and passes a guard that
        only inspects explicit values — a presence check cannot catch it.
        """
        defaults = self.spec.get("roleDefaults", {})
        self.assertIsInstance(defaults, dict, "roleDefaults must be an object")
        for surface, value in defaults.items():
            if surface in SURFACES:
                self.assertIsInstance(
                    value,
                    bool,
                    f"roleDefaults.{surface} must be boolean, got {value!r} "
                    f"({type(value).__name__}) — every role omitting this surface inherits it",
                )

    def test_effective_surface_values_are_boolean(self):
        """Raw AND effective validation: merge, then check every surface."""
        defaults = self.spec.get("roleDefaults", {})
        bad = {}
        for name, block in self.roles.items():
            if not isinstance(block, dict):
                continue
            for surface in SURFACES:
                effective = block.get(surface, defaults.get(surface))
                if not isinstance(effective, bool):
                    bad.setdefault(name, {})[surface] = repr(effective)[:60]
        self.assertEqual(
            bad,
            {},
            f"effective surface values must be boolean after merging roleDefaults: {bad}",
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
