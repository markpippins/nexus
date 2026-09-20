#!/usr/bin/env python3
"""Hermetic: born-clean role-vocabulary parity (V190 unit, PR #386).

No database, no network — three in-repo files are compared:

  PIN   sql/V190__scratch_role_vocabulary_widening.sql, under the
        ROLE-VOCAB PIN marker (the marker-designated in-repo authority)
  BOOT  sql/ci-bootstrap/nexus-ci-bootstrap.sql's agent_records_role_check
  V190  the pin inside V190's own swap DDL (pin == swap-target consistency)

Pins the contract from R1 e9711b15:

  P1  bootstrap CHECK literals == pin literals  (born-clean: every fresh
      deploy carries the authoritative vocabulary at birth)
  P2  V190's swap-DDL literals == pin literals  (the migration cannot
      claim one vocabulary and install another)
  P3  exactly ONE ROLE-VOCAB PIN marker exists repo-wide (authority is
      unique; a future widening migration must move the marker, not add
      a second one)
  P4  the pin carries the ratified-12 trio (ontologist / lead-engineer /
      sound-technician) — the actual G1 defect this unit closes; catches
      an accidental pin regression to the stale list
  P5  the bootstrap CHECK literally names agent_records_role_check on
      nebula.agent_records_history (guards against renames silently
      orphaning this suite)

Runs in wr-conf-042 alongside the throwaway-DB E2E; CI-cost ~zero.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."))
V190_PATH = os.path.join(_REPO_ROOT, "sql", "V190__scratch_role_vocabulary_widening.sql")
BOOT_PATH = os.path.join(_REPO_ROOT, "sql", "ci-bootstrap", "nexus-ci-bootstrap.sql")

LITERAL_RE = re.compile(r"'([^']*)'")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _literals(text):
    """Role literals in order of appearance (includes '')."""
    return LITERAL_RE.findall(text)


def _vocab(text):
    """The role VOCABULARY carried by a region: literal set minus ''.

    '' is structural (the `role = ''::text` escape clause / the pin's
    inclusion of it), not a vocabulary member — array contents legitimately
    differ on it, so parity compares the vocabulary only.
    """
    return sorted(set(_literals(text)) - {""})


class Parity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v190 = _read(V190_PATH)
        cls.boot = _read(BOOT_PATH)

        # ── locate the pin block: marker → END of the first ARRAY[...] ──
        mk = cls.v190.find("ROLE-VOCAB PIN")
        cls.assertGreater(cls, mk, 0, "ROLE-VOCAB PIN marker missing from V190")
        arr = cls.v190.find("ARRAY[", mk)
        cls.assertGreater(cls, arr, 0, "pin ARRAY not found after marker")
        close = cls.v190.find("]", arr)
        cls.pin_text = cls.v190[arr:close]

        # ── locate the swap DDL: the LAST ARRAY[...] (post-marker, in the
        #    ADD CONSTRAINT) and the bootstrap CHECK ──
        swap_arr = cls.v190.rfind("ARRAY[")
        swap_close = cls.v190.find("]", swap_arr)
        cls.swap_text = cls.v190[swap_arr:swap_close]

        boot_m = re.search(
            r"agent_records_role_check CHECK.*?ARRAY\[(.*?)\]\)\)\)",
            cls.boot, re.S)
        cls.assertIsNotNone(cls, boot_m,
                            "agent_records_role_check not found in bootstrap")
        cls.boot_text = boot_m.group(0)

    # ── P1 ──
    def test_bootstrap_matches_pin(self):
        self.assertEqual(
            _vocab(self.boot_text), _vocab(self.pin_text),
            "ci-bootstrap role CHECK drifted from the ROLE-VOCAB PIN — "
            "fresh deploys would be born-regressed",
        )

    # ── P2 ──
    def test_swap_ddl_matches_pin(self):
        self.assertEqual(
            _vocab(self.swap_text), _vocab(self.pin_text),
            "V190's ADD CONSTRAINT differs from its own pin — the migration "
            "would refuse itself at apply time",
        )

    # ── P3 ──
    def test_exactly_one_pin_marker_repo_wide(self):
        hits = []
        for base, _dirs, files in os.walk(os.path.join(_REPO_ROOT, "sql")):
            for name in files:
                if not name.endswith(".sql"):
                    continue
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        if "ROLE-VOCAB PIN" in fh.read():
                            hits.append(os.path.relpath(path, _REPO_ROOT))
                except OSError:
                    continue
        self.assertEqual(
            hits, [os.path.relpath(V190_PATH, _REPO_ROOT)],
            "ROLE-VOCAB PIN marker must exist in exactly one sql/ file",
        )

    # ── P4 ──
    def test_pin_carries_the_g1_trio(self):
        pin = set(_literals(self.pin_text))
        for role in ("ontologist", "lead-engineer", "sound-technician"):
            self.assertIn(role, pin,
                          "pin lost a ratified-12 role — G1 regression")

    # ── P5 ──
    def test_bootstrap_check_is_on_the_canonical_table(self):
        idx = self.boot.find("agent_records_role_check")
        self.assertGreater(idx, 0)
        create_m = None
        for m in re.finditer(r"CREATE TABLE (\S+)", self.boot[:idx]):
            create_m = m  # nearest CREATE TABLE before the constraint
        self.assertIsNotNone(create_m)
        self.assertEqual(
            create_m.group(1), "nebula.agent_records_history",
            "bootstrap CHECK no longer sits on nebula.agent_records_history "
            "— re-anchor the parity suite",
        )


if __name__ == "__main__":
    unittest.main()
