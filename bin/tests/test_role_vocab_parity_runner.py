#!/usr/bin/env python3
"""Hermetic: runner-path role-vocabulary parity (C3, thread 6bba5dd3 item 3).

No database, no network — pure file analysis. Complements
test_role_vocab_parity.py, which pins the sql/ tier (PIN marker, CI
bootstrap, V197/V200 swap DDL) but has ZERO coverage of the path the
nebula-srv startup runner actually applies:
typescript/nebula-srv/migrations/NNN-*.sql (src/migrate.ts FILE_RE).

That blind spot is the root cause of two live defect classes: role
vocabulary drifted between the runner path and the sql/ authority with
no CI signal (056/058 era), and a runner-path migration's comment
claimed multi-schema application its DDL never performed (054's
"applied to both nebula and scratch" — DDL is nebula-only).

Proposed contract (pre-built per draft 19d9d570; PR gated on the DBA
ruling in consolidated decision request 46fe074f):

  R1  every runner-path role-vocab snapshot is a SUBSET of the
      ROLE-VOCAB PIN vocabulary (the pin stays the single authority;
      P3's exactly-one-marker invariant is inherited, not re-asserted)
  R2  born-clean: the runner path's FINAL role-check (highest NNN)
      carries exactly the PIN vocabulary — same doctrine as P1 for the
      CI bootstrap: a fresh database built by the startup runner is
      born with the authoritative vocabulary, not a stale snapshot
  R3  mirror-claim honesty: a migration whose comments claim multi-
      schema (nebula+scratch) application must carry the matching DDL,
      and scratch DDL must not appear without a matching claim — both
      directions fail (the 054 defect class, mechanically caught)
  R4  runner role-checks may only target the two canonical mirrors
      (nebula.agent_records_history, scratch.agent_records_history) —
      guards against renames silently orphaning this suite (P5 analog)

If the DBA rules the weaker subset-only contract (drop R2), that is a
one-line deletion of test_final_snapshot_matches_pin; R1/R3/R4 stand
independently.

Runs in wr-conf-042 alongside the throwaway-DB E2E; CI-cost ~zero.
"""
import os
import re
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", ".."))
RUNNER_DIR = os.path.join(
    _REPO_ROOT, "typescript", "nebula-srv", "migrations")
SQL_DIR = os.path.join(_REPO_ROOT, "sql")

# The startup runner's own selection rule (src/migrate.ts FILE_RE): only
# NNN-prefixed files are applied; anything else in the directory
# (scd-type4-*.sql etc.) is not runner-applied and out of scope here.
NNN_RE = re.compile(r"^(\d{3})-.*\.sql$")

LITERAL_RE = re.compile(r"'([^']*)'")
ROLE_CHECK_RE = re.compile(
    r"ALTER TABLE (\S+)\s+ADD CONSTRAINT agent_records_role_check"
    r"\s+CHECK\s*\((.*?)\)\s*;",
    re.S,
)
CANONICAL_TABLES = {
    "nebula.agent_records_history",
    "scratch.agent_records_history",
}
# Comment phrasings that claim multi-schema application.
CLAIM_RE = re.compile(
    r"both\s+(?:the\s+)?(?:nebula\s+and\s+scratch|scratch\s+and\s+nebula"
    r"|schemas|mirrors)|nebula\s+and\s+scratch|scratch\s+and\s+nebula",
    re.I,
)
# L3 eligibility (pre-blocking sweep 66c90fae, false positives 041/044):
# a comment line inside a Rollback section documents the REVERSE path, and
# a line referencing another migration is historical narration of THAT
# file's defects — neither may assert this file's forward claim.
ROLLBACK_SECTION_RE = re.compile(r"^\s*--\s*rollback\b", re.I)
OTHER_MIGRATION_RE = re.compile(
    r"migration\s+\d{3}\b|\b[Vv]\d{3}\b|\b0\d{2}\b", re.I)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _find_pin_file():
    """The sql/*.sql file carrying the ROLE-VOCAB PIN marker (exactly one).

    Same authority protocol as test_role_vocab_parity.py: the marker
    MOVES between widening migrations; uniqueness is that suite's P3.
    """
    hits = []
    for base, _dirs, files in os.walk(SQL_DIR):
        for name in files:
            if name.endswith(".sql") and "ROLE-VOCAB PIN" in _read(os.path.join(base, name)):
                hits.append(os.path.join(base, name))
    return hits


