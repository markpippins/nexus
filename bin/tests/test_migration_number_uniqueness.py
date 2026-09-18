#!/usr/bin/env python3
"""Migration duplicate-number guard (sql/ V-number uniqueness).

Hermetic: no database, no network. Companion to test_migration_commit_lint
(pitfall #19 family). Pins the invariant learned from the 2026-09-18
collision audit (DBA R1 793679f8):

  Two migrations sharing one V-number breaks apply-order determinism: which
  file "is" V125 depends on directory sort order, and a fresh environment
  (CI bootstrap, new laptop, vanadium restore) may apply them in a different
  order than the machine that evolved the schema — silently diverging.

The live corpus contained SIX historical duplicate numbers (V039, V040,
V045, V046, V051, V125). The 2026-09-18 audit verified by live schema
introspection that BOTH files of every pair were already applied-live
history — immutable, unrenumberable — EXCEPT V039__open_question_entities
(applied-then-retired: its table was deliberately dropped by nebula-srv
migration 042 / approved disposition D4), which was renumbered to V183 in
the same PR as this guard. The FIVE surviving pairs are pinned as a
permanent ALLOWLIST — and any NEW duplicate fails CI:

  - exact pair match against the allowlist  → historical, PASS
  - any other shared number                 → FAIL with the colliding files

Run:
  python3 -m pytest bin/tests/test_migration_number_uniqueness.py -v
"""

import os
import re
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_SELF, "..", ".."))
SQL_DIR = os.path.join(REPO_ROOT, "sql")

_MIG_RE = re.compile(r"^(V\d+)__(.+)\.sql$")

# Historical duplicates verified applied-live on 2026-09-18 (DBA audit,
# record 793679f8 lineage). Both files of each pair are immutable history;
# the one renumberable member (V039__open_question_entities → V183) was
# renumbered in this PR. Format: {number: {filename_stem, ...}}.
HISTORICAL_DUPLICATES = {
    # V039's collision was RESOLVED by this PR (V039__open_question_entities
    # → V183); only pairs that remain duplicated on live history are listed.
    "V040": {"operator_continuity_queue", "update_requirements_view"},
    "V045": {"expand_harvest_candidates_schema", "kernel_trigger_enforcement"},
    "V046": {"open_questions_answer_columns", "wind_event_model"},
    "V051": {"db_audit_fixes", "drop_resolution_column"},
    "V125": {"promotion_gate_pg_registrations",
             "statement_evidence_resolution_proposition_unique_index"},
}


def scan_numbers(sql_dir=SQL_DIR):
    """Return {V-number: [filename, ...]} for all migration files."""
    by_number = {}
    if not os.path.isdir(sql_dir):
        return by_number
    for name in sorted(os.listdir(sql_dir)):
        m = _MIG_RE.match(name)
        if m:
            by_number.setdefault(m.group(1), []).append(name)
    return by_number


def find_new_collisions(sql_dir=SQL_DIR):
    """Return {number: [files]} for duplicates NOT covered by the allowlist.

    An exact pair match with the allowlist is historical (PASS); a shared
    number whose file set differs in any way (new third file, renamed
    member, entirely new collision) fails — the allowlist cannot be grown
    silently by renaming.
    """
    by_number = scan_numbers(sql_dir)
    collisions = {}
    for number, files in by_number.items():
        if len(files) < 2:
            continue
        stems = {re.match(_MIG_RE, f).group(2) for f in files}
        if HISTORICAL_DUPLICATES.get(number) == stems:
            continue
        collisions[number] = files
    return collisions


class TestNumberUniqueness(unittest.TestCase):
    def test_no_new_duplicate_numbers(self):
        """Every duplicate outside the verified historical allowlist fails."""
        collisions = find_new_collisions()
        self.assertEqual(
            collisions,
            {},
            "New migration-number collisions (each breaks apply-order "
            "determinism in fresh environments):\n"
            + "\n".join(f"  {k}: {v}" for k, v in collisions.items()),
        )

    def test_historical_allowlist_matches_reality_exactly(self):
        """The allowlist must neither rot nor over-cover: each entry's file
        set must exist exactly, and every real duplicate must be allowlisted.
        Drift in either direction fails, forcing the audit to be re-run."""
        by_number = scan_numbers()
        real_dups = {
            n: {re.match(_MIG_RE, f).group(2) for f in files}
            for n, files in by_number.items() if len(files) > 1
        }
        self.assertEqual(real_dups, HISTORICAL_DUPLICATES)

    def test_corpus_is_nonempty(self):
        names = [n for n in os.listdir(SQL_DIR) if _MIG_RE.match(n)]
        self.assertGreaterEqual(len(names), 80, "corpus walk must be real")

    def test_renumbered_v183_present(self):
        """The one renumberable file from the audit landed as V183."""
        self.assertTrue(
            any(f.startswith("V183__") for f in os.listdir(SQL_DIR)),
            "V183__open_question_entities renumber missing",
        )
        self.assertFalse(
            any(f.startswith("V039__open_question_entities") for f in os.listdir(SQL_DIR)),
            "old V039__open_question_entities still present",
        )


if __name__ == "__main__":
    unittest.main()
