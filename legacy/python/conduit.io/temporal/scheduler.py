"""Temporal Scheduler — replaces main.py's cron-driven dispatch loop.

Scans for eligible plans and starts PlanExecutionWorkflows on the
appropriate Temporal task queues.  Lightweight: no Activities or
Workflows registered — just a Temporal Client that starts workflows.

The heavy lifting (retry, fallback, receipts, ticket lifecycle) lives
inside PlanExecutionWorkflow, executed by the Temporal Worker.

Usage:
    python -m conduit.temporal.scheduler          # All roles, 30s interval
    python -m conduit.temporal.scheduler --interval 15  # 15s interval
    python -m conduit.temporal.scheduler --role builder  # Single role
    python -m conduit.temporal.scheduler --once          # One pass, exit

Requires a running Temporal server and the Conduit schema in PostgreSQL
(configured via CONDUIT_PG_SCHEMA, defaults to 'conduit').
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from temporalio.client import Client

# ── Path setup ──────────────────────────────────────────────────────

_PARENT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PARENT))

from env_config import load_env  # noqa: F401 — fires at import time
from db_adapter import DBAdapter

# ── Config ──────────────────────────────────────────────────────────

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "conduit")

# Role dispatch order (reviewer first so it catches completed builders
# from a previous cycle; planner+builder back-to-back so planner-created
# builder tickets dispatch on the same cycle; critic last).
DEFAULT_ROLE_ORDER = ["reviewer", "planner", "builder", "critic"]

SCHEDULER_INTERVAL = int(os.environ.get("SCHEDULER_INTERVAL", "30"))
SCHEDULER_IDLE_BACKOFF = int(os.environ.get("SCHEDULER_IDLE_BACKOFF", "60"))
SCHEDULER_LOG_FORMAT = os.environ.get(
    "SCHEDULER_LOG_FORMAT",
    "%(asctime)s [%(levelname)s] %(message)s",
)

# ── Logging ─────────────────────────────────────────────────────────

_log = logging.getLogger("conduit.scheduler")


def _setup_logging() -> None:
    level_name = os.environ.get("CONDUIT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    _log.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        SCHEDULER_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _log.handlers.clear()
    _log.addHandler(handler)


# ── Scheduler ───────────────────────────────────────────────────────


class Scheduler:
    """Scans eligible plans and starts PlanExecutionWorkflows."""

    def __init__(
        self,
        temporal_address: str = TEMPORAL_ADDRESS,
        temporal_namespace: str = TEMPORAL_NAMESPACE,
        roles: Optional[List[str]] = None,
        interval: int = SCHEDULER_INTERVAL,
        idle_backoff: int = SCHEDULER_IDLE_BACKOFF,
        once: bool = False,
    ):
        self._address = temporal_address
        self._namespace = temporal_namespace
        self._roles = roles or DEFAULT_ROLE_ORDER
        self._interval = interval
        self._idle_backoff = idle_backoff
        self._once = once
        self._next_sleep = interval  # starts at active interval
        self._client: Optional[Client] = None
        self._db: Optional[DBAdapter] = None
        self._running = False
        self._wake_event = asyncio.Event()

        # Cycle stats
        self._cycle_count = 0
        self._total_dispatched = 0
        self._started_at: Optional[datetime] = None

        # Last time we checked for scheduler wake signal (for idle-backoff reduction)
        self._last_wake_check: str = datetime.now(timezone.utc).isoformat() + "Z"

    async def start(self) -> None:
        """Connect to Temporal and begin the dispatch loop."""
        _log.info("Scheduler starting — connecting to Temporal at %s (ns=%s)",
                  self._address, self._namespace)

        self._client = await Client.connect(
            self._address,
            namespace=self._namespace,
        )
        self._db = DBAdapter()
        self._running = True
        self._started_at = datetime.now(timezone.utc)

        _log.info("Scheduler connected — roles=%s interval=%ds idle_backoff=%ds once=%s",
                  self._roles, self._interval, self._idle_backoff, self._once)
        print(f"Scheduler: polling {self._roles} every {self._interval}s "
              f"(idle backoff: {self._idle_backoff}s)")

        try:
            if self._once:
                await self._dispatch_cycle()
            else:
                while self._running:
                    await self._dispatch_cycle()
                    # Check if scheduler wake was requested (config change, etc.)
                    # before committing to a long idle backoff.
                    if self._db.consume_scheduler_wake(self._last_wake_check):
                        _log.info("Scheduler wake consumed — reducing backoff to active interval")
                        self._next_sleep = self._interval
                    self._last_wake_check = datetime.now(timezone.utc).isoformat() + "Z"
                    # Use wake_event for immediate shutdown instead of
                    # plain asyncio.sleep() which blocks up to interval.
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(),
                            timeout=self._next_sleep,
                        )
                    except asyncio.TimeoutError:
                        pass  # Normal interval elapsed
                    self._wake_event.clear()
        except asyncio.CancelledError:
            _log.info("Scheduler cancelled — shutting down")
        finally:
            self._running = False
            _log.info("Scheduler stopped — cycles=%d dispatched=%d",
                      self._cycle_count, self._total_dispatched)

    def stop(self) -> None:
        """Request graceful shutdown (wakes immediately)."""
        self._running = False
        self._wake_event.set()

    # ── Dispatch cycle ──────────────────────────────────────────

    async def _dispatch_cycle(self) -> None:
        """One full scan-and-dispatch cycle across all roles."""
        self._cycle_count += 1
        cycle_start = datetime.now(timezone.utc)
        _log.debug("Cycle %d START", self._cycle_count)
        self._next_sleep = self._interval  # reset each cycle

        # Guards — skip the whole cycle if paused
        if self._db.is_conduit_paused():
            _log.info("Cycle %d — conduit is paused, skipping", self._cycle_count)
            print(f"[{_now_short()}] Cycle {self._cycle_count}: paused — skipping")
            return

        # Ticket lifecycle maintenance (stale/expired detection)
        stale = self._db.detect_stale_tickets()
        expired = self._db.detect_expired_tickets()
        if stale or expired:
            _log.info("Cycle %d — ticket lifecycle: %d stale, %d expired",
                      self._cycle_count, stale, expired)

        # ── Dispatch per role ─────────────────────────────────
        total_this_cycle = 0
        for role in self._roles:
            dispatched = await self._dispatch_role(role)
            total_this_cycle += dispatched

        cycle_elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        self._total_dispatched += total_this_cycle

        if total_this_cycle > 0:
            _log.info("Cycle %d END — dispatched %d workflows in %.1fs (total: %d)",
                      self._cycle_count, total_this_cycle, cycle_elapsed,
                      self._total_dispatched)
            print(f"[{_now_short()}] Cycle {self._cycle_count}: "
                  f"dispatched {total_this_cycle} workflow(s) in {cycle_elapsed:.1f}s "
                  f"(total: {self._total_dispatched})")
            # Reset sleep to regular interval after active cycle
            self._next_sleep = self._interval
        else:
            _log.debug("Cycle %d END — no eligible plans", self._cycle_count)
            # Use longer idle backoff when nothing was dispatched
            self._next_sleep = self._idle_backoff

    async def _dispatch_role(self, role: str) -> int:
        """Scan eligible plans for one role and start workflows."""
        if self._db.is_role_circuit_breaker_tripped(role):
            _log.info("Cycle %d — role=%s circuit breaker tripped, skipping", self._cycle_count, role)
            return 0
        try:
            plans = self._db.get_eligible_plans(role)
        except Exception as exc:
            _log.error("_dispatch_role: failed to query eligible plans for role=%s: %s",
                       role, exc)
            return 0

        if not plans:
            return 0

        _log.info("_dispatch_role: role=%s eligible=%d", role, len(plans))
        dispatched = 0

        for plan in plans:
            plan_id = plan.get("id") or plan.get("plan_id", "")
            if not plan_id:
                _log.warning("_dispatch_role: plan missing id, skipping")
                continue

            title = (plan.get("title") or plan.get("goal") or "")[:80]

            try:
                wf_id = f"plan-{plan_id}-{role}"
                handle = await self._client.start_workflow(
                    "PlanExecutionWorkflow",
                    args=[plan_id, role, False],
                    id=wf_id,
                    task_queue=role,
                )
                dispatched += 1
                _log.info(
                    "_dispatch_role: started workflow role=%s plan=%s "
                    "wf_id=%s title=%s",
                    role, plan_id, wf_id, title,
                )
                print(f"  ✓ started {wf_id} ({title})")
            except Exception as exc:
                # WorkflowAlreadyStartedError is expected for idempotent
                # dispatch — a previous cycle already started this plan.
                err_text = str(exc)
                if "already" in err_text.lower() and "started" in err_text.lower():
                    _log.debug(
                        "_dispatch_role: workflow already started plan=%s role=%s",
                        plan_id, role,
                    )
                else:
                    _log.error(
                        "_dispatch_role: failed to start workflow plan=%s role=%s: %s",
                        plan_id, role, exc,
                    )
                    print(f"  ✗ FAILED {wf_id}: {err_text[:120]}")

        return dispatched


# ── Helpers ─────────────────────────────────────────────────────────

def _now_short() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ── Entry point ─────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Temporal Scheduler — scans plans and starts workflows"
    )
    parser.add_argument(
        "--interval", type=int, default=SCHEDULER_INTERVAL,
        help=f"Seconds between dispatch cycles (default: {SCHEDULER_INTERVAL})",
    )
    parser.add_argument(
        "--idle-backoff", type=int, default=SCHEDULER_IDLE_BACKOFF,
        help=f"Seconds to sleep when no plans found (default: {SCHEDULER_IDLE_BACKOFF})",
    )
    parser.add_argument(
        "--role", choices=DEFAULT_ROLE_ORDER,
        help="Dispatch only a single role",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one dispatch cycle and exit",
    )
    parser.add_argument(
        "--temporal-address", default=TEMPORAL_ADDRESS,
        help=f"Temporal server address (default: {TEMPORAL_ADDRESS})",
    )
    parser.add_argument(
        "--temporal-namespace", default=TEMPORAL_NAMESPACE,
        help=f"Temporal namespace (default: {TEMPORAL_NAMESPACE})",
    )
    args = parser.parse_args()

    _setup_logging()

    roles = [args.role] if args.role else DEFAULT_ROLE_ORDER

    scheduler = Scheduler(
        temporal_address=args.temporal_address,
        temporal_namespace=args.temporal_namespace,
        roles=roles,
        interval=args.interval,
        idle_backoff=args.idle_backoff,
        once=args.once,
    )

    # Install signal handlers for graceful shutdown (immediate wake)
    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, scheduler.stop)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    await scheduler.start()


if __name__ == "__main__":
    asyncio.run(main())
