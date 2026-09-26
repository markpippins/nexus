"""Tests for bin/drain_write_queue_buffer.py — buffered_local JSONL drainer.

Covers the drain pass end to end with the publish callable injected (no
NATS, no PG):

  - happy path:    every complete line republished to its embedded subject,
                   byte-fidelity payload, buffer truncated to empty
  - torn tail:     a trailing partial line (in-flight/crash-torn producer
                   write) is left in place, never published or guessed at
  - quarantine:    malformed JSON / missing subject / out-of-stream subject
                   lines are quarantined raw (appended, never republished
                   cross-stream) and consumed from the buffer
  - abort safety:  a publish failure raises BEFORE truncation — buffer
                   bytes unchanged (at-least-once replay is safe: the
                   reconciler dedups on write_id)
  - no-ops:        missing/empty buffer, blank lines, single torn write
  - dry run:       counts and classifies but writes nothing anywhere
  - gate:          provisioning helper missing / failing / succeeding
  - buffer path:   NEXUS_CORE_WRITEQUEUE_DIR mirroring of the producer

Run:
  python3 -m pytest bin/tests/test_drain_write_queue_buffer.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "bin"))

import drain_write_queue_buffer as dwb  # noqa: E402


def _envelope_line(subject="nexus.write-queue.v1.solscript.proposition",
                   write_id="w-1", correlation_id=None) -> bytes:
    """A realistic CanonicalEnvelope line as WriteQueueProducer.bufferLocally writes it."""
    envelope = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": "WriteQueueEntry",
        "occurred_at": "2026-09-26T05:00:00Z",
        "origin_system": "nexus-core",
        "origin_component": "nexus-core-solscript",
        "correlation_id": correlation_id or write_id,
        "classification": "internal",
        "subject": subject,
        "payload": {
            "correlationId": correlation_id or write_id,
            "intent": {
                "writeId": write_id,
                "target": "solscript.proposition",
                "verb": "transition-entity",
                "payload": {},
                "schemaVersion": "1",
            },
        },
    }
    return json.dumps(envelope).encode()


class DrainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.buffer = pathlib.Path(self.tmp.name) / "write-queue-buffer.jsonl"
        self.quarantine = self.buffer.with_suffix(self.buffer.suffix + ".quarantine")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, *chunks: bytes) -> None:
        self.buffer.write_bytes(b"".join(chunks))

    def test_publishes_each_complete_line_to_embedded_subject(self):
        l1, l2 = _envelope_line(write_id="w-1"), _envelope_line(write_id="w-2")
        self._write(l1 + b"\n", l2 + b"\n")
        calls: list[tuple[str, bytes]] = []
        published, quarantined, torn, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: calls.append((s, p)))
        self.assertEqual((published, quarantined, torn, consumed), (2, 0, 0, True))
        self.assertEqual([s for s, _ in calls],
                         ["nexus.write-queue.v1.solscript.proposition"] * 2)
        self.assertEqual(calls[0][1], l1)  # byte fidelity
        self.assertEqual(calls[1][1], l2)
        self.assertEqual(self.buffer.read_bytes(), b"")  # rotated away

    def test_torn_tail_is_merged_back(self):
        l1 = _envelope_line(write_id="w-1")
        torn = _envelope_line(write_id="w-2")[:20]  # crash-torn write
        self._write(l1 + b"\n", torn)
        calls: list[tuple[str, bytes]] = []
        published, quarantined, torn_count, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: calls.append((s, p)))
        self.assertEqual((published, quarantined, torn_count, consumed), (1, 0, 1, True))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.buffer.read_bytes(), torn)  # survives for a later pass
        self.assertFalse(self.buffer.with_suffix(self.buffer.suffix + ".draining").exists())

    def test_malformed_and_wrong_subject_quarantined(self):
        good = _envelope_line(write_id="w-1")
        bad_json = b"{not json at all"
        other_stream = _envelope_line(write_id="w-3",
                                      subject="nexus.events.something")
        no_subject = json.dumps({"event_type": "WriteQueueEntry"}).encode()
        self._write(good + b"\n", bad_json + b"\n", other_stream + b"\n", no_subject + b"\n")
        calls: list[tuple[str, bytes]] = []
        published, quarantined, torn, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: calls.append((s, p)))
        self.assertEqual((published, quarantined, torn, consumed), (1, 3, 0, True))
        self.assertEqual(len(calls), 1)  # the wrong-stream line was NOT republished
        q_lines = self.quarantine.read_bytes().splitlines()
        self.assertEqual(len(q_lines), 3)
        self.assertEqual(sorted(q_lines), sorted([bad_json, other_stream, no_subject]))
        self.assertEqual(self.buffer.read_bytes(), b"")

    def test_quarantine_appends_not_overwrites(self):
        self.quarantine.write_bytes(b"pre-existing forensic line\n")
        bad = b"{broken"
        self._write(bad + b"\n")
        dwb.drain(self.buffer, self.quarantine, lambda s, p: None)
        q = self.quarantine.read_bytes().splitlines()
        self.assertEqual(q, [b"pre-existing forensic line", bad])

    def test_publish_failure_aborts_before_any_file_operation(self):
        l1, l2 = _envelope_line(write_id="w-1"), _envelope_line(write_id="w-2")
        original = l1 + b"\n" + l2 + b"\n"
        self._write(l1 + b"\n", l2 + b"\n")

        def failing_publish(subject, payload):
            raise RuntimeError("nats down")

        with self.assertRaises(RuntimeError):
            dwb.drain(self.buffer, self.quarantine, failing_publish)
        # Nothing lost, nothing moved: buffer byte-identical, no quarantine,
        # no rotated work file.
        self.assertEqual(self.buffer.read_bytes(), original)
        self.assertFalse(self.quarantine.exists())
        self.assertFalse(self.buffer.with_suffix(self.buffer.suffix + ".draining").exists())

    def test_stale_work_file_recovered_same_pass(self):
        # Simulate a crash between rotate and merge-back: already-published
        # lines sit in .draining while a fresh intent landed on the live
        # buffer. Recovery happens BEFORE the read, so the stale lines join
        # THIS pass's publish set (at-least-once: dedup on write_id), and
        # the stale file is resolved.
        stale = _envelope_line(write_id="w-old")
        fresh = _envelope_line(write_id="w-new")
        work = self.buffer.with_suffix(self.buffer.suffix + ".draining")
        work.write_bytes(stale + b"\n")
        self._write(fresh + b"\n")
        calls: list[tuple[str, bytes]] = []
        dwb.drain(self.buffer, self.quarantine, lambda s, p: calls.append((s, p)))
        payloads = [p for _, p in calls]
        self.assertIn(stale, payloads)        # orphaned line re-published this pass
        self.assertIn(fresh, payloads)        # live line drained normally
        self.assertFalse(work.exists())       # stale file resolved

    def test_blank_lines_consumed_silently(self):
        l1, l2 = _envelope_line(write_id="w-1"), _envelope_line(write_id="w-2")
        self._write(b"\n", l1 + b"\n", b"\n\n", l2 + b"\n")
        calls: list[tuple[str, bytes]] = []
        published, quarantined, torn, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: calls.append((s, p)))
        self.assertEqual((published, quarantined, torn, consumed), (2, 0, 0, True))
        self.assertEqual(self.buffer.read_bytes(), b"")

    def test_missing_buffer_is_noop(self):
        published, quarantined, torn, truncated = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: None)
        self.assertEqual((published, quarantined, torn, truncated), (0, 0, 0, False))

    def test_empty_buffer_is_noop(self):
        self._write()
        published, quarantined, torn, truncated = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: None)
        self.assertEqual((published, quarantined, torn, truncated), (0, 0, 0, False))

    def test_only_torn_write_is_noop(self):
        torn = _envelope_line(write_id="w-1")[:15]
        self._write(torn)
        published, quarantined, torn_count, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: None)
        self.assertEqual((published, quarantined, torn_count, consumed), (0, 0, 1, False))
        self.assertEqual(self.buffer.read_bytes(), torn)  # untouched

    def test_dry_run_touches_nothing(self):
        good = _envelope_line(write_id="w-1")
        bad = b"{broken"
        original = good + b"\n" + bad + b"\n"
        self._write(good + b"\n", bad + b"\n")
        calls: list[tuple[str, bytes]] = []
        published, quarantined, torn, consumed = dwb.drain(
            self.buffer, self.quarantine, lambda s, p: calls.append((s, p)), dry_run=True)
        self.assertEqual((published, quarantined, torn, consumed), (1, 1, 0, False))
        self.assertEqual(calls, [])                      # nothing published
        self.assertEqual(self.buffer.read_bytes(), original)  # untouched
        self.assertFalse(self.quarantine.exists())       # no quarantine side effect
        self.assertFalse(self.buffer.with_suffix(self.buffer.suffix + ".draining").exists())


class GateTest(unittest.TestCase):
    def test_helper_missing_fails(self):
        with mock.patch.object(dwb, "HELPER", pathlib.Path("/nonexistent/helper.py")):
            ok, detail = dwb.gate_stream("nats://localhost:4222")
        self.assertFalse(ok)
        self.assertIn("missing", detail)

    def test_helper_failure_fails_with_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = pathlib.Path(tmp) / "helper.py"
            helper.write_text("import sys; print('boom', file=sys.stderr); sys.exit(5)\n")
            with mock.patch.object(dwb, "HELPER", helper):
                ok, detail = dwb.gate_stream("nats://localhost:4222")
        self.assertFalse(ok)
        self.assertIn("exit 5", detail)
        self.assertIn("boom", detail)

    def test_helper_success_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = pathlib.Path(tmp) / "helper.py"
            helper.write_text("import sys; sys.exit(0)\n")
            with mock.patch.object(dwb, "HELPER", helper):
                ok, detail = dwb.gate_stream("nats://localhost:4222")
        self.assertTrue(ok)
        self.assertEqual(detail, "stream ensured")


class BufferPathTest(unittest.TestCase):
    def test_env_var_mirrors_producer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"NEXUS_CORE_WRITEQUEUE_DIR": tmp}):
                p = dwb._buffer_path()
        self.assertEqual(p, pathlib.Path(tmp) / "write-queue-buffer.jsonl")

    def test_default_dir(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = dwb._buffer_path()
        self.assertEqual(p, pathlib.Path("/tmp/nexus-writequeue") / "write-queue-buffer.jsonl")


if __name__ == "__main__":
    unittest.main()