def _vocab(text):
    """Role vocabulary: string literals minus the structural ''."""
    return sorted(set(LITERAL_RE.findall(text)) - {""})


def _comments_and_ddl(text):
    """Split a migration into (comments, ddl) at line granularity."""
    comments, ddl = [], []
    for line in text.splitlines():
        (comments if line.strip().startswith("--") else ddl).append(line)
    return "\n".join(comments), "\n".join(ddl)


def _eligible_claim_text(comments):
    """Comment text on which a forward claim MAY be asserted (L3).

    Drops, at line granularity: lines from the first Rollback-section
    marker onward (rollback docs describe the reverse path; house style
    keeps forward-scope claims above them), and lines referencing another
    migration ('migration NNN', 'VNNN', bare '0NNN' — narration of that
    file's defects, the 041 shape). Eligible lines are REJOINED with
    newlines so multi-line claims still match (054's own claim spans two
    comment lines). A parenthetical claim about other files WITHOUT any
    NNN/VNNN token is not mechanically detectable — recorded as an
    inspector caveat in the sweep record, not code.
    """
    eligible, in_rollback = [], False
    for line in comments.splitlines():
        stripped = line.strip()
        if ROLLBACK_SECTION_RE.match(stripped):
            in_rollback = True
        if in_rollback or OTHER_MIGRATION_RE.search(stripped):
            continue
        eligible.append(stripped)
    return "\n".join(eligible)


def _claims_multi_schema(comments):
    """True iff eligible comment text asserts the multi-schema claim."""
    return bool(CLAIM_RE.search(_eligible_claim_text(comments)))


def _role_check_blocks(text):
    """All agent_records_role_check rebuilds: [(table, check_body), ...]."""
    return ROLE_CHECK_RE.findall(text)


def _runner_migration_names(files_dir):
    return sorted(
        name for name in os.listdir(files_dir) if NNN_RE.match(name)
    ) if os.path.isdir(files_dir) else []


def check_runner_files(pin_vocab, files_text):
    """Pure core: validate {filename: text} against the pin vocabulary.

    Returns a list of violation strings (empty == conformant). Unit
    tests exercise this with synthetic fixtures; the integration tests
    below apply it to the real runner directory.
    """
    violations = []
    final_name = None
    final_vocab = None
    for name in sorted(files_text):
        text = files_text[name]
        blocks = _role_check_blocks(text)

        # ── R3: mirror-claim honesty (both directions) ──
        # Claims are evaluated on L3-ELIGIBLE comment text only (see
        # _eligible_claim_text): rollback-section lines and other-migration
        # narration can neither assert nor be required to document scope.
        comments, ddl = _comments_and_ddl(text)
        claims_multi = _claims_multi_schema(comments)
        ddl_scratch = "scratch." in ddl
        if claims_multi and not ddl_scratch:
            violations.append(
                f"{name}: comments claim multi-schema application but the DDL "
                "contains no scratch statements — comment/DDL mismatch "
                "(054 defect class); fix the comment or add the DDL")
        if ddl_scratch and not claims_multi:
            violations.append(
                f"{name}: DDL writes to scratch without a documenting claim "
                "— multi-schema writes must state their scope")

        # ── R4: canonical-table guard ──
        for table, _body in blocks:
            if table not in CANONICAL_TABLES:
                violations.append(
                    f"{name}: agent_records_role_check rebuilt on non-canonical "
                    f"table {table} — expected one of {sorted(CANONICAL_TABLES)}")

        # ── R1 + R2 inputs ──
        if blocks:
            file_vocab = sorted(set().union(
                *(_vocab(body) for _table, body in blocks)))
            out_of_pin = set(file_vocab) - set(pin_vocab)
            if out_of_pin:
                violations.append(
                    f"{name}: runner-path role vocabulary carries tokens not in "
                    f"the ROLE-VOCAB PIN: {sorted(out_of_pin)} — widen the pin "
                    "(move the marker), never the runner path alone")
            final_name, final_vocab = name, file_vocab

    # ── R2: born-clean final vocabulary == pin ──
    if final_vocab is not None and sorted(final_vocab) != sorted(pin_vocab):
        violations.append(
            f"born-clean violation: the runner path's final role-check "
            f"({final_name}) carries {final_vocab}, but the ROLE-VOCAB PIN "
            f"carries {sorted(pin_vocab)} — a fresh database built by the "
            "startup runner would not be born with the authoritative "
            "vocabulary (add the runner-path counterpart of the widening)")
    return violations


