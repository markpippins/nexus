#!/usr/bin/env python3
"""Hermetic tests for bin/verify-same-lan-checklist.py (§6 checklist).

No sockets, no systemd, no journal, no real logs: every surface is injected
through Runners, and LOG_DIR is pointed at a temp dir. Pins the classifications
the summation's §6 relies on:

  - PASS for a healthy timer + journal + fresh log
  - FAIL for a disabled timer, a stale trigger, a failed run, a log-recorded
    error, or a log-recorded down target while vanadium is reachable
  - UNREACHABLE (not FAIL) when the same defects coincide with an absent
    vanadium — an off-network run is never a host-down claim
  - SKIP for the vanadium-ci drive-absent guard (Finished over a deliberate
    skip is the guard working), and FAIL if the target is down anyway while
    vanadium IS reachable
  - fleet reachability PASS/UNREACHABLE
  - twin band: 0 bound is the expected PASS; listeners are reported by name
  - JVM lanes: thallium closed = FAIL, helium closed = PASS (retired),
    helium open = WARN
  - exit contract: FAIL -> 1; SKIP/UNREACHABLE-only -> 0
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "verify-same-lan-checklist.py")

_spec = importlib.util.spec_from_file_location("vscl", _TOOL)
vscl = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vscl  # dataclass processing reads sys.modules
_spec.loader.exec_module(vscl)

NOW = datetime(2026, 11, 10, 12, 0, 0)
FRESH = "2026-11-10 11:50:00"   # 10 min ago
STALE = "2026-11-10 08:00:00"   # 4 h ago: stale for the 10-min sync tier
JOURNAL_OK = "Starting x.service...\nFinished x.service."
JOURNAL_FAIL = ("Starting x.service...\n"
                "sync-pg-to-vanadium.service: Failed with result 'exit-code'.\n"
                "Failed to start x.service.")


def fresh_log(tmp: Path, name: str, body: str = "run ok\n") -> Path:
    path = tmp / name
    path.write_text(body)
    stamp = NOW.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def fake_runner(tmp: Path, tcp=None, http=None, systemctl=None,
                journalctl=None, now=NOW):
    """Green-by-default injected runner; tests break one surface at a time."""
    logs = {log for _u, log, _a, _c in vscl.VANADIUM_UNITS}
    for name in logs:
        # Seed only when absent: a test that pre-wrote a fault log needs its
        # content to survive the runner construction.
        if not (tmp / name).exists():
            fresh_log(tmp, name)

    def default_tcp(host, port):
        return (True, "") if host.endswith(("209", "82", "229")) else (False, "closed")

    def default_systemctl(args):
        if args[0] == "is-enabled":
            return 0, "enabled\n", ""
        unit = args[-1]
        return 0, (f"Tue 2026-11-10 12:10:00 EST 10min "
                   f"Tue {FRESH} EST 10min ago {unit}"), ""

    return vscl.Runners(
        tcp=tcp or default_tcp,
        http=http or (lambda url: (200, {"status": "UP"})),
        systemctl=systemctl or default_systemctl,
        journalctl=journalctl or (lambda args: (0, JOURNAL_OK, "")),
    )


def get(rep, cid):
    return next(c for c in rep.checks if c.cid == cid)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old_log_dir = vscl.LOG_DIR
        vscl.LOG_DIR = self.tmp

    def tearDown(self):
        vscl.LOG_DIR = self._old_log_dir
        self._tmp.cleanup()

    def vanadium_report(self, reachable=True, skipped=False, r=None, **kw):
        r = r or fake_runner(self.tmp, **kw)
        rep = vscl.Report()
        vscl.check_vanadium(rep, r, NOW, reachable, skipped=skipped)
        return rep


class Helpers(Base):
    def test_timer_line_takes_last_stamp(self):
        line = ("Tue 2026-11-10 12:10:00 EST 10min "
                "Mon 2026-11-09 02:59:40 EST 17h ago sync-pg-to-vanadium.timer")
        self.assertEqual(vscl.parse_timer_line(line), "2026-11-09 02:59:40")

    def test_timer_line_never_triggered(self):
        line = "Tue 2026-11-10 12:10:00 EST 10min n/a n/a foo.timer"
        self.assertIsNone(vscl.parse_timer_line(line))

    def test_load_twin_ports_parses_canary_only(self):
        p = self.tmp / "ports.yaml"
        p.write_text(
            "infra:\n"
            "  - port: 4050\n"
            '    name: "search"\n'
            "canary:\n"
            "  - port: 4100\n"
            '    name: "kernel"\n'
            "  - port: 4101\n"
            '    name: "nebula"\n')
        self.assertEqual(vscl.load_twin_ports(p),
                         [(4100, "kernel"), (4101, "nebula")])

    def test_load_twin_ports_falls_back_when_missing(self):
        pairs = vscl.load_twin_ports(self.tmp / "nope.yaml")
        self.assertIn((4101, "nebula"), pairs)
        self.assertIn((4501, "prompt-sync"), pairs)

    def test_last_meaningful_line_skips_blanks(self):
        self.assertEqual(vscl.last_meaningful_line("a\n\n  \n"), "a")


class VanadiumClassifier(Base):
    def test_healthy_all_pass(self):
        rep = self.vanadium_report()
        self.assertEqual([c.status for c in rep.checks], ["PASS"] * 3)

    def test_disabled_timer_fails(self):
        def systemctl(args):
            if args[0] == "is-enabled":
                return 1, "disabled\n", ""
            return 0, f"Tue 2026-11-10 12:10:00 EST 10min Tue {FRESH} EST 10min ago {args[-1]}", ""
        rep = self.vanadium_report(systemctl=systemctl)
        self.assertEqual(get(rep, "V1").status, "FAIL")
        self.assertIn("timer not enabled", get(rep, "V1").evidence)

    def test_stale_trigger_fails(self):
        def systemctl(args):
            if args[0] == "is-enabled":
                return 0, "enabled\n", ""
            return 0, f"Tue 2026-11-10 12:10:00 EST 10min Tue {STALE} EST 4h ago {args[-1]}", ""
        rep = self.vanadium_report(systemctl=systemctl)
        # sync tier has a 1h budget; V2/V3 are daily and still inside 26h.
        self.assertEqual(get(rep, "V1").status, "FAIL")
        self.assertIn("last trigger", get(rep, "V1").evidence)
        self.assertEqual(get(rep, "V2").status, "PASS")

    def test_failed_run_fails_when_reachable(self):
        rep = self.vanadium_report(
            journalctl=lambda args: (0, JOURNAL_FAIL, ""))
        self.assertEqual(get(rep, "V1").status, "FAIL")
        self.assertIn("last run failed", get(rep, "V1").evidence)

    def test_failed_run_is_unreachable_when_offline(self):
        rep = self.vanadium_report(
            reachable=False, journalctl=lambda args: (0, JOURNAL_FAIL, ""))
        self.assertEqual(get(rep, "V1").status, "UNREACHABLE")
        self.assertFalse(rep.has_fail)
        self.assertIn("off-network", get(rep, "V1").evidence)

    def test_log_recorded_target_down_fails_when_reachable(self):
        fresh_log(self.tmp, "sync-vanadium.log",
                  "target unreachable: 'vanadium' does not resolve\n")
        rep = self.vanadium_report()
        self.assertEqual(get(rep, "V1").status, "FAIL")
        self.assertIn("target unreachable", get(rep, "V1").evidence)

    def test_log_recorded_target_down_is_unreachable_when_offline(self):
        fresh_log(self.tmp, "sync-vanadium.log",
                  "target unreachable: 'vanadium' does not resolve\n")
        rep = self.vanadium_report(reachable=False)
        self.assertEqual(get(rep, "V1").status, "UNREACHABLE")
        self.assertFalse(rep.has_fail)

    def test_log_recorded_error_fails(self):
        fresh_log(self.tmp, "sync-vanadium.log",
                  "psql: error: could not translate host name\n")
        rep = self.vanadium_report()
        self.assertIn("log reports an error", get(rep, "V1").evidence)

    def test_drive_absent_guard_is_skip(self):
        fresh_log(self.tmp, "vanadium-ci-backup.log",
                  "SKIP: spool directory is a dangling symlink (-> /mnt/x) "
                  "— removable drive absent.\n")
        rep = self.vanadium_report()
        self.assertEqual(get(rep, "V3").status, "SKIP")
        self.assertIn("drive-absent guard honoured", get(rep, "V3").evidence)
        self.assertFalse(rep.has_fail)

    def test_drive_absent_but_target_down_fails_when_reachable(self):
        fresh_log(self.tmp, "vanadium-ci-backup.log",
                  "SKIP: drive absent\n"
                  "SEPARATE CONDITION — the target vanadium is ALSO unreachable:\n"
                  "  target unreachable: 'vanadium' does not resolve\n")
        rep = self.vanadium_report()
        self.assertEqual(get(rep, "V3").status, "FAIL")
        self.assertIn("target unreachable", get(rep, "V3").evidence)

    def test_missing_log_fails(self):
        r = fake_runner(self.tmp)
        (self.tmp / "sync-vanadium.log").unlink()
        rep = self.vanadium_report(r=r)
        self.assertEqual(get(rep, "V1").status, "FAIL")
        self.assertIn("log missing", get(rep, "V1").evidence)

    def test_skip_remote_labels_defects_as_skip(self):
        def systemctl(args):
            return (1, "disabled\n", "") if args[0] == "is-enabled" else (0, "", "")
        rep = self.vanadium_report(systemctl=systemctl, skipped=True)
        self.assertEqual(get(rep, "V1").status, "SKIP")
        self.assertIn("--skip-remote", get(rep, "V1").evidence)


class Fleet(Base):
    def test_all_open(self):
        r = fake_runner(self.tmp)
        rep = vscl.Report()
        self.assertTrue(vscl.check_fleet(r, rep))
        self.assertEqual([c.status for c in rep.checks], ["PASS"] * 3)

    def test_vanadium_absent_is_unreachable(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "no route"))
        rep = vscl.Report()
        self.assertFalse(vscl.check_fleet(r, rep))
        self.assertEqual([c.status for c in rep.checks], ["UNREACHABLE"] * 3)
        self.assertFalse(rep.has_fail)


class Twins(Base):
    def test_zero_bound_matches_port_map(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "closed"))
        rep = vscl.Report()
        vscl.check_twins(rep, r, [(4101, "nebula"), (4116, "aegis")])
        self.assertEqual(get(rep, "T1").status, "PASS")
        self.assertIn("0/2", get(rep, "T1").evidence)

    def test_listeners_are_named(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (p == 4116, "closed"))
        rep = vscl.Report()
        vscl.check_twins(rep, r, [(4101, "nebula"), (4116, "aegis")])
        self.assertIn("4116:aegis", get(rep, "T1").evidence)
        self.assertNotIn("4101", get(rep, "T1").evidence)


class Jvm(Base):
    def test_thallium_absent_is_unreachable(self):
        r = fake_runner(self.tmp)
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=False,
                       helium_reachable=False)
        self.assertEqual(get(rep, "J1").status, "UNREACHABLE")
        self.assertEqual(get(rep, "J2").status, "UNREACHABLE")
        self.assertFalse(rep.has_fail)

    def test_thallium_closed_port_fails(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "refused"))
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=True,
                       helium_reachable=True)
        self.assertEqual(get(rep, "J1").status, "FAIL")

    def test_thallium_actuator_up_passes_with_disk(self):
        r = fake_runner(
            self.tmp,
            tcp=lambda h, p: (True, ""),
            http=lambda u: (200, {"status": "UP", "components": {
                "diskSpace": {"details": {"free": 10 * 1024 ** 3}}}}))
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=True,
                       helium_reachable=True)
        self.assertEqual(get(rep, "J1").status, "PASS")
        self.assertIn("disk_free=10.0GB", get(rep, "J1").evidence)

    def test_thallium_actuator_down_fails(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (True, ""),
                        http=lambda u: (200, {"status": "DOWN"}))
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=True,
                       helium_reachable=False)
        self.assertEqual(get(rep, "J1").status, "FAIL")

    def test_helium_closed_is_pass_retired(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "refused"))
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=False,
                       helium_reachable=True)
        self.assertEqual(get(rep, "J2").status, "PASS")
        self.assertIn("retired", get(rep, "J2").evidence)

    def test_helium_open_warns(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (True, ""))
        rep = vscl.Report()
        vscl.check_jvm(rep, r, thallium_reachable=False,
                       helium_reachable=True)
        self.assertEqual(get(rep, "J2").status, "WARN")


class Corpus(Base):
    def _ports(self):
        p = self.tmp / "ports.yaml"
        p.write_text(
            "canary:\n"
            "  - port: 4101\n"
            '    name: "nebula"\n'
            "  - port: 4116\n"
            '    name: "aegis"\n')
        return p

    def test_green_verdict_with_honest_github_skip(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (h.endswith("82"), "closed")
                        if p == 8092 else (True, ""))
        rep = vscl.build_report(r, now=NOW, ports_yaml=self._ports())
        self.assertFalse(rep.has_fail)
        self.assertEqual(get(rep, "G1").status, "SKIP")
        self.assertEqual(rep.verdict(), "green-with-honest-skips")

    def test_offline_verdict_is_inconclusive_not_defects(self):
        def tcp(host, port):
            return (host == "127.0.0.1" and port == 9999), "no route"
        r = fake_runner(self.tmp, tcp=tcp,
                        journalctl=lambda a: (0, JOURNAL_FAIL, ""))
        rep = vscl.build_report(r, now=NOW, ports_yaml=self._ports())
        self.assertFalse(rep.has_fail)
        self.assertEqual(rep.verdict(), "off-network-inconclusive")
        self.assertEqual(get(rep, "F1").status, "UNREACHABLE")
        self.assertEqual(get(rep, "V1").status, "UNREACHABLE")

    def test_skip_remote_skips_every_network_item(self):
        r = fake_runner(self.tmp)
        rep = vscl.build_report(r, now=NOW, skip_remote=True,
                                ports_yaml=self._ports())
        for cid in ("F1", "F2", "F3", "T1", "J1", "J2"):
            self.assertEqual(get(rep, cid).status, "SKIP", cid)
        self.assertFalse(rep.has_fail)


class Main(Base):
    def _main(self, argv, runner):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return vscl.main(argv, now=NOW, runners=runner), buf.getvalue()

    def test_exit0_on_offline_run(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "no route"))
        code, _ = self._main(["--json"], r)
        self.assertEqual(code, 0)

    def test_exit1_on_real_defect(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (True, "")
                        if p != 8092 else (False, "refused"))
        code, _ = self._main(["--json"], r)
        self.assertEqual(code, 1)

    def test_json_summary_shape(self):
        r = fake_runner(self.tmp, tcp=lambda h, p: (False, "no route"))
        _code, out = self._main(["--json"], r)
        summary = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(summary["tool"], "verify-same-lan-checklist")
        self.assertEqual(summary["schema_version"], 1)
        self.assertIn(summary["verdict"],
                      ("green", "green-with-honest-skips",
                       "off-network-inconclusive", "defects"))
        self.assertEqual([c["id"] for c in summary["checks"]][:3],
                         ["F1", "F2", "F3"])


if __name__ == "__main__":
    unittest.main()
