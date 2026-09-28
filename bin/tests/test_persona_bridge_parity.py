#!/usr/bin/env python3
"""Guard: every persona an agent .md requests must actually resolve.

Background
----------
The opencode agent files under `config/harnesses/opencode/agents/*.md` load
their persona at turn start through the tackle persona bridge:

    curl -s "http://localhost:3400/prompts/get?name=<role>/<slug>"

`tackle-prompt-bridge` (:3400) serves from Redis, which
`tackle-prompt-sync-srv` (:3501) fills from `tackle.prompts`. A request for a
row that was never seeded fails like this:

    HTTP 404  Prompt "engineer-iii/opencode-persona" not cached.

That is what Engineer III hit on 2026-09-27. The agent file and the persona
row are two independent artefacts with nothing checking that they agree, so
the failure only surfaces at agent launch.

What this pins
--------------
  P1  Every `prompts/get?name=<role>/<slug>` referenced by an agent .md has
      a matching `tackle.prompts` row.  (requires a database)

  P2  The role an agent file declares (`assumes_role:`) matches the role it
      asks the bridge for, case-insensitively. Catches an agent file wired
      to a persona belonging to a differently-named role.  (hermetic)

  P3  Case-exact agreement, with a short, explicit exception list. This is
      the check that is currently RED for DBA and is pinned as known drift
      rather than left to rot: `dba.md` declares `assumes_role: dba` but
      requests `DBA/database-admin`, because the role row and the persona
      were both created uppercase. Tracked as the D1 role-name
      canonicalisation item. When DBA is canonicalised, delete the entry
      from KNOWN_CASE_DRIFT — the test then holds the line.

Scoping note — why "referenced by an agent .md" and not "every role"
------------------------------------------------------------------------
These are deliberately different questions, and conflating them is a
mistake this file exists to prevent:

  * "Role has a persona row" is NOT "the persona bridge works for it." As of
    2026-09-27 nine `opencode-persona` rows exist that no agent file
    requests (auditor, design-synthesist, epistemologist, layout-mechanic,
    leased-builder, ontologist, supervisor, sysadmin, tester).
  * Roles with no agent file (Rover, test, sound-technician) and no persona
    are not broken bridges — nothing requests one. Whether they *should*
    have a persona is a design decision, not a defect.

So P1 keys off the request, which is what actually has to resolve at launch.
P1 needs a database and skips cleanly when none is configured; P2/P3 are
hermetic.
"""
from __future__ import annotations

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AGENT_DIR = os.path.join(REPO_ROOT, "config", "harnesses", "opencode", "agents")

# Both the GET form and the documented POST form in the agent files.
REF_RE = re.compile(r"prompts/get\?name=([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)")
ASSUMES_RE = re.compile(r"^assumes_role:\s*([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)

# (filename, declared role, requested role) triples known to differ in case.
# See P3 in the module docstring. Keep this list short and justified: every
# entry is drift someone has decided not to fix yet, not a tolerance.
KNOWN_CASE_DRIFT = {
    ("dba.md", "dba", "DBA"),
}


def agent_files() -> list[str]:
    if not os.path.isdir(AGENT_DIR):
        return []
    return sorted(f for f in os.listdir(AGENT_DIR) if f.endswith(".md"))


def read(fn: str) -> str:
    with open(os.path.join(AGENT_DIR, fn), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def requests_for(fn: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2)) for m in REF_RE.finditer(read(fn))}


def declared_role(fn: str) -> str | None:
    m = ASSUMES_RE.search(read(fn))
    return m.group(1) if m else None


def dsn():
    return os.environ.get("CONDUIT_PG_DSN") or os.environ.get("NEXUS_PG_DSN")


class PersonaRefParity(unittest.TestCase):
    def test_agent_dir_is_populated(self):
        """Guard the guard: if AGENT_DIR moves, every check below passes
        vacuously."""
        self.assertTrue(
            agent_files(), f"no agent .md files under {AGENT_DIR} — path is wrong?"
        )

    def test_requested_roles_match_declared_role(self) -> None:
        """P2 — hermetic. The declared role must be one it actually requests.

        Not "the only role it requests": an agent file may legitimately load
        more than one persona. `architect.md` requests both
        `architect/opencode-persona` and `big-pickle/opencode-persona`,
        which is correct, so equality would flag it wrongly.
        """
        bad = []
        for fn in agent_files():
            declared = declared_role(fn)
            if declared is None:
                continue  # not every agent file declares assumes_role
            roles = {r for r, _ in requests_for(fn)}
            if roles and not any(r.lower() == declared.lower() for r in roles):
                bad.append(
                    f"{fn}: assumes_role={declared} but requests {sorted(roles)}"
                )
        self.assertEqual(bad, [], "\n".join(bad))

    def test_role_case_drift_is_only_what_we_know_about(self) -> None:
        """P3 — hermetic. Case-exact agreement, minus pinned known drift.

        Only the declared role's OWN reference is compared. Extra personas a
        file loads for other roles (architect -> big-pickle) are not drift.
        """
        drift = set()
        for fn in agent_files():
            declared = declared_role(fn)
            if declared is None:
                continue
            for role, _slug in requests_for(fn):
                if role.lower() == declared.lower() and role != declared:
                    drift.add((fn, declared, role))

        unexpected = drift - KNOWN_CASE_DRIFT
        self.assertEqual(
            unexpected,
            set(),
            "new case drift not on KNOWN_CASE_DRIFT — either fix the names "
            "or add a justified entry:\n"
            + "\n".join(f"  {a}: {b} vs {c}" for a, b, c in sorted(unexpected)),
        )
        # Keep the exception list honest: a stale entry means the drift was
        # fixed but the pin was not removed, which hides future regressions.
        stale = KNOWN_CASE_DRIFT - drift
        self.assertEqual(
            stale,
            set(),
            "stale KNOWN_CASE_DRIFT entries — this drift no longer exists, "
            "remove it:\n" + "\n".join(f"  {a}: {b} vs {c}" for a, b, c in sorted(stale)),
        )

    def test_every_requested_persona_exists_in_db(self) -> None:
        """P1 — needs a database; skips cleanly without one."""
        connection = dsn()
        if not connection:
            self.skipTest("no CONDUIT_PG_DSN / NEXUS_PG_DSN set")

        try:
            import psycopg2
        except ImportError:  # pragma: no cover
            self.skipTest("psycopg2 not installed")

        wanted = set()
        for fn in agent_files():
            wanted |= requests_for(fn)
        self.assertTrue(wanted, "no persona requests parsed — regex may be stale")

        conn = psycopg2.connect(connection)
        try:
            cur = conn.cursor()
            cur.execute("SELECT role, slug FROM tackle.prompts")
            have = {(r, s) for r, s in cur.fetchall()}
            cur.close()
        finally:
            conn.close()

        missing = sorted(wanted - have)
        self.assertEqual(
            missing,
            [],
            "persona requested by an agent .md but absent from tackle.prompts — "
            "the bridge will 404 at agent launch:\n"
            + "\n".join(f"  {r}/{s}" for r, s in missing),
        )


if __name__ == "__main__":
    unittest.main()