class RunnerParity(unittest.TestCase):
    """Integration: the real runner directory against the real pin."""

    @classmethod
    def setUpClass(cls):
        if len(_find_pin_file()) != 1:
            raise AssertionError(
                "ROLE-VOCAB PIN marker must exist in exactly one sql/ file "
                "(authority protocol owned by test_role_vocab_parity P3)")
        pin_text = _read(_find_pin_file()[0])
        mk = pin_text.find("ROLE-VOCAB PIN")
        arr = pin_text.find("ARRAY[", mk)
        close = pin_text.find("]", arr)
        cls.pin_vocab = _vocab(pin_text[arr:close])
        cls.files_text = {
            name: _read(os.path.join(RUNNER_DIR, name))
            for name in _runner_migration_names(RUNNER_DIR)
        }

    def test_runner_path_conforms_to_pin(self):
        violations = check_runner_files(self.pin_vocab, self.files_text)
        self.assertEqual(
            violations, [],
            "runner-path role-vocabulary parity violations:\n  "
            + "\n  ".join(violations))

    def test_runner_path_has_role_check_files(self):
        """Guard against the suite silently passing on an empty scan.

        8 as of 2026-09-27: 047, 049-054, 058 (048 adds a column and 055
        adds a GIN index — both touch the table without rebuilding the
        CHECK). Bump the floor when the next role migration lands.
        """
        with_checks = [
            name for name, text in self.files_text.items()
            if _role_check_blocks(text)
        ]
        self.assertGreaterEqual(
            len(with_checks), 8,
            f"expected the runner path to carry the historical role-check "
            f"migrations (047-058 era); found only {with_checks} — "
            "did the scan break?")

    def test_scan_covers_the_real_directory(self):
        self.assertGreater(
            len(self.files_text), 40,
            "runner migration scan suspiciously small — RUNNER_DIR wrong "
            "or NNN_RE drifted from src/migrate.ts FILE_RE")


