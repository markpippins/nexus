"""Tests for the JetStream unconsumed-stream monitor.

These tests exist because a monitor that alerts wrongly is worse than no
monitor: it trains the operator to ignore it. So the alert predicate gets
asserted directly, and the two riskiest guards are proven non-vacuous — each
is shown to FAIL when the condition it protects against is reintroduced.

The pure core (StreamState / classify / update_counts / verdict_exit_code) is
tested without a broker. Only `fetch_stream_states` touches a server, and it is
exercised against the live broker only when reachable (skipped otherwise), so
this suite is deterministic offline.

Run:
  python3 -m pytest bin/tests/test_jetstream_unconsumed_monitor.py -v
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "bin" / "jetstream_unconsumed_monitor.py"

_spec = importlib.util.spec_from_file_location("jum", SCRIPT)
jum = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jum)

StreamState = jum.StreamState
classify = jum.classify
update_counts = jum.update_counts
verdict_exit_code = jum.verdict_exit_code
CHECK_FAILED = jum.CHECK_FAILED


def _stream(name="S", messages=0, nbytes=0, max_msgs=-1, max_bytes=-1,
            max_age=0, discard="limits", consumers=0):
    return StreamState(name=name, messages=messages, nbytes=nbytes,
                       max_msgs=max_msgs, max_bytes=max_bytes, max_age=max_age,
                       discard=discard, consumer_count=consumers)


class ClassifyPredicateTest(unittest.TestCase):
    """The alert predicate itself. Ordering of the branches is the design."""

    def test_empty_stream_is_ok(self):
        self.assertEqual(classify(_stream(messages=0, consumers=0)), "ok")

    def test_messages_with_consumer_is_ok(self):
        self.assertEqual(classify(_stream(messages=500, consumers=1)), "ok")

    def test_write_queue_shape_is_critical(self):
        # THE INCIDENT SHAPE: unbounded limits, messages present, no consumer.
        # This is what ran silently for 24 days. Must be critical.
        s = _stream(messages=16, max_msgs=-1, max_bytes=-1, max_age=0,
                    discard="limits", consumers=0)
        self.assertEqual(classify(s), "critical")

    def test_fs_voyager_shape_is_warning_not_critical(self):
        # Capped and shedding: pinned at max_msgs with discard=old. Bounded,
        # not accumulating. Paging on this forever trains the operator to
        # ignore the alert — the exact failure mode this tool exists to avoid.
        # NOTE: this is the bare shape with NO consumer of any kind. The live
        # FS_VOYAGER stream DOES have a core-NATS subscriber and is therefore
        # classified ok — see CoreNatsSubscriberTest.
        s = _stream(messages=50000, max_msgs=50000, discard="old", consumers=0)
        self.assertEqual(classify(s), "warning")

    def test_under_cap_unbounded_is_critical(self):
        # Capped limit but NOT yet reached, and nothing will drain it.
        s = _stream(messages=100, max_msgs=50000, discard="old", consumers=0)
        self.assertEqual(classify(s), "critical")

    def test_byte_cap_with_discard_old_is_warning(self):
        s = _stream(messages=10, nbytes=1 << 30, max_bytes=1 << 30,
                    discard="old", consumers=0)
        self.assertEqual(classify(s), "warning")

    def test_capped_but_discard_new_is_critical(self):
        # discard=new drops the NEW messages, so the stream never sheds its
        # backlog — it is still effectively accumulating garbage. Critical.
        s = _stream(messages=50000, max_msgs=50000, discard="new", consumers=0)
        self.assertEqual(classify(s), "critical")

    def test_bounded_shedding_checked_before_accumulating(self):
        # Ordering regression: if the accumulating branch ran first, the
        # capped FS_VOYAGER stream would be misfiled as critical.
        s = _stream(messages=50000, max_msgs=50000, discard="old", consumers=0)
        self.assertNotEqual(classify(s), "critical")


class CoreNatsSubscriberTest(unittest.TestCase):
    """Regression suite for a FALSE POSITIVE I shipped in the first version.

    The original classify() counted only JetStream consumers
    (jsm.consumers_info()), which is blind to core-NATS subscribers. FS_VOYAGER
    is consumed live by voyager-adapter over core NATS (obs/hint/span) and was
    reported as WARNING "zero consumers" — a running pipeline misread as dead.
    Every test here exists to make that specific miss impossible again.
    """

    def test_fs_voyager_with_core_subscriber_is_ok(self):
        # The exact false positive: capped stream, zero JETSTREAM consumers,
        # but a live core-NATS subscriber. Must be ok, NOT warning.
        s = _stream(messages=50000, max_msgs=50000, discard="old", consumers=0)
        s.subjects = ["nexus.fs.v1.>"]
        s.core_subscribers = 3
        self.assertEqual(classify(s), "ok")

    def test_fs_voyager_without_core_subscriber_still_warns(self):
        # Removing the subscriber must flip it back — proves the assertion
        # actually depends on core_subscribers and is not vacuously ok.
        s = _stream(messages=50000, max_msgs=50000, discard="old", consumers=0)
        s.subjects = ["nexus.fs.v1.>"]
        s.core_subscribers = 0
        self.assertEqual(classify(s), "warning")

    def test_unbounded_with_core_subscriber_is_ok(self):
        # The WRITE_QUEUE shape WITH a core consumer: not critical either.
        s = _stream(messages=16, max_msgs=-1, max_bytes=-1, consumers=0)
        s.subjects = ["nexus.write-queue.v1.>"]
        s.core_subscribers = 1
        self.assertEqual(classify(s), "ok")

    def test_jetstream_consumer_alone_still_suffices(self):
        s = _stream(messages=100, consumers=1)
        self.assertEqual(classify(s), "ok")

    def test_neither_consumer_nor_subscriber_is_flagged(self):
        # The genuine failure case must STILL alert after the fix.
        s = _stream(messages=100, consumers=0)
        s.subjects = ["dead.subject.>"]
        s.core_subscribers = 0
        self.assertEqual(classify(s), "critical")


class SubjectMatchTest(unittest.TestCase):
    """NATS wildcard matching decides whether a subscription is real coverage."""

    def test_exact_match(self):
        self.assertTrue(jum.subject_matches("nexus.fs.v1.observation",
                                            "nexus.fs.v1.observation"))

    def test_gt_wildcard_covers_deeper_subject(self):
        # The FS_VOYAGER case: stream filter 'nexus.fs.v1.>' must be satisfied
        # by a subscriber on 'nexus.fs.v1.observation'.
        self.assertTrue(jum.subject_matches("nexus.fs.v1.>",
                                            "nexus.fs.v1.observation"))
        self.assertTrue(jum.subject_matches("nexus.fs.v1.>", "nexus.fs.v1.span"))

    def test_star_wildcard_matches_exactly_one_token(self):
        self.assertTrue(jum.subject_matches("nexus.*.v1", "nexus.fs.v1"))
        self.assertFalse(jum.subject_matches("nexus.*.v1", "nexus.fs.deep.v1"))

    def test_different_subject_does_not_match(self):
        self.assertFalse(jum.subject_matches("nexus.fs.v1.>",
                                             "nexus.write-queue.v1.x"))

    def test_prefix_without_wildcard_is_not_enough(self):
        # 'nexus.fs.v1' is a DIFFERENT subject from 'nexus.fs.v1.observation';
        # only '>' or '*' makes them related.
        self.assertFalse(jum.subject_matches("nexus.fs.v1",
                                             "nexus.fs.v1.observation"))

    def test_count_counts_only_matching(self):
        subs = ["nexus.fs.v1.observation", "nexus.fs.v1.span",
                "nexus.fs.v1.hint", "other.subject"]
        self.assertEqual(
            jum.count_core_subscribers(["nexus.fs.v1.>"], subs), 3)

    def test_count_zero_when_nothing_matches(self):
        self.assertEqual(
            jum.count_core_subscribers(["nexus.fs.v1.>"], ["a.b.c"]), 0)


class BoundednessHelpersTest(unittest.TestCase):
    def test_at_msg_cap(self):
        self.assertTrue(_stream(messages=100, max_msgs=100).at_msg_cap())
        self.assertFalse(_stream(messages=99, max_msgs=100).at_msg_cap())

    def test_unbounded_never_at_cap(self):
        # max_msgs=-1 is JetStream's "unbounded"; must not read as at-cap.
        self.assertFalse(_stream(messages=10**9, max_msgs=-1).at_msg_cap())

    def test_is_bounded_and_shedding(self):
        self.assertTrue(_stream(messages=100, max_msgs=100,
                                discard="old").is_bounded_and_shedding())
        self.assertFalse(_stream(messages=100, max_msgs=100,
                                 discard="new").is_bounded_and_shedding())


class SustainedWindowTest(unittest.TestCase):
    """The window must delay, never suppress."""

    def test_below_threshold_not_confirmed(self):
        state = {}
        out = update_counts(state, {"S": "critical"}, min_consecutive=2, now=0)
        self.assertEqual(out["S"], (1, False))

    def test_at_threshold_confirmed(self):
        state = {}
        update_counts(state, {"S": "critical"}, 2, 0)
        out = update_counts(state, {"S": "critical"}, 2, 0)
        self.assertEqual(out["S"], (2, True))

    def test_recovery_resets_count(self):
        state = {}
        update_counts(state, {"S": "critical"}, 2, 0)
        update_counts(state, {"S": "critical"}, 2, 0)
        update_counts(state, {"S": "ok"}, 2, 0)
        self.assertEqual(state["S"], 0)

    def test_window_delays_but_never_suppresses(self):
        # After enough ticks the count keeps rising; it must not plateau below
        # the threshold, or a long-lived alert would be permanently muted.
        state = {}
        for _ in range(50):
            update_counts(state, {"S": "critical"}, 3, 0)
        self.assertGreaterEqual(state["S"], 3)


class ExitCodeContractTest(unittest.TestCase):
    def test_all_ok_is_zero(self):
        self.assertEqual(verdict_exit_code({"A": "ok", "B": "ok"}), jum.OK)

    def test_warning_also_alerts(self):
        # A capped/shedding orphan still eats disk and still needs a human;
        # it is lower severity in the report, not invisible to the exit code.
        self.assertEqual(verdict_exit_code({"A": "ok", "B": "warning"}), jum.ALERT)

    def test_critical_alerts(self):
        self.assertEqual(verdict_exit_code({"A": "critical"}), jum.ALERT)


class NonVacuityProofTest(unittest.TestCase):
    """Prove the guards are not trivially true. Each check is shown to change
    outcome when the condition it protects against is reintroduced."""

    def test_critical_guard_is_not_vacuous(self):
        # Guard: WRITE_QUEUE shape is critical. Show it flips if we wrongly
        # attach a consumer — i.e. the assertion depends on consumer_count.
        with_consumer = _stream(messages=16, consumers=1)
        without_consumer = _stream(messages=16, consumers=0)
        self.assertNotEqual(classify(with_consumer), classify(without_consumer))
        self.assertEqual(classify(without_consumer), "critical")

    def test_boundedness_guard_is_not_vacuous(self):
        # Guard: capped+discard=old downgrades to warning. Show it flips if
        # the cap is removed — i.e. the assertion depends on boundedness.
        capped = _stream(messages=50000, max_msgs=50000, discard="old", consumers=0)
        uncapped = _stream(messages=50000, max_msgs=-1, discard="old", consumers=0)
        self.assertNotEqual(classify(capped), classify(uncapped))
        self.assertEqual(classify(uncapped), "critical")


@unittest.skipIf(importlib.util.find_spec("nats") is None, "nats-py not installed")
class LiveBrokerSmokeTest(unittest.TestCase):
    """Integration: runs against the live broker when present, skipped when
    not. Read-only — classify() is applied to real data, nothing is mutated."""

    def test_fetch_and_classify_against_live_broker(self):
        import asyncio
        from nats import connect

        async def _reachable():
            try:
                nc = await connect(servers=[jum.DEFAULT_NATS], connect_timeout=2,
                                   max_reconnect_attempts=1)
                await nc.close()
                return True
            except Exception:
                return False

        if not asyncio.run(_reachable()):
            self.skipTest(f"broker {jum.DEFAULT_NATS} not reachable")

        streams = jum.fetch_stream_states(jum.DEFAULT_NATS)
        self.assertIsInstance(streams, list)
        for s in streams:
            v = classify(s)
            self.assertIn(v, ("ok", "warning", "critical"), s.name)
            # A stream with consumers must never be flagged, whatever its size.
            if s.consumer_count > 0:
                self.assertEqual(v, "ok", s.name)


if __name__ == "__main__":
    unittest.main()
