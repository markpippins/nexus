"""Ratchet on typescript/nebula-srv/migrations/attestations.json.

The Decision 23 content-binding (ruling ``7f2b377a``) requires every pending
migration file's bytes to be hash-bound to an attested artifact before the
startup runner will apply it. The manifest is the attested-hash source; this
guard keeps it honest four ways, each a way the mechanism could rot:

1. A MISSING entry - a numbered migration with no attested hash - is exactly
   the "unknown" state Decision 23 property 3 blocks on. The gate would
   refuse to boot; this test names the file before CI does.
2. A STALE entry - a hash for a file that no longer exists - is a lie about
   an artifact that is gone (mirror of the bin/tests-ci-manifest.json
   ratchet).
3. A WRONG hash - the file on disk no longer matches the recorded hash -
   means the migration changed after its hash was recorded. That is only
   legitimate inside the PR that changes the migration, and that PR must
   regenerate the manifest entry (Decision 23 property 1: the applied file
   must be the reviewed file).
4. Malformed entries (wrong length, non-hex, uppercase) would either fail
   closed at compare time for no reason or silently defeat exact string
   comparison, so the format is pinned.

Mirrors the conventions of bin/tests/test_bin_tests_manifest.py: unittest,
REPO-relative paths, executed with `python3 -m pytest`. The recomputation is
sha256 over raw file bytes - the same operation the gate's pre-apply check
performs (DBA sketch record ``7c5fe000``, item A/E).
"""
import hashlib
import json
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MIGRATIONS = REPO / "typescript" / "nebula-srv" / "migrations"
MANIFEST = MIGRATIONS / "attestations.json"
FILE_RE = re.compile(r"^\d{3}-.*\.sql$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def numbered_files():
    return sorted(p.name for p in MIGRATIONS.glob("*.sql") if FILE_RE.match(p.name))


class TestMigrationAttestations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.files = numbered_files()

    def test_manifest_exists_nonempty_and_flat(self):
        self.assertTrue(MANIFEST.exists())
        self.assertGreater(len(self.manifest), 0)
        for key, value in self.manifest.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, str)
            self.assertTrue(FILE_RE.match(key), f"non-numbered manifest key: {key}")

    def test_every_numbered_migration_is_covered(self):
        missing = sorted(set(self.files) - set(self.manifest))
        self.assertEqual(
            missing, [], f"numbered migrations with no attested hash: {missing}"
        )

    def test_no_stale_entries(self):
        stale = sorted(set(self.manifest) - set(self.files))
        self.assertEqual(
            stale, [], f"manifest entries for non-existent files: {stale}"
        )

    def test_hashes_are_wellformed(self):
        bad = {
            name: value
            for name, value in self.manifest.items()
            if not HASH_RE.match(value)
        }
        self.assertEqual(bad, {}, f"malformed hashes (want 64 lowercase hex): {bad}")

    def test_hashes_match_file_bytes(self):
        # Missing files are the stale-entry guard's case (test_no_stale_entries);
        # this guard only judges files that exist.
        wrong = [
            name
            for name, value in self.manifest.items()
            if (MIGRATIONS / name).exists()
            and hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest() != value
        ]
        self.assertEqual(
            wrong,
            [],
            f"file bytes no longer match attested hash (regenerate the manifest "
            f"in the same PR that changes the migration): {wrong}",
        )


if __name__ == "__main__":
    unittest.main()