class ExtractorUnits(unittest.TestCase):
    """Synthetic fixtures: each rule must fire on its defect and stay
    silent on its conformant shape (negative + positive cases)."""

    PIN = ["analyst", "builder", "engineer", "supervisor"]

    def _migrate(self, roles, table="nebula.agent_records_history",
                 comment=""):
        arr = ", ".join(f"'{r}'" for r in roles)
        return (
            f"-- Migration 999: test fixture\n{comment}\n"
            f"BEGIN;\n\n"
            f"ALTER TABLE {table}\n"
            f"    DROP CONSTRAINT IF EXISTS agent_records_role_check;\n\n"
            f"ALTER TABLE {table}\n"
            f"    ADD CONSTRAINT agent_records_role_check\n"
            f"    CHECK (\n"
            f"        role = ''\n"
            f"        OR role = ANY (ARRAY[{arr}]::text[])\n"
            f"    );\n\n"
            f"COMMIT;\n"
        )

    def test_conformant_file_is_silent(self):
        files = {"058-ok.sql": self._migrate(self.PIN)}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_subset_snapshot_is_silent(self):
        """A historical subset snapshot is fine — as long as a LATER
        final snapshot closes the gap (cumulative-widening shape)."""
        files = {
            "050-old.sql": self._migrate(self.PIN[:3]),
            "051-final.sql": self._migrate(self.PIN),
        }
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_new_token_not_in_pin_fails_r1(self):
        files = {"060-widen.sql": self._migrate(self.PIN + ["rogue"])}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("not in" in x and "rogue" in x for x in v), v)

    def test_stale_final_snapshot_fails_r2(self):
        files = {"058-final.sql": self._migrate(self.PIN[:2])}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("born-clean" in x for x in v), v)

    def test_claim_without_scratch_ddl_fails_r3(self):
        comment = ("-- Idempotent (drop + recreate the CHECK). Applied to "
                   "both nebula and scratch schemas for parity.")
        files = {"054-liar.sql": self._migrate(self.PIN, comment=comment)}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("multi-schema" in x for x in v), v)

    def test_scratch_ddl_without_claim_fails_r3_other_direction(self):
        files = {"059-quiet.sql": self._migrate(
            self.PIN, table="scratch.agent_records_history")}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("without a documenting claim" in x for x in v), v)

    def test_claim_with_matching_ddl_is_silent(self):
        comment = ("-- Idempotent (drop + recreate the CHECK). Applied to "
                   "both nebula and scratch schemas for parity.")
        files = {"059-both.sql": self._migrate(self.PIN, comment=comment)
                 + "\nALTER TABLE scratch.agent_records_history\n"
                   "    DROP CONSTRAINT IF EXISTS agent_records_role_check;\n"}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_noncanonical_table_fails_r4(self):
        files = {"060-move.sql": self._migrate(
            self.PIN, table="other.agent_records_history")}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("non-canonical" in x for x in v), v)

    def test_non_role_files_do_not_gate_r2(self):
        """A directory whose files have no role-checks must not emit a
        born-clean violation against a phantom final snapshot."""
        files = {"001-unrelated.sql": "SELECT 1;\n"}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    # ── L3 eligibility (pre-blocking sweep 66c90fae, 041/044 shapes) ──

    def test_rollback_section_claim_is_ineligible(self):
        """044 shape: claim-worded text inside the Rollback section
        documents the REVERSE path and must not assert this file's
        forward scope (nor be required to)."""
        comment = ("-- Migration 044: add entity_key.\n"
                   "-- Rollback: DROP INDEX + DROP COLUMN + recreate view\n"
                   "-- without entity_key for both schemas.")
        files = {"044-like.sql": self._migrate(self.PIN, comment=comment)}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_other_migration_narration_is_ineligible(self):
        """041 shape: narration about ANOTHER migration's defect must
        not be construed as this file's claim — even when the narration
        line carries claim wording, as long as that wording sits on the
        migration-referencing (dropped) line. Claim wording on an
        otherwise-eligible line still counts (see the 054 anchor test)."""
        comment = ("-- Restore the auto-segment trigger.\n"
                   "-- (DROP TRIGGER loop) and recreated only the bitemporal\n"
                   "-- core set. The INSTEAD OF triggers (migration 003) were\n"
                   "-- NOT recreated for both schemas. Result: ...".replace(
                       "were\n-- NOT recreated for both schemas",
                       "were NOT recreated for both schemas"))
        files = {"041-like.sql": self._migrate(self.PIN, comment=comment)}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_054_style_claim_still_fires_across_lines(self):
        """The anchor: 054's real claim spans two comment lines and sits
        near narration lines that carry other migration numbers. L3
        eligibility must drop the narration WITHOUT breaking the
        multi-line claim match."""
        comment = ("-- Role-surface parity. Same pattern as migrations\n"
                   "-- 049/050/051/052/053.\n"
                   "--\n"
                   "-- Idempotent (drop + recreate the CHECK). Applied to\n"
                   "-- both nebula and scratch schemas for parity.")
        files = {"054-like.sql": self._migrate(self.PIN, comment=comment)}
        v = check_runner_files(self.PIN, files)
        self.assertTrue(any("multi-schema" in x for x in v), v)

    def test_or_replace_recreate_wording_alone_is_silent(self):
        """027 shape (L1 lesson): 'Recreate the view' language without a
        multi-schema claim is outside R3's contract in both directions —
        the sweep's recreate-vs-DROP false-positive class must never be
        introduced here by future claim-wording broadening."""
        comment = "-- Recreate the view to include role (appended at end)."
        files = {"027-like.sql": self._migrate(self.PIN, comment=comment)}
        self.assertEqual(check_runner_files(self.PIN, files), [])

    def test_structural_empty_literal_excluded(self):
        """role = '' is structural; its presence must not affect parity."""
        text = self._migrate(self.PIN).replace("role = ''", "role = ''")
        files = {"058-ok.sql": text}
        self.assertEqual(check_runner_files(self.PIN, files), [])


if __name__ == "__main__":
    unittest.main()
