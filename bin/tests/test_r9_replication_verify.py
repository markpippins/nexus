#!/usr/bin/env python3
"""Hermetic tests for bin/r9-replication-verify.py (R9 checklist battery).

No ssh, no systemd, no journal, no filesystem: every process surface is
injected via Runners. Pins the classifications from the filed checklist
(record 550df8af):

  - V5 RPO bands (30h ok / 54h warn / beyond fail) + applied-after gate
  - honest SKIP for the vanadium-ci drive-absent guard (SKIP is a good state)
  - UNREACHABLE vs FAIL distinction (travel state is not a defect)
  - escape-hatch gap arithmetic (today excluded, back-window only)
  - journal classification: FAIL body line overrides Finished (exit-0-with-
    defect guard), and --skip-verify-last yields SKIP not PASS
  - exit contract: FAIL -> 1; SKIP/UNREACHABLE-only -> 0
"""

import contextlib
import datetime as dt
import importlib.util
import io
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "r9-replication-verify.py")

_spec = importlib.util.spec_from_file_location("r9rv", _TOOL)
r9rv = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = r9rv  # dataclass processing reads sys.modules
_spec.loader.exec_module(r9rv)

NOW = dt.datetime(2026, 9, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
TODAY = NOW.date()

TIMER_LINE = ("Sat 2026-09-20 13:05:00 UTC 1h left "
              "Sat 2026-09-20 03:22:14 UTC 5h ago "
              "backup-pg-to-vanadium.timer sync-pg-to-vanadium.timer")
JOURNAL_OK = ("Starting backup-pg-to-vanadium.service...\n"
              "Finished backup-pg-to-vanadium.service.")
MANIFESTS = "manifest__20260920_033503.txt\nmanifest__20260919_033501.txt"
ESCAPE_DUMPS = "\n".join(
    "nexus__2026%02d%02d_033503.dump" % (9, 20 - i) for i in range(1, 8)
)


def fake_runner(ssh=None, systemctl=None, journalctl=None, local=None):
    """A fully-green default fleet; tests override only what they break."""
    r = r9rv.Runners()
    r.ssh = ssh or (
        lambda a: (0, MANIFESTS if "ls" in a
                   else "/dev/sdb1 1T 800G 200G 80% /mnt/backups", "")
    )
    r.systemctl = systemctl or (
        lambda a: (0, "enabled", "") if "is-enabled" in a else (0, TIMER_LINE, "")
    )
    r.journalctl = journalctl or (lambda a: (0, JOURNAL_OK, ""))
    r.local = local or (
        lambda a: (0, ESCAPE_DUMPS + "\n", "") if a[0] == "ls" else (0, "verify ok", "")
    )
    return r


def run(r, **kw):
    return r9rv.run_battery(r, now=NOW, **kw)


def get(rep, cid):
    return next(c for c in rep.checks if c.cid == cid)


class Helpers(unittest.TestCase):
    def test_timer_line(self):
        line = ("Sat 2026-09-20 13:05:00 UTC  1h 2min left Sat 2026-09-20 "
                "03:22:14 UTC 5h ago backup-pg-to-vanadium.timer")
        self.assertEqual(r9rv.parse_timer_line(line), "2026-09-20 03:22:14")

    def test_manifest_ts(self):
        self.assertEqual(r9rv.parse_manifest_ts("manifest__20260920_033503.txt"),
                         dt.datetime(2026, 9, 20, 3, 35, 3,
                                     tzinfo=dt.timezone.utc))

    def test_rpo_bands(self):
        self.assertEqual(r9rv.rpo_classify(30.0), "ok")
        self.assertEqual(r9rv.rpo_classify(30.1), "warn")
        self.assertEqual(r9rv.rpo_classify(54.0), "warn")
        self.assertEqual(r9rv.rpo_classify(54.1), "fail")

    def test_disk_bands(self):
        self.assertEqual(r9rv.disk_classify(15.0), "ok")
        self.assertEqual(r9rv.disk_classify(14.9), "warn")
        self.assertEqual(r9rv.disk_classify(5.0), "warn")
        self.assertEqual(r9rv.disk_classify(4.9), "fail")

    def test_df_free(self):
        self.assertEqual(r9rv.parse_df_free_pct(
            "/dev/sdb1 235G 146G 80G 65% /mnt/backups"), 35.0)
        self.assertIsNone(r9rv.parse_df_free_pct("garbage"))

    def test_journal_truncated_window_still_classifies(self):
        """A window that misses 'Starting' but carries 'Finished' is ok."""
        status, ev = r9rv.journal_last_run([
            "psql output line",
            "Finished sync-pg-to-vanadium.service.",
        ])
        self.assertEqual(status, "ok")
        self.assertIn("Finished", ev)

    def test_journal_fail_overrides_finished(self):
        status, _ = r9rv.journal_last_run([
            "Starting backup-pg-to-vanadium.service...",
            "FAIL: checksum mismatch for dumpset 20260920_033503",
            "Finished backup-pg-to-vanadium.service.",
        ])
        self.assertEqual(status, "fail")

    def test_journal_ok(self):
        status, ev = r9rv.journal_last_run([
            "Starting backup-pg-to-vanadium.service...",
            "Finished backup-pg-to-vanadium.service.",
        ])
        self.assertEqual(status, "ok")
        self.assertIn("Finished", ev)

    def test_journal_absent(self):
        self.assertEqual(r9rv.journal_last_run(["noise only"])[0], "absent")

    def test_vdci_drive_absent_is_skip(self):
        status, ev = r9rv.classify_vdci([
            "Starting backup-vanadium-ci.service...",
            "SKIP: spool drive absent (/media/pgpass missing)",
            "Finished backup-vanadium-ci.service.",
        ])
        self.assertEqual(status, "skip")
        self.assertIn("guard honest", ev)

    def test_escape_dates_and_gaps(self):
        names = ["nexus__20260919_033503.dump", "nexus__20260918_033501.dump",
                 "not-a-dump.txt"]
        self.assertEqual(r9rv.escape_hatch_dates(names),
                         ["2026-09-18", "2026-09-19"])
        gaps = r9rv.escape_hatch_gaps(names, 4, dt.date(2026, 9, 20))
        self.assertEqual(gaps, ["2026-09-16", "2026-09-17"])  # today excluded

    def test_escape_since_excludes_pre_install_nights(self):
        names = ["nexus__20260919_033503.dump"]
        gaps = r9rv.escape_hatch_gaps(names, 7, dt.date(2026, 9, 20),
                                      dt.date(2026, 9, 16))
        self.assertEqual(gaps, ["2026-09-16", "2026-09-17", "2026-09-18"])


class Battery(unittest.TestCase):
    def test_all_green(self):
        ls = "\n".join(("manifest__20260920_033503.txt",
                        "manifest__20260919_033501.txt"))
        r = fake_runner(ssh=lambda a: (0, ls if "ls" in a
                                       else "/dev/sdb1 1T 800G 200G 80% /x", ""))
        rep = run(r)
        self.assertFalse(rep.has_fail)
        self.assertEqual(rep.verdict(), "green")
        self.assertEqual(get(rep, "V1").status, "PASS")
        self.assertEqual(get(rep, "V5").status, "PASS")
        self.assertEqual(get(rep, "V6").status, "PASS")   # 20% free
        self.assertEqual(get(rep, "V9").status, "PASS")   # tonight missing only

    def test_unreachable_is_not_fail(self):
        r = fake_runner(ssh=lambda a: (255, "", "ssh: Could not resolve"))
        rep = run(r)
        self.assertFalse(rep.has_fail)
        self.assertEqual(get(rep, "V1").status, "UNREACHABLE")
        self.assertEqual(get(rep, "V5").status, "UNREACHABLE")
        self.assertEqual(rep.verdict(), "green-with-honest-skips")

    def test_stale_remote_manifest_fails_rpo(self):
        ls = "manifest__20260918_033501.txt"  # ~57h old
        r = fake_runner(ssh=lambda a: (0, ls if "ls" in a
                                       else "/dev/sdb1 1T 10G 80% /x", ""))
        rep = run(r)
        self.assertTrue(rep.has_fail)
        self.assertEqual(get(rep, "V5").status, "FAIL")
        self.assertIn("newest manifest", get(rep, "V5").evidence)

    def test_escape_gaps_respect_since(self):
        r = fake_runner(local=lambda a: (0, "nexus__20260914_033503.dump\n", "")
                        if a[0] == "ls" else (0, "ok", ""))
        rep = run(r, escape_since=dt.date(2026, 9, 16))
        self.assertEqual(get(rep, "V9").status, "FAIL")
        self.assertIn("missing nights", get(rep, "V9").evidence)
        self.assertNotIn("2026-09-14", get(rep, "V9").evidence)
        self.assertNotIn("2026-09-15", get(rep, "V9").evidence)

    def test_applied_after_gate(self):
        ls = "manifest__20260920_033503.txt"  # fresh, but pre-migration
        r = fake_runner(ssh=lambda a: (0, ls if "ls" in a
                                       else "/dev/x 1T 10G 80% /x", ""))
        after = dt.datetime(2026, 9, 20, 5, 0, 0, tzinfo=dt.timezone.utc)
        rep = run(r, applied_after=after)
        self.assertEqual(get(rep, "V5").status, "FAIL")
        self.assertIn("PREDATES", get(rep, "V5").evidence)

    def test_v4_sources_backup_env(self):
        seen = {}
        r = fake_runner(local=lambda a: (
            seen.update(cmd=a) if any("--verify-last" in x for x in a)
            else None) or ((0, ESCAPE_DUMPS + "\n", "") if a[0] == "ls"
                           else (0, "verify ok", "")))
        rep = run(r)
        self.assertEqual(get(rep, "V4").status, "PASS")
        self.assertIn("pg-backup.env", seen["cmd"][2])
        self.assertIn("--verify-last", seen["cmd"][2])

    def test_skip_verify_last_is_skip(self):
        r = fake_runner()
        rep = run(r, do_verify=False)
        self.assertEqual(get(rep, "V4").status, "SKIP")
        self.assertIn("--skip-verify-last", get(rep, "V4").evidence)

    def test_vdci_drive_absent_skip_does_not_fail(self):
        r = fake_runner(
            journalctl=lambda a: (
                (0, ["Starting backup-vanadium-ci.service...",
                     "SKIP: spool drive absent (/media/pgpass missing)",
                     "Finished backup-vanadium-ci.service."], "")
                if any("backup-vanadium-ci" in x for x in a) else
                (0, ["Starting backup-pg-to-vanadium.service...",
                     "Finished backup-pg-to-vanadium.service."], "")
            ),
        )
        rep = run(r)
        self.assertEqual(get(rep, "V8").status, "SKIP")
        self.assertFalse(rep.has_fail)

    def test_disabled_timer_fails(self):
        r = fake_runner(
            systemctl=lambda a: (1, "disabled", "") if "is-enabled" in a
            else (0, "", ""),
            local=lambda a: (0, "", "") if a[0] == "ls" else (0, "ok", ""),
        )
        rep = run(r)
        self.assertEqual(get(rep, "V2").status, "FAIL")
        self.assertTrue(rep.has_fail)

    def test_missing_escape_night_fails(self):
        r = fake_runner(
            local=lambda a: (0, "nexus__20260914_033503.dump\n", "")
            if a[0] == "ls" else (0, "ok", ""),
        )
        rep = run(r, escape_since=None)
        self.assertEqual(get(rep, "V9").status, "FAIL")
        self.assertIn("missing nights", get(rep, "V9").evidence)


class Main(unittest.TestCase):
    def _main(self, argv, runner):
        orig = r9rv.Runners.real
        r9rv.Runners.real = classmethod(lambda cls: runner)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                return r9rv.main(argv)
        finally:
            r9rv.Runners.real = orig

    def test_exit0_on_skip_only(self):
        r = fake_runner(ssh=lambda a: (255, "", "unreachable"))
        self.assertEqual(self._main(["--json"], r), 0)

    def test_exit1_on_fail(self):
        r = fake_runner(
            systemctl=lambda a: (1, "disabled", "") if "is-enabled" in a
            else (0, "", ""),
        )
        self.assertEqual(self._main(["--json"], r), 1)


if __name__ == "__main__":
    unittest.main()
