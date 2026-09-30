"""Guard: nebula.harvests must carry its full trigger set — no less, no more.

Live defect (thread `e0a40d6d`, routed to DBA by Decision 26 `00d339f9`):
nebula.harvests lost ALL of its triggers while its 10 trigger functions
remained — functions present, bindings lost. On the bitemporal-upgraded
schema (nebula.harvests is a VIEW), that makes every harvest write fail with
42809 "cannot insert into view". Migration 070 restores the view wiring and
records what it did in nebula.trigger_restore_log.

This guard is the post-repair ratchet. It asserts, per relkind:

- relkind 'v' (bitemporal view): EXACTLY the three INSTEAD OF triggers
  trg_harvests_{insert,update,delete} -> nebula.harvests_*_trigger().
  Missing OR extra user triggers on the view are both failures — extras are
  drift of exactly the kind that went unnoticed here.
- relkind 'r' (fresh numbered-chain TABLE): the 023-lineage triggers
  trg_harvests_insert / trg_harvests_update must be present.

Infrastructure guard: needs a reachable PostgreSQL with the nebula schema.
Runs against standard libpq env vars (PGHOST/PGPORT/PGUSER/PGDATABASE/
PGPASSWORD); on connection failure it raises a loud infrastructure error
rather than passing — a guard that silently passes with no database is the
"healthy report on a broken thing" shape this repo keeps killing.

Mirrors bin/tests conventions: unittest, REPO-relative, executed with
`python3 -m pytest`.
"""
import pathlib
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

VIEW_TRIGGERS = {
    "trg_harvests_insert": "nebula.harvests_insert_trigger",
    "trg_harvests_update": "nebula.harvests_update_trigger",
    "trg_harvests_delete": "nebula.harvests_delete_trigger",
}
TABLE_TRIGGERS = {"trg_harvests_insert", "trg_harvests_update"}


def psql(sql: str) -> str:
    """One psql -At call; raises a loud infrastructure error when PG is down."""
    r = subprocess.run(
        ["psql", "-X", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            "infrastructure: psql failed (is PostgreSQL reachable and are "
            f"PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD set?): {r.stderr.strip()}"
        )
    return r.stdout.strip()


class TestHarvestsTriggersPresent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.relkind = psql(
            "SELECT relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='nebula' AND c.relname='harvests'"
        )
        if cls.relkind not in ("v", "r"):
            raise RuntimeError(
                f"infrastructure: nebula.harvests relkind is {cls.relkind!r} "
                "(empty = object missing) — guard cannot judge this surface"
            )
        rows = psql(
            "SELECT t.tgname, p.proname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_proc p ON p.oid=t.tgfoid "
            "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
            "WHERE n.nspname='nebula' AND c.relname='harvests' AND NOT t.tgisinternal"
        ).splitlines()
        cls.triggers = {}
        for line in filter(None, rows):
            name, func = line.split("|", 1)
            cls.triggers[name] = f"nebula.{func}"

    def test_relkind_branch_triggers_are_complete(self):
        expected = VIEW_TRIGGERS if self.relkind == "v" else {t: None for t in TABLE_TRIGGERS}
        missing = sorted(set(expected) - set(self.triggers))
        self.assertEqual(
            missing, [],
            f"nebula.harvests ({'view' if self.relkind == 'v' else 'table'}) is missing "
            f"triggers: {missing} — harvest writes are broken; apply 070"
        )

    def test_no_extra_user_triggers(self):
        allowed = set(VIEW_TRIGGERS if self.relkind == "v" else TABLE_TRIGGERS)
        extra = sorted(set(self.triggers) - allowed)
        self.assertEqual(
            extra, [],
            f"unexpected user triggers on nebula.harvests: {extra} — drift; "
            f"reconcile with the migration chain before trusting this surface"
        )

    def test_view_trigger_functions_bind_correctly(self):
        if self.relkind != "v":
            self.skipTest("view-branch only")
        wrong = {n: f for n, f in self.triggers.items()
                 if n in VIEW_TRIGGERS and VIEW_TRIGGERS[n] != f}
        self.assertEqual(
            wrong, {},
            f"trigger-to-function bindings diverge from the bitemporal spec: {wrong}"
        )

    def test_restore_log_matches_reality(self):
        row = psql(
            "SELECT coalesce(relkind::text, '') FROM nebula.trigger_restore_log "
            "WHERE version=70"
        ) if psql("SELECT to_regclass('nebula.trigger_restore_log') IS NOT NULL") == "t" else ""
        if not row:
            self.skipTest("nebula.trigger_restore_log absent (070 not applied here)")
        self.assertEqual(
            row, self.relkind,
            "nebula.trigger_restore_log row 70 disagrees with the actual relkind "
            "of nebula.harvests — re-run 070 bookkeeping"
        )


if __name__ == "__main__":
    unittest.main()
