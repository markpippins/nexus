#!/usr/bin/env python3
"""Hermetic tests for bin/calendar-consolidate.py (fold-back intake).

No database, no network: the sink seam (exec_fn) is injected, and the
emitter module is loaded by path exactly as the tool loads it. Pins the
contract from R1 567ee961 / thread a330914e:

  - validation: required keys, kind vocabulary, window ordering/RFC3339,
    observed-requires-consolidatedFrom, participants array
  - Q2 identity: ids are RE-DERIVED and compared, never trusted
    (mismatched eventId -> 'foreign id' rejection)
  - inert detection: to_regclass NULL -> named refusal, exit 3, ZERO
    writes (store_present is the only query before the gate)
  - sink: idempotent ON CONFLICT upsert, parent calendar ensured,
    recorded_by carries the consolidator, provenance.recordedAt honored
  - torn/invalid JSONL lines are data, never a crash; --strict exits 4
  - never-raises: sink errors are collected per-event as data
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
TOOL = os.path.join(_REPO, "bin", "calendar-consolidate.py")

spec = importlib.util.spec_from_file_location("cc", TOOL)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def make_event(machine="titanium", emitter="probe.timer", start="2026-09-18T13:25:00Z",
               kind="occurred", **overrides):
    """Build a wire-shaped event exactly as the emitter does."""
    ev = {
        "eventId": cc.derive_event_id(machine, emitter, start),
        "calendarId": cc.derive_event_id(machine, "local-calendar", "calendar-root"),
        "kind": kind,
        "source": {"machine": machine, "emitter": emitter},
        "title": f"timer {emitter} occurred",
        "window": {"start": start, "end": None},
        "participants": [],
        "sessionRef": None,
        "payload": {},
        "provenance": {"recordedAt": "2026-09-18T13:25:04Z"},
    }
    ev.update(overrides)
    return ev


def write_jsonl(events, torn_line=None, extra_text=None):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
        if torn_line is not None:
            fh.write(torn_line)
        if extra_text is not None:
            fh.write(extra_text)
    return Path(path)


def query_log_exec(log, present=True, insert_results=None):
    """Fake exec_fn: records (sql, params), answers to_regclass and INSERTs."""
    calls = []

    def exec_fn(sql, params=()):
        calls.append((sql, params))
        if "to_regclass" in sql:
            return [(("vision.calendar_events" if present else None),)]
        if sql.strip().startswith("INSERT INTO vision.calendars"):
            return []
        if sql.strip().startswith("INSERT INTO vision.calendar_events"):
            if insert_results is not None and insert_results:
                return [(insert_results.pop(0),)]
            return [("row",)] if (insert_results is None) else []
        return []
    exec_fn.calls = calls
    return exec_fn


class ValidationTests(unittest.TestCase):
    def test_valid_event_passes(self):
        self.assertEqual(cc.validate_event(make_event()), [])

    def test_missing_required_keys(self):
        ev = make_event()
        del ev["window"]
        errs = cc.validate_event(ev)
        self.assertTrue(any("window" in e for e in errs))

    def test_bad_kind_rejected(self):
        errs = cc.validate_event(make_event(kind="rumored"))
        self.assertTrue(any("kind" in e for e in errs))

    def test_foreign_event_id_rejected(self):
        # A trusted-but-wrong id would poison consolidation — the tool
        # re-derives and compares instead.
        ev = make_event(eventId=str(uuid.uuid4()))
        errs = cc.validate_event(ev)
        self.assertTrue(any("eventId mismatch" in e for e in errs))

    def test_window_ordering(self):
        errs = cc.validate_event(make_event(start="2026-09-18T13:25:00Z",
                                            window={"start": "2026-09-18T13:25:00Z",
                                                    "end": "2026-09-18T13:24:00Z"}))
        self.assertTrue(any("precedes" in e for e in errs))

    def test_bad_timestamps(self):
        errs = cc.validate_event(make_event(start="not-a-time"))
        self.assertTrue(any("RFC3339" in e for e in errs))
        # empty string is caught by the missing-start branch
        errs = cc.validate_event(make_event(start=""))
        self.assertTrue(any("window.start" in e for e in errs))

    def test_observed_requires_consolidated_from(self):
        errs = cc.validate_event(make_event(kind="observed"))
        self.assertTrue(any("observed" in e for e in errs))
        ok = make_event(kind="observed",
                        consolidatedFrom=[str(uuid.uuid4())])
        self.assertEqual(cc.validate_event(ok), [])

    def test_participants_must_be_array(self):
        errs = cc.validate_event(make_event(participants={"role": "dba"}))
        self.assertTrue(any("participants" in e for e in errs))


class ParseTests(unittest.TestCase):
    def test_torn_line_is_data(self):
        good = make_event()
        path = write_jsonl([good], torn_line='{"eventId": "torn')
        try:
            events, invalid = cc.load_events(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(invalid), 1)
            self.assertIn("line", invalid[0])
        finally:
            path.unlink()

    def test_non_object_line_is_invalid(self):
        path = write_jsonl([], extra_text='"just a string"\n')
        try:
            events, invalid = cc.load_events(path)
            self.assertEqual(events, [])
            self.assertEqual(len(invalid), 1)
        finally:
            path.unlink()


class InertGateTests(unittest.TestCase):
    def test_absent_store_named_refusal_zero_writes(self):
        exec_fn = query_log_exec(None, present=False)
        path = write_jsonl([make_event()])
        try:
            with mock.patch.object(cc, "default_exec_factory", return_value=exec_fn):
                rc = cc.cmd_observe(mock.Mock(source=str(path), by=None,
                                              strict=False, dry_run=False,
                                              json=True))
            self.assertEqual(rc, 3)
            inserts = [c for c in exec_fn.calls if "INSERT" in c[0]]
            self.assertEqual(inserts, [], "inert gate must precede any write")
            regclass = [c for c in exec_fn.calls if "to_regclass" in c[0]]
            self.assertEqual(len(regclass), 1, "gate is the FIRST query")
        finally:
            path.unlink()

    def test_unreachable_store_is_hard_error_not_inert(self):
        # Unreachable != absent: collapsing them would attest the fold-back
        # ran (or honestly failed) when neither is known.
        def boom(*a, **k):
            raise ConnectionError("no route to host")
        with mock.patch.object(cc, "default_exec_factory", return_value=boom):
            rc = cc.cmd_observe(mock.Mock(source="/nonexistent.jsonl",
                                          by=None, strict=False,
                                          dry_run=False, json=True))
        self.assertEqual(rc, 1)


class SinkTests(unittest.TestCase):
    def test_insert_and_parent_calendar(self):
        ev = make_event()
        exec_fn = query_log_exec(None, present=True)
        outcome = cc.sink_event(exec_fn, ev, consolidator="dba-consolidator")
        self.assertEqual(outcome, "inserted")
        sqls = [c[0] for c in exec_fn.calls]
        self.assertTrue(any("vision.calendars" in s for s in sqls))
        ev_sql, ev_params = next(c for c in exec_fn.calls
                                 if "vision.calendar_events" in c[0])
        self.assertIn("ON CONFLICT (event_id) DO NOTHING", ev_sql)
        # recorded_by is the consolidator; source stays in source columns
        self.assertIn("dba-consolidator", ev_params)
        self.assertIn("titanium", ev_params)

    def test_recorded_at_honored_from_provenance(self):
        ev = make_event()
        exec_fn = query_log_exec(None, present=True)
        cc.sink_event(exec_fn, ev, "c")
        _, params = next(c for c in exec_fn.calls
                         if "vision.calendar_events" in c[0])
        self.assertIn("2026-09-18T13:25:04Z", params)

    def test_observed_row_carries_consolidated_from(self):
        fid = str(uuid.uuid4())
        ev = make_event(kind="observed", consolidatedFrom=[fid])
        exec_fn = query_log_exec(None, present=True)
        cc.sink_event(exec_fn, ev, "c")
        _, params = next(c for c in exec_fn.calls
                         if "vision.calendar_events" in c[0])
        self.assertIn(fid, params[-1])


class ObserveTests(unittest.TestCase):
    def test_observe_counts_insert_and_skip(self):
        e1, e2 = make_event(emitter="a.timer"), make_event(emitter="b.timer")
        path = write_jsonl([e1, e2])
        exec_fn = query_log_exec(None, present=True,
                                 insert_results=[("id1",), None])
        try:
            with mock.patch.object(cc, "default_exec_factory", return_value=exec_fn):
                rc = cc.cmd_observe(mock.Mock(source=str(path), by="op",
                                              strict=False, dry_run=False,
                                              json=True))
            self.assertEqual(rc, 0)
        finally:
            path.unlink()

    def test_dry_run_writes_nothing(self):
        path = write_jsonl([make_event()])
        exec_fn = query_log_exec(None, present=True)
        try:
            with mock.patch.object(cc, "default_exec_factory", return_value=exec_fn):
                rc = cc.cmd_observe(mock.Mock(source=str(path), by=None,
                                              strict=False, dry_run=True,
                                              json=True))
            self.assertEqual(rc, 0)
            self.assertEqual([c for c in exec_fn.calls if "INSERT" in c[0]], [])
        finally:
            path.unlink()

    def test_sink_error_never_raises(self):
        path = write_jsonl([make_event()])

        def flaky(sql, params=()):
            if "INSERT INTO vision.calendar_events" in sql:
                raise RuntimeError("db exploded mid-fold")
            if "to_regclass" in sql:
                return [("vision.calendar_events",)]
            return []
        try:
            with mock.patch.object(cc, "default_exec_factory",
                                   return_value=flaky):
                rc = cc.cmd_observe(mock.Mock(source=str(path), by=None,
                                              strict=False, dry_run=False,
                                              json=True))
            self.assertEqual(rc, 0)  # error collected as data, run completes
        finally:
            path.unlink()

    def test_strict_exit_4_on_invalid(self):
        path = write_jsonl([make_event()], torn_line='{"kind":')
        try:
            with mock.patch.object(cc, "default_exec_factory",
                                   return_value=query_log_exec(None, True)):
                rc = cc.cmd_observe(mock.Mock(source=str(path), by=None,
                                              strict=True, dry_run=True,
                                              json=True))
            self.assertEqual(rc, 4)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
