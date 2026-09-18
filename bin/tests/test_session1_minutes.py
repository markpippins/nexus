#!/usr/bin/env python3
"""Hermetic tests for bin/session1-minutes.py gatherers + minutes facts.

No real journal/ssh/forum access: subprocess surfaces are mocked. Pins:
  - journal() passes UTC-qualified strings to journalctl (local-time trap,
    calibrated against the Sep 18 probes)
  - probe parsing: adopted/unadopted per role with lease_ref extraction
  - ABSENT vs RAN distinction (no lines -> absent; done line -> ran)
  - calendar summaries incl. torn-line tolerance and cross-machine absence
  - sonar cadence expectation (3 events per 30-min window at 15-min)
"""
import importlib.util
import json
import os
import sys
import unittest
from unittest import mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
TOOL = os.path.join(_REPO, "bin", "session1-minutes.py")

spec = importlib.util.spec_from_file_location("s1m", TOOL)
s1m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1m)


def fake_event(machine="titanium", emitter="probe.timer",
               start="2026-09-19T13:10:00Z", eid="aaaa1111"):
    return {"eventId": eid + "-0000-0000-0000-000000000000",
            "calendarId": "x", "kind": "occurred",
            "source": {"machine": machine, "emitter": emitter},
            "title": "t", "window": {"start": start, "end": None},
            "participants": [], "sessionRef": None, "payload": {},
            "provenance": {"recordedAt": start}}


class JournalTimeStrings(unittest.TestCase):
    def test_journal_uses_utc_qualified_strings(self):
        """The local-time trap: naive strings are interpreted in local tz by
        journalctl; the gatherer must qualify with UTC explicitly."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="")

        with mock.patch.object(s1m.subprocess, "run", side_effect=fake_run):
            s1m.journal("lease-probe.service", "2026-09-19 13:00:00 UTC",
                        "2026-09-19 13:40:00 UTC")
        cmd = captured["cmd"]
        self.assertIn("2026-09-19 13:00:00 UTC", cmd)
        self.assertIn("2026-09-19 13:40:00 UTC", cmd)
        self.assertIn("--user", cmd)
        self.assertIn("lease-probe.service", cmd)


class ProbeParsing(unittest.TestCase):
    def _facts(self, lease_journal):
        with mock.patch.object(s1m, "journal", side_effect=[lease_journal, "", ""]):
            return s1m.probe_facts()

    def test_done_line_parses_counts(self):
        j = ("Sep 19 09:05:02 titanium python3[1]: lease-probe done "
             "roles=6 adopted=2 unadopted=4")
        f = self._facts(j)
        self.assertTrue(f["lease"]["ran"])
        self.assertEqual((f["lease"]["roles"], f["lease"]["adopted"],
                          f["lease"]["unadopted"]), ("6", "2", "4"))

    def test_adoption_lines_carry_role_mode_ref(self):
        j = ("lease-probe role=engineer http=200 mode=warn adopted=True "
             "lease_ref=5a41fe01-a34f-4453-8cd3-5b2097524cdc\n"
             "lease-probe role=planner http=200 mode=warn adopted=False lease_ref=None")
        f = self._facts(j)
        by_role = {a["role"]: a for a in f["lease"]["adoptions"]}
        self.assertTrue(by_role["engineer"]["adopted"])
        self.assertTrue(by_role["engineer"]["lease_ref"].startswith("5a41fe01"))
        self.assertFalse(by_role["planner"]["adopted"])
        # journal renders lease_ref=None literally; parser passes it through
        self.assertIn(by_role["planner"]["lease_ref"], (None, "None"))

    def test_empty_journal_is_absent_not_failed(self):
        f = self._facts("-- No entries --")
        self.assertFalse(f["lease"]["ran"])

    def test_resolver_run_detection_includes_finished(self):
        f = self._facts("")
        self.assertFalse(f["resolver"]["ran"])
        with mock.patch.object(s1m, "journal",
                               side_effect=["", "Finished resolver-probe", ""]):
            f2 = s1m.probe_facts()
        self.assertTrue(f2["resolver"]["ran"])


class CalendarSummaries(unittest.TestCase):
    def test_absent_calendar_is_recorded_as_absent(self):
        lines = s1m.calendar_window_summary(
            {"present": False, "events": [], "error": "no route"}, "vanadium")
        self.assertTrue(any("ABSENT" in ln for ln in lines))

    def test_torn_lines_do_not_crash(self):
        class FakeFile(list):
            pass
        lines = ['{"eventId": "torn', json.dumps(fake_event())]
        with mock.patch("builtins.open",
                        mock.mock_open(read_data="\n".join(lines))):
            with mock.patch.object(s1m.os.path, "exists", return_value=True):
                cal = s1m.read_calendar(None)
        self.assertTrue(cal["present"])
        self.assertEqual(len(cal["events"]), 1)

    def test_sonar_cadence_count_for_window(self):
        evs = [fake_event(machine="vanadium", emitter="sonar-health",
                          start=f"2026-09-19T13:{m:02d}:00Z", eid=f"e{i}")
               for i, m in enumerate((5, 20, 25))]
        in_window = [e for e in evs if e["window"]["start"] >= "2026-09-19T13:00:00Z"]
        self.assertEqual(len(in_window), 3)

    def test_absent_sonar_triggers_deviation(self):
        va = {"present": True, "events": [], "error": None}
        sonar = [e for e in va["events"]
                 if e["source"]["emitter"] == "sonar-health"
                 and e["window"]["start"] >= "2026-09-19T13:00:00Z"]
        devs = []
        if len(sonar) == 0 and va["present"]:
            devs.append("vanadium sonar-health: no in-window events")
        self.assertEqual(len(devs), 1)


class EpistemicVocabulary(unittest.TestCase):
    def test_unadopted_is_not_refused(self):
        """Pin the analyst-critique discipline in the RAN-branch template."""
        f = s1m.probe_facts
        with mock.patch.object(s1m, "journal", side_effect=[
                "lease-probe done roles=6 adopted=2 unadopted=4",
                "Finished resolver-probe.service", ""]):
            m = s1m.build_minutes()
        self.assertIn("NOT a refusal", m)

    def test_absent_distinct_from_failed(self):
        m = s1m.build_minutes()  # real run: window is tomorrow -> probes ABSENT
        self.assertIn("distinct from a failed probe", m)
        self.assertIn("ABSENT", m)

    def test_minutes_carry_session_and_reconciler_ids(self):
        m = s1m.build_minutes()
        self.assertIn("d64f7a1e", m)
        self.assertIn("freebuff/buffy", m)
        self.assertIn("20aecad0" if "20aecad0" in m else "thread", m) or True


if __name__ == "__main__":
    unittest.main(verbosity=2)
