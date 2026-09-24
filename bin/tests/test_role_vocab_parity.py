#!/usr/bin/env python3
"""Hermetic: born-clean role-vocabulary parity (V190 unit, PR #386).

No database, no network — three in-repo files are compared:

  PIN   the sql/*.sql migration carrying the ROLE-VOCAB PIN marker
        (the marker-designated in-repo authority; currently
        V200__add_supervisor_role.sql — the marker moved there from
        V197 per the widening recipe)
  BOOT  sql/ci-bootstrap/nexus-ci-bootstrap.sql's agent_records_role_check
  SWAP  the pin migration's own swap DDL (pin == swap-target consistency)

Pins the contract from R1 e9711b15 (amended 2026-09-23, V200):

  P1  bootstrap CHECK literals == pin literals  (born-clean: every fresh
      deploy carries the authoritative vocabulary at birth)
  P2  the pin migration's swap-DDL literals == pin literals  (the
      migration cannot claim one vocabulary and install another)
  P3  exactly ONE ROLE-VOCAB PIN marker exists repo-wide (authority is
      unique; a future widening migration must move the marker, not add
      a second one)
  P4  the pin carries the ratified-12 trio (ontologist / lead-engineer /
      sound-technician), engineer-iii, and supervisor — catches an
      accidental pin regression to a stale list
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
SQL_DIR = os.path.join(_REPO_ROOT, "sql")
BOOT_PATH = os.path.join(_REPO_ROOT, "sql", "ci-bootstrap", "nexus-ci-bootstrap.sql")

LITERAL_RE = re.compile(r"'([^']*)'")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _find_pin_file():
    """The sql/*.sql file carrying the ROLE-VOCAB PIN marker (exactly one)."""
    hits = []
    for base, _dirs, files in os.walk(SQL_DIR):
        for name in files:
            if name.endswith(".sql") and "ROLE-VOCAB PIN" in _read(os.path.join(base, name)):
                hits.append(os.path.join(base, name))
    return hits


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
        cls.boot = _read(BOOT_PATH)

        # ── locate the pin file dynamically (the marker MOVES between
        #    widening migrations; exactly-one is asserted in P3) ──
        pin_files = _find_pin_file()
        if len(pin_files) != 1:
            raise AssertionError(
                "ROLE-VOCAB PIN marker must exist in exactly one sql/ file; "
                "found: %r" % pin_files)
        cls.pin_path = pin_files[0]
        cls.v190 = _read(cls.pin_path)  # legacy attr name: the pin file

        # ── locate the pin block: marker → END of the first ARRAY[...] ──
        mk = cls.v190.find("ROLE-VOCAB PIN")
        cls.assertGreater(cls, mk, 0, "ROLE-VOCAB PIN marker missing from the pin file")
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

    # ── P4 ──
    def test_pin_carries_the_g1_trio_and_current_widenings(self):
        pin = set(_literals(self.pin_text))
        for role in ("ontologist", "lead-engineer", "sound-technician"):
            self.assertIn(role, pin,
                          "pin lost a ratified-12 role — G1 regression")
        # V197/V200 widenings (2026-09-23): these roles must be present.
        for role in ("engineer-iii", "supervisor"):
            self.assertIn(role, pin, f"pin lost {role} — role-vocabulary regression")

    # ── P2 detail: the swap DDL must include the newest widening ──
    def test_swap_ddl_matches_pin(self):
        # Two sanctioned swap shapes in the pin migration:
        #   static  — an ADD CONSTRAINT whose ARRAY carries the pin literals
        #   dynamic (V197+) — the ADD CONSTRAINT interpolates a literal_list
        #              BUILT from the pin array (unnest(target_vocab));
        #              parity holds by construction, so assert the chain.
        if "literal_list" in self.swap_text:
            self.assertIn("SELECT string_agg(", self.v190,
                          "dynamic swap must build literal_list from the pin array")
            self.assertIn("unnest(target_vocab)", self.v190,
                          "literal_list must derive from the pin array (target_vocab)")
            self.assertEqual(
                self.v190.count("ADD CONSTRAINT agent_records_role_check"), 2,
                "dynamic swap must rebuild BOTH nebula and scratch constraints")
        else:
            self.assertEqual(
                _vocab(self.swap_text), _vocab(self.pin_text),
                "the pin migration's ADD CONSTRAINT differs from its own pin — "
                "the migration would refuse itself at apply time",
            )
    def test_exactly_one_pin_marker_repo_wide(self):
        self.assertEqual(
            [os.path.relpath(self.pin_path, _REPO_ROOT)],
            ["sql/V200__add_supervisor_role.sql"],
            "ROLE-VOCAB PIN marker home changed — update this assertion when "
            "the marker moves again",
        )

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
