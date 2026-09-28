#!/usr/bin/env python3
"""Coverage guard for the bin/tests CI gate (finding ff3264f0).

The gate itself is easy; the failure mode that made ff3264f0 necessary is
*silent exclusion*. A new guard lands in bin/tests/, nobody adds it to any
workflow, it never runs, and it rots — which is exactly what happened to
test_migration_number_uniqueness.py (red for months against duplicate sql/
version prefixes, invisible) and to test_r9_replication_verify.py (a
time-bomb fixture that went red on 2026-09-21 and stayed red).

So the manifest is not allowed to be incomplete. Every bin/tests/test_*.py
must appear in exactly one tier of bin/tests/CI_MANIFEST.json. Adding a test
without classifying it fails THIS test, in CI, at PR time.

The other direction is guarded too: a manifest entry naming a file that does
not exist means the manifest has drifted from reality and must be corrected,
not worked around.
"""

from __future__ import annotations

import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
TESTS_DIR = REPO / "bin" / "tests"
MANIFEST = TESTS_DIR / "CI_MANIFEST.json"

TIERS = ("gated", "service_dependent", "dsn_gated", "known_red")
# Tiers that are allowed to hold a bare filename rather than {file, reason}.
BARE_TIERS = ("gated",)


def _load() -> dict:
    return json.loads(MANIFEST.read_text())


def _on_disk() -> set[str]:
    return {p.name for p in TESTS_DIR.glob("test_*.py")}


def _entries(manifest: dict, tier: str) -> list:
    return manifest.get(tier, [])


def _names(manifest: dict, tier: str) -> list[str]:
    raw = _entries(manifest, tier)
    if tier in BARE_TIERS:
        return list(raw)
    return [e["file"] if isinstance(e, dict) else e for e in raw]


def _reasons(manifest: dict, tier: str) -> list[str]:
    return [
        (e.get("reason", "") if isinstance(e, dict) else "")
        for e in _entries(manifest, tier)
    ]


class Coverage(unittest.TestCase):
    """The manifest must account for every file, exactly once."""

    def setUp(self):
        self.manifest = _load()
        self.disk = _on_disk()

    def test_manifest_exists_and_parses(self):
        self.assertTrue(MANIFEST.is_file(), f"missing manifest: {MANIFEST}")

    def test_every_test_file_is_classified(self):
        classified = set()
        for tier in TIERS:
            classified |= set(_names(self.manifest, tier))
        unclassified = sorted(self.disk - classified)
        self.assertEqual(
            unclassified, [],
            "bin/tests/ files missing from CI_MANIFEST.json — CI will not run "
            "them and they will rot exactly as ff3264f0 describes. Classify "
            "each one (measure it: run it with no services, then with "
            "services) and add it to a tier.")

    def test_manifest_names_only_real_files(self):
        classified = set()
        for tier in TIERS:
            classified |= set(_names(self.manifest, tier))
        ghost = sorted(classified - self.disk)
        self.assertEqual(
            ghost, [],
            "CI_MANIFEST.json names files that do not exist in bin/tests/. "
            "The manifest has drifted from the tree; fix the manifest.")

    def test_no_file_in_two_tiers(self):
        seen: dict[str, list[str]] = {}
        for tier in TIERS:
            for name in _names(self.manifest, tier):
                seen.setdefault(name, []).append(tier)
        dupes = {n: t for n, t in seen.items() if len(t) > 1}
        self.assertEqual(
            dupes, {},
            "files listed in more than one tier — a file that is both gated "
            "and excluded is a contradiction, not a policy.")

    def test_no_duplicate_within_a_tier(self):
        for tier in TIERS:
            names = _names(self.manifest, tier)
            dupes = sorted({n for n in names if names.count(n) > 1})
            self.assertEqual(dupes, [], f"duplicate entries in tier '{tier}'")


class TierHygiene(unittest.TestCase):
    """Exclusions must carry a reason; the gate must be non-empty."""

    def setUp(self):
        self.manifest = _load()

    def test_gated_tier_is_not_empty(self):
        self.assertTrue(
            _names(self.manifest, "gated"),
            "the gated tier is empty — the gate would pass vacuously, which "
            "is the same as no gate at all.")

    def test_every_exclusion_states_a_reason(self):
        for tier in ("service_dependent", "dsn_gated", "known_red"):
            for name, reason in zip(_names(self.manifest, tier),
                                    _reasons(self.manifest, tier)):
                self.assertTrue(
                    reason.strip(),
                    f"{name} is excluded from CI in tier '{tier}' with no "
                    f"stated reason. An unexplained exclusion is a silent "
                    f"exclusion.")

    def test_reasons_are_substantive(self):
        # A one-word reason is not a reason. Require enough text to name
        # what the test needs or what is wrong with it.
        for tier in ("service_dependent", "dsn_gated", "known_red"):
            for name, reason in zip(_names(self.manifest, tier),
                                    _reasons(self.manifest, tier)):
                self.assertGreaterEqual(
                    len(" ".join(reason.split())), 40,
                    f"{name} ({tier}) has a too-thin reason: {reason!r}")

    def test_gated_tier_is_sorted_and_unique(self):
        names = _names(self.manifest, "gated")
        self.assertEqual(names, sorted(names),
                         "gated tier should be sorted so diffs stay readable")

    def test_known_red_entries_are_actually_red(self):
        """A 'known red' that has quietly gone green is stale policy.

        This is the mirror of the time-bomb failure: a test parked in
        known_red stops being a to-do item the moment it starts passing, and
        nothing would notice. We cannot run pytest from here cheaply, so we
        only assert the tier is small and explicitly accounted for — the
        actual green/red signal comes from the gate run itself.
        """
        names = _names(self.manifest, "known_red")
        self.assertLessEqual(
            len(names), 5,
            "known_red is growing. Every entry is a defect someone is not "
            "looking at; if the number is climbing, the gate is being used "
            "as a place to hide failures rather than surface them.")


class KnownRedContent(unittest.TestCase):
    def test_known_red_reasons_name_the_defect(self):
        manifest = _load()
        for entry in _entries(manifest, "known_red"):
            reason = " ".join(entry.get("reason", "").split())
            self.assertIn(
                "RED", reason,
                f"{entry['file']} is in known_red; its reason should say so "
                "explicitly so nobody reads the tier as 'these are fine'.")
            self.assertRegex(
                reason, r"V?\d{2,}",
                f"{entry['file']} is in known_red; its reason should cite the "
                "concrete version/file/identifier involved so the finding "
                "stays actionable.")


if __name__ == "__main__":
    unittest.main()
