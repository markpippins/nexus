#!/usr/bin/env python3
"""JetStream unconsumed-stream monitor.

WHY THIS EXISTS
---------------
`WRITE_QUEUE` ran for **24 days** with a stream and *no durable consumer at all*
(incident 2026-10-10, escalation 4644f5f6, trace 13a81664). Publishers kept
publishing, JetStream kept acking, and nothing drained the stream — because the
reconciler unit died at `status=200/CHDIR` before it ever reached `ExecStart`, so
it never subscribed, so the durable consumer was never created. No alert fired
anywhere, because no such alert existed.

This is that alert. It closes the *class* of defect: a stream holding messages
that no consumer will ever receive, whatever the cause (dead unit, pruned
worktree, manual consumer deletion, another host's config).

SEVERITY — why not just "msgs > 0 and consumers == 0"
----------------------------------------------------
Probing the live broker before writing this showed why the naive predicate is
wrong: `FS_VOYAGER` holds exactly 50000 msgs (`max_msgs`) with zero consumers
and `discard=old`. It is **pinned at its cap and shedding oldest** — a bounded
rolling buffer at steady state, NOT an accumulating backlog. Paging on that
forever would train the operator to ignore the alert, which is worse than no
alert.

So two tiers, distinguished by whether the stream can still grow:

  CRITICAL  msgs > 0, no consumers, and the stream is NOT at its cap
            (max_msgs/max_bytes/max_age unbounded or not yet reached).
            This is the WRITE_QUEUE shape: unbounded accumulation that will
            never stop. Page it.
  WARNING   msgs > 0, no consumers, but the stream is pinned at a configured
            limit with discard=old — bounded and shedding, not growing.
            Report it; it usually means an orphaned/abandoned stream eating
            disk, not a live backlog.
  OK        anything else (no messages, or consumers exist).

READ-ONLY BY CONSTRUCTION
-------------------------
This tool only *reads* stream and consumer info. It never creates, updates,
deletes, or drains anything. That is deliberate: the incident that prompted it
was caused by a well-intentioned mutation, so the monitor that watches for the
failure mode must be incapable of causing one.

Exit codes are the contract (mirrors drain_write_queue_buffer.py):
  0  healthy — every stream OK, or all alerts covered by --ack
  1  alert-worthy — at least one stream needs a human
  2  could not check — broker unreachable / query failed.
     This is itself a page: an unmonitorable broker is an outage.

Run:
  python3 bin/jetstream_unconsumed_monitor.py [--nats nats://localhost:4222]
      [--ack STREAM ...] [--state-file PATH] [--min-consecutive N]
      [--json] [--list-streams]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

DEFAULT_NATS = os.environ.get("NATS_URL", "nats://localhost:4222")
DEFAULT_STATE = "/tmp/jetstream-unconsumed-monitor.state.json"
DEFAULT_MIN_CONSECUTIVE = 2

OK, ALERT, CHECK_FAILED = 0, 1, 2


class StreamState:
    """The subset of stream info the decision needs. Pure data — no client."""

    __slots__ = ("name", "messages", "bytes", "max_msgs", "max_bytes",
                 "max_age", "discard", "consumer_count", "max_pending")

    def __init__(self, name, messages, nbytes, max_msgs, max_bytes, max_age,
                 discard, consumer_count, max_pending=0):
        self.name = name
        self.messages = messages
        self.bytes = nbytes
        self.max_msgs = max_msgs          # -1 == unbounded
        self.max_bytes = max_bytes        # -1 == unbounded
        self.max_age = max_age            # 0  == unbounded (seconds)
        self.discard = discard            # "old" | "new"
        self.consumer_count = consumer_count
        self.max_pending = max_pending

    # ── the boundedness question, which is the whole design ────────────
    def at_msg_cap(self):
        """True when max_msgs is set and the stream has reached it."""
        return self.max_msgs > 0 and self.messages >= self.max_msgs

    def at_byte_cap(self):
        return self.max_bytes > 0 and self.bytes >= self.max_bytes

    def is_bounded_and_shedding(self):
        """Pinned at a configured cap with discard=old: sheds oldest, cannot grow."""
        return (self.at_msg_cap() or self.at_byte_cap()) and self.discard == "old"

    def has_messages(self):
        return self.messages > 0


def classify(s: StreamState) -> str:
    """Return 'ok' | 'warning' | 'critical'. Pure — the unit-tested core.

    Ordering matters: bounded-and-shedding must be checked BEFORE the
    accumulating branch, or FS_VOYAGER-style capped streams get misfiled as
    critical and the alert becomes noise.
    """
    if not s.has_messages():
        return "ok"
    if s.consumer_count > 0:
        return "ok"
    if s.is_bounded_and_shedding():
        return "warning"
    return "critical"


def verdict_exit_code(verdicts: dict) -> int:
    """Map stream->verdict to the process exit contract."""
    if any(v == "critical" for v in verdicts.values()):
        return ALERT
    if any(v == "warning" for v in verdicts.values()):
        return ALERT
    return OK


# ── sustained-window state ────────────────────────────────────────────
# A stream can be legitimately consumer-less for a few seconds while its unit
# restarts. Alerting on that is how monitors get muted. We require the bad
# verdict to persist for --min-consecutive checks before it is *reported*.
# The state is deliberately a small JSON file: no DB, no dependency, and a
# stale file only delays an alert, never suppresses one forever (counts only
# ever increase while the condition holds, and reset to 0 when it clears).

def load_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(path: pathlib.Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except Exception as e:  # state is an optimisation, never a hard failure
        print(f"[jetstream-unconsumed-monitor] WARN: could not persist state: {e}",
              file=sys.stderr)


def update_counts(state: dict, verdicts: dict, min_consecutive: int,
                  now: float) -> dict:
    """Return {stream: (count, confirmed)} applying the sustained window.

    Pure, so the window behaviour is unit-testable without a broker or clock.
    """
    out = {}
    for name, verdict in verdicts.items():
        if verdict == "ok":
            state[name] = 0
            out[name] = (0, False)
            continue
        prev = int(state.get(name, 0)) + 1
        state[name] = prev
        out[name] = (prev, prev >= min_consecutive)
    return out


# ── broker I/O (the only part that needs a live server) ───────────────

def fetch_stream_states(nats_url: str) -> list:
    """Read-only pull of every stream. Raises on broker failure."""
    import asyncio

    from nats import connect

    async def _go():
        nc = await connect(servers=[nats_url], connect_timeout=5,
                           max_reconnect_attempts=1)
        try:
            jsm = nc.jsm()
            infos = await jsm.streams_info()
            out = []
            for i in infos:
                c = i.config
                try:
                    cons = await jsm.consumers_info(i.config.name)
                    n_cons = len(cons)
                    max_pending = max((x.num_pending for x in cons), default=0)
                except Exception:
                    n_cons, max_pending = 0, 0
                out.append(StreamState(
                    name=i.config.name,
                    messages=i.state.messages,
                    nbytes=i.state.bytes,
                    max_msgs=getattr(c, "max_msgs", -1),
                    max_bytes=getattr(c, "max_bytes", -1),
                    max_age=getattr(c, "max_age", 0) or 0,
                    discard=str(getattr(c, "discard", "")),
                    consumer_count=n_cons,
                    max_pending=max_pending,
                ))
            return out
        finally:
            await nc.close()

    return asyncio.run(_go())


def describe(s: StreamState, verdict: str) -> str:
    cap = []
    if s.max_msgs > 0:
        cap.append(f"max_msgs={s.max_msgs}")
    if s.max_bytes > 0:
        cap.append(f"max_bytes={s.max_bytes}")
    if s.max_age > 0:
        cap.append(f"max_age={int(s.max_age)}s")
    cap_s = ",".join(cap) if cap else "UNBOUNDED"
    return (f"  [{verdict.upper():8s}] {s.name}: msgs={s.messages} "
            f"bytes={s.bytes} consumers={s.consumer_count} "
            f"discard={s.discard} limits({cap_s})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Alert when a JetStream stream holds messages no consumer will receive.")
    ap.add_argument("--nats", default=DEFAULT_NATS,
                    help=f"NATS URL (default {DEFAULT_NATS})")
    ap.add_argument("--ack", action="append", default=[], metavar="STREAM",
                    help="Acknowledge a known stream (repeatable). Acked streams "
                         "are still evaluated and printed, but do not affect the "
                         "exit code. Use for intentionally consumer-less streams.")
    ap.add_argument("--state-file", default=DEFAULT_STATE)
    ap.add_argument("--min-consecutive", type=int, default=DEFAULT_MIN_CONSECUTIVE,
                    help="Consecutive bad checks before a stream is reported "
                         f"(default {DEFAULT_MIN_CONSECUTIVE}; guards against "
                         "paging during a consumer's restart window)")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    ap.add_argument("--list-streams", action="store_true",
                    help="Print stream table and exit 0 (recon mode, no alerting)")
    args = ap.parse_args(argv)

    # ── check: broker reachable? ──────────────────────────────────────
    try:
        streams = fetch_stream_states(args.nats)
    except Exception as e:
        print(f"[jetstream-unconsumed-monitor] CHECK FAILED: cannot reach "
              f"{args.nats}: {type(e).__name__}: {e}", file=sys.stderr)
        print("  -> an unmonitorable broker is itself an outage (exit 2)",
              file=sys.stderr)
        return CHECK_FAILED

    verdicts = {s.name: classify(s) for s in streams}

    if args.list_streams:
        for s in streams:
            print(describe(s, verdicts[s.name]))
        return OK

    # ── sustained window ──────────────────────────────────────────────
    state_path = pathlib.Path(args.state_file)
    state = load_state(state_path)
    counts = update_counts(state, verdicts, args.min_consecutive, time.time())
    save_state(state_path, state)

    acked = set(args.ack)
    reportable = {n: v for n, v in verdicts.items()
                  if v != "ok" and counts[n][1] and n not in acked}
    unconfirmed = {n: v for n, v in verdicts.items()
                   if v != "ok" and not counts[n][1] and n not in acked}
    acked_bad = {n: v for n, v in verdicts.items()
                 if v != "ok" and n in acked}

    exit_code = verdict_exit_code(reportable)

    if args.json:
        print(json.dumps({
            "verdicts": verdicts,
            "counts": {k: v[0] for k, v in counts.items()},
            "confirmed": {k: v[1] for k, v in counts.items()},
            "reportable": reportable,
            "acked": acked_bad,
            "min_consecutive": args.min_consecutive,
            "exit_code": exit_code,
            "nats": args.nats,
            "ts": time.time(),
        }, indent=1))
        return exit_code

    # ── human report ──────────────────────────────────────────────────
    if exit_code == OK and not unconfirmed and not acked_bad:
        print(f"[jetstream-unconsumed-monitor] OK — {len(streams)} stream(s), "
              f"none holding unconsumed messages: "
              f"{', '.join(s.name for s in streams) or '(none)'}")
        return OK

    print("[jetstream-unconsumed-monitor] "
          + ("ALERT — a stream is holding messages no consumer will receive"
             if exit_code == ALERT else "WATCH — no sustained alert yet"))
    for s in streams:
        v = verdicts[s.name]
        if v == "ok":
            continue
        n, confirmed = counts[s.name]
        tag = ""
        if s.name in acked:
            tag = f" [ACKED — excluded from exit code]"
        elif not confirmed:
            tag = f" [not yet confirmed: {n}/{args.min_consecutive} checks]"
        print(describe(s, v) + tag)

    if unconfirmed:
        print(f"  (held for confirmation: {', '.join(sorted(unconfirmed))} — "
              f"a consumer may still be restarting)")
    if acked_bad:
        print(f"  (acked, not alerting: {', '.join(sorted(acked_bad))})")
    if exit_code == ALERT:
        print("  → a stream with messages and no consumer means nobody will ever "
              "drain it. Check the owning unit: is it running, and did its durable "
              "consumer attach? (See incident 4644f5f6.)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
