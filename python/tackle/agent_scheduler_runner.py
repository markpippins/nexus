#!/usr/bin/env python3
"""agent_scheduler_runner.py — Tackle-owned scheduler runner (T14/T15).

Implements the single evaluation loop ratified in decision `85ae61af`
(scheduler/tackle/wind, 2026-08-03): one timed tick, cron notation +
event-stream criteria. Storage conforms via V091 (cron_expr,
event_criteria columns on tackle.agent_scheduler).

Responsibilities
----------------
- ``evaluate_tick()`` — the ONE evaluator. Called from cron every minute
  (or in tests). Determines which enabled entries are due:
    * ``cron``     — 5-field cron expression matched against the window
                     since ``last_run_at`` (missed ticks fire).
    * ``interval`` — legacy seconds-based schedule (kept for
                     backward-compatible readers; normalized cron lives in
                     cron_expr).
    * ``event``    — matches unconsumed ``wind.events`` rows against
                     ``event_criteria`` JSONB; matching events are stamped
                     ``consumed_at`` (consumed events cannot re-fire).
    * ``manual``   — Run Now only; never evaluated by the tick.
- Launch construction is delegated to ``nexus_core.harness.launcher``
  (T05/T14 boundary) — no bare ``opencode`` PATH assumption, model IDs
  resolved through Tackle (``tackle.db.get_role_config`` fallback).
- Conduit ``main.py --run`` branch preserved; ``CONDUIT_DIR`` resolved
  explicitly with a safe default.
- Shadow mode (``--shadow``): evaluates + reports without launching and
  without stamping events — used for T15/T16 acceptance before the cron
  line is re-enabled.

Invocation (cron, once per minute)::

    cd /home/codex/dev/nexus/python && \\
    PYTHONPATH=/home/codex/dev/nexus/python \\
    /home/codex/opt/anaconda3/bin/python3 \\
        -m tackle.agent_scheduler_runner [--shadow]

Supersedes: python/conduit/agent_scheduler_runner.py (deprecated shim).
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2

from nexus_core.harness.enums import ExecutionMode
from nexus_core.harness.launcher import HarnessLauncher, DEFAULT_BINARIES

_log = logging.getLogger("agent-scheduler")

SLEEP_AFTER_LAUNCH = int(os.environ.get("AGENT_SCHEDULER_SLEEP", "5"))

# Task slugs that name placeholder/smoke work rather than a real queue. A row
# bound to one of these has no work source behind it: rows 164 (engineer-ii)
# and 165 (devops) fired every 30 minutes off `test-task`, 30 launches each
# with zero agent records to show for it.
PLACEHOLDER_TASK_SLUGS = frozenset({
    "test",
    "test-task",
    "placeholder",
    "placeholder-task",
    "smoke",
    "smoke-test",
})


def _setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)


def _get_dsn() -> str:
    dsn = os.environ.get("CONDUIT_PG_DSN")
    if not dsn:
        raise RuntimeError("CONDUIT_PG_DSN must be set")
    return dsn


# ── Minimal 5-field cron matcher (no external dependency) ──────────────
#
# Field grammar: `*`, `*/step`, `a-b`, `a-b/step`, `v1,v2,v3`, or a literal
# value. Fields: minute(0-59) hour(0-23) dom(1-31) month(1-12) dow(0-6,
# Sunday=0). Standard Vixie day semantics: when BOTH dom and dow are
# restricted a date matches if EITHER field matches; when one is `*` the
# other field's restriction must hold (AND).


def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    if spec == "*":
        return set(range(lo, hi + 1))
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if base == "*":
                values.update(range(lo, hi + 1, step))
            else:
                a, b = (int(x) for x in base.split("-")) if "-" in base else (int(base), hi)
                values.update(range(a, min(b, hi) + 1, step))
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
            values.update(range(a, min(b, hi) + 1))
        else:
            values.add(int(part))
    return {v for v in values if lo <= v <= hi}


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(fields)}: {expr!r}")
    minute, hour, dom, month, dow = fields
    dow_set = {0 if d == 7 else d for d in _parse_field(dow, 0, 7)}
    return (
        _parse_field(minute, 0, 59),
        _parse_field(hour, 0, 23),
        _parse_field(dom, 1, 31),
        _parse_field(month, 1, 12),
        dow_set,
    )


def _dow_ok(dow_set: set[int], dt: datetime) -> bool:
    # Normalize Python weekday (Mon=0..Sun=6) to cron dow (Sun=0..Sat=6).
    cron_dow = (dt.weekday() + 1) % 7
    return cron_dow in dow_set


def _dom_ok(dom_set: set[int], dt: datetime) -> bool:
    return dt.day in dom_set


def cron_matches_window(expr: str, since: datetime, until: datetime, max_minutes: int = 720) -> bool:
    """True if the cron would have fired at any minute in [since, until].

    Both ``since`` and ``until`` are timezone-aware; the walk is minute
    granularity. ``max_minutes`` guards against pathological windows
    (default 12h); the normal tick window is <= a few minutes.
    """
    try:
        minutes, hours, dom, months, dow = parse_cron(expr)
    except (ValueError, TypeError) as e:
        _log.error("Invalid cron_expr %r: %s", expr, e)
        return False

    # Walk minute-by-minute over the open window (since, until): a match at
    # ``since`` belongs to the PREVIOUS tick (missed-tick semantics), and a
    # match at ``until`` belongs to the NEXT tick. Round the cursor UP so the
    # window is deterministic at minute granularity.
    dom_full = dom == set(range(1, 32))
    dow_full = dow == set(range(0, 7))
    cursor = since.replace(second=0, microsecond=0)
    if cursor <= since:
        cursor += timedelta(minutes=1)
    steps = 0
    while cursor < until and steps <= max_minutes:
        if (cursor.minute in minutes and cursor.hour in hours
                and cursor.month in months):
            if dom_full and dow_full:
                day_ok = True
            elif dom_full:
                day_ok = _dow_ok(dow, cursor)
            elif dow_full:
                day_ok = _dom_ok(dom, cursor)
            else:
                day_ok = _dom_ok(dom, cursor) or _dow_ok(dow, cursor)
            if day_ok:
                return True
        cursor += timedelta(minutes=1)
        steps += 1
    return False


class Runner:
    def __init__(self):
        self._conn: psycopg2.connection | None = None

    def connect(self):
        self._conn = psycopg2.connect(_get_dsn())
        self._conn.autocommit = True

    # ── Evaluation (T15: the single evaluator) ─────────────────────────

    def get_enabled_entries(self) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT id, role, model_id, harness, agent_config,
                   schedule_type, schedule_value, cron_expr, event_criteria,
                   project_dir, last_run_at, metadata, task_slug
            FROM tackle.agent_scheduler
            WHERE enabled = 1
            ORDER BY last_run_at ASC NULLS FIRST
            """
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    def _due_cron(self, entry: dict, now: datetime) -> bool:
        expr = entry.get("cron_expr")
        if not expr:
            _log.warning("entry %d schedule_type=cron but cron_expr empty", entry["id"])
            return False
        last = entry.get("last_run_at")
        since = last if last is not None else now - timedelta(minutes=1)
        if isinstance(since, str):
            since = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return cron_matches_window(expr, since, now)

    def _due_interval(self, entry: dict, now: datetime) -> bool:
        last = entry.get("last_run_at")
        if last is None:
            return True
        if isinstance(last, str):
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
        seconds = int(entry.get("schedule_value") or 0)
        return (now - last).total_seconds() >= seconds

    def _matching_events(self, entry: dict) -> list[dict]:
        criteria = entry.get("event_criteria")
        if not criteria:
            _log.warning("entry %d schedule_type=event but event_criteria empty", entry["id"])
            return []
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except (json.JSONDecodeError, TypeError):
                criteria = {}

        event_type = criteria.get("event_type")
        where = ["consumed_at IS NULL"]
        params: list[Any] = []
        if event_type:
            where.append("event_type = %s")
            params.append(event_type)

        # Optional subject/type filter from criteria (kept generic).
        subject = criteria.get("subject")
        if subject:
            where.append("subject = %s")
            params.append(subject)

        cur = self._conn.cursor()
        cur.execute(
            f"SELECT id, event_type, subject, payload, created_at "
            f"FROM wind.events WHERE {' AND '.join(where)} ORDER BY created_at ASC LIMIT 10",
            params,
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    def _stamp_consumed(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE wind.events SET consumed_at = NOW() "
            "WHERE id = ANY(%s::uuid[]) AND consumed_at IS NULL",
            (event_ids,),
        )
        cur.close()

    # ── Launch construction (T14: harness.launcher delegation) ─────────

    def _launch_command(self, entry: dict) -> tuple[list[str], str]:
        """Build the launch command via nexus_core.harness.launcher.

        Returns (cmd, launch_cwd). Conduit branch preserved; opencode
        branch fully delegated — no bare ``opencode`` PATH assumption.
        """
        role = entry["role"]
        harness = entry.get("harness", "opencode")
        agent_config = {}
        raw = entry.get("agent_config")
        if raw:
            try:
                agent_config = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        extra_args = agent_config.get("extra_args", [])

        if harness == "conduit":
            run_title = agent_config.get("title", f"scheduled-pipeline-{role}")
            conduit_python = os.environ.get(
                "CONDUIT_PYTHON", "/home/codex/opt/anaconda3/bin/python3"
            )
            conduit_dir = os.environ.get(
                "CONDUIT_DIR",
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "conduit"),
            )
            cmd = [conduit_python, "main.py", "--run", role] + extra_args
            return cmd, conduit_dir

        # opencode (default): delegate construction to the canonical launcher.
        model_id = entry.get("model_id") or agent_config.get("model_id") or ""
        project_dir = entry.get("project_dir") or "/home/codex/dev"
        run_title = agent_config.get("title", f"scheduled-{role}")

        launcher = HarnessLauncher(
            binary=DEFAULT_BINARIES.get("opencode", "opencode"),
            capabilities={"model": True, "agent": True, "working_directory": True},
            execution_mode=ExecutionMode.INTERACTIVE,
            semantics={
                "model": {"type": "flag", "flag": "--model"},
                "agent": {"type": "flag", "flag": "--agent"},
                "working_directory": {"type": "flag", "flag": "--dir"},
            },
            execution_data={"mode": "interactive", "subcommand": "run"},
        )
        if model_id:
            launcher.set_model(model_id)
        launcher.set_agent(role)
        launcher.set_working_directory(project_dir)
        launcher.set_prompt(run_title)
        cmd = launcher.build() + extra_args
        return cmd, project_dir

    # ── Execution ──────────────────────────────────────────────────────

    def launch_agent(self, entry: dict) -> dict:
        role = entry["role"]
        try:
            cmd, launch_cwd = self._launch_command(entry)
        except Exception as e:  # noqa: BLE001 — construction must not kill the tick
            _log.error("Failed to build launch command for entry %d: %s", entry["id"], e)
            return {"status": "error", "error": str(e)}

        _log.info("Launching: %s (entry=%d role=%s)", " ".join(cmd), entry["id"], role)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=launch_cwd,
                start_new_session=True,
            )
            _log.info("Launched PID %d for entry %d (role=%s)", proc.pid, entry["id"], role)
            return {"status": "launched", "pid": proc.pid}
        except FileNotFoundError:
            _log.error("Binary not found for: %s", cmd[0])
            return {"status": "error", "error": f"{cmd[0]} not found"}
        except Exception as e:  # noqa: BLE001
            _log.error("Failed to launch agent: %s", e)
            return {"status": "error", "error": str(e)}

    def record_run(self, entry_id: int, result: dict) -> None:
        cur = self._conn.cursor()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = result.get("status", "unknown")
        pid = result.get("pid")
        meta = json.dumps({"last_pid": pid, "last_status": status})
        cur.execute(
            """UPDATE tackle.agent_scheduler
               SET last_run_at = %s, last_run_status = %s,
                   metadata = %s, updated_at = %s
               WHERE id = %s""",
            (now, status, meta, now, entry_id),
        )
        cur.close()

    def _is_interactive_hosted(self, role: str) -> bool:
        """Check whether a role is INTERACTIVE-hosted (runs inside Freebuff).

        Roles whose active config_bundle resolves to invocation_mode=
        'INTERACTIVE' (harness harn-freebuff) are executed by the Freebuff
        interactive session — the scheduler must NEVER launch them via
        opencode/ollama. This mirrors the harness-srv /run guard.
        """
        try:
            cur = self._conn.cursor()
            cur.execute(
                """SELECT invocation_mode FROM tackle.config_bundle
                   WHERE role = %s AND is_active = 1
                   ORDER BY priority ASC LIMIT 1""",
                (role,),
            )
            row = cur.fetchone()
            cur.close()
            return bool(row and row[0] == "INTERACTIVE")
        except Exception as e:
            # Fail CLOSED: if we can't confirm the role is launchable, we
            # must not launch it. A skipped tick is benign; wrongly spawning
            # a Freebuff-resident role defeats the entire guard.
            _log.warning(
                "interactive-hosted check failed for role=%s: %s — assuming interactive-hosted (refusing launch)",
                role, e,
            )
            return True

    def _has_eligible_work(self, role: str, entry: dict | None = None) -> bool:
        """Check whether a role (and its scheduled entry) has eligible work.

        This is the 1285 remediation: the runaway-reviewer incident
        (e6d854da) happened because the scheduler launched reviewer with
        0 eligible plans — the agent burned CPU generating nothing.

        Checks:
          - placeholder task_slug on the entry → never eligible, whatever the
            role. Rows 164/165 (engineer-ii, devops) launched ~30×/hour off
            `test-task` with no queue behind them.
          - builder  → READY execution.requests count > 0
          - reviewer → open reviewer tickets in vision.tickets > 0
          - other    → assume eligible (conservative fallback)

        engineer-ii / devops have no queue check on purpose: neither role has
        ever held a vision.tickets row (only builder, reviewer, planner and
        critic do), conduit only ever bootstraps `builder` tickets, wind.titles
        has no title for them, and their work arrives as interactive prompts
        rather than as queued rows. The placeholder guard is the whole gate
        for them — inventing a queue here would be a guess, not a check.
        """
        slug = ((entry or {}).get("task_slug") or "").strip().lower()
        if slug in PLACEHOLDER_TASK_SLUGS:
            _log.warning(
                "skip (role=%s, task_slug=%r) — placeholder task binding, not a work source",
                role, slug,
            )
            return False

        try:
            cur = self._conn.cursor()
            if role == "builder":
                cur.execute(
                    "SELECT COUNT(*) AS n FROM execution.requests WHERE status = 'READY'"
                )
                return cur.fetchone()[0] > 0
            if role == "reviewer":
                cur.execute(
                    "SELECT COUNT(*) AS n FROM vision.tickets WHERE role = 'reviewer' AND status = 'open'"
                )
                return cur.fetchone()[0] > 0
            # Conservative: roles without a specific check are assumed to have work
            return True
        except Exception as e:
            _log.warning("eligibility check failed for role=%s: %s — assuming eligible", role, e)
            return True  # fail open — don't block the scheduler on a query error

    def evaluate_tick(self, *, shadow: bool = False) -> dict:
        """The single evaluation tick. Returns a summary dict.

        In shadow mode: reports what WOULD launch / be consumed without
        launching or stamping (used for T15/T16 acceptance).
        """
        now = datetime.now(timezone.utc)
        summary: dict[str, Any] = {
            "evaluated": 0, "due": [], "events_consumed": 0, "launched": 0,
            "errors": 0, "skipped_empty": 0, "skipped_interactive": 0,
        }
        for entry in self.get_enabled_entries():
            summary["evaluated"] += 1
            entry_id = entry["id"]
            role = entry.get("role", "?")
            stype = entry.get("schedule_type", "interval")

            events: list[dict] = []
            due = False
            if stype == "cron":
                due = self._due_cron(entry, now)
            elif stype == "event":
                events = self._matching_events(entry)
                due = bool(events)
            elif stype == "interval":
                due = self._due_interval(entry, now)
            # 'manual' is never evaluated by the tick (Run Now only).

            if not due:
                continue

            item = {"entry_id": entry_id, "role": role, "schedule_type": stype}
            if events:
                item["event_ids"] = [str(e["id"]) for e in events]

            # ── Interactive-hosted guard (Freebuff roles) ────────────
            # Roles running inside Freebuff (config_bundle invocation_mode
            # = INTERACTIVE, harn-freebuff) are never launched by the
            # scheduler — the interactive session IS their executor.
            if self._is_interactive_hosted(role):
                _log.info("skip (role=%s, interactive-hosted) — runs inside Freebuff, not launchable", role)
                summary["skipped_interactive"] = summary.get("skipped_interactive", 0) + 1
                continue

            # ── Emptiness check (1285 remediation slice 1) ──────────
            # Before launching, verify the role has eligible work.
            # This prevents the runaway-reviewer incident: reviewer
            # launched with 0 plans and burned CPU generating nothing.
            # Runs before both shadow and real paths so it's visible in shadow mode.
            if not self._has_eligible_work(role, entry):
                _log.info("skip (role=%s, eligible=0) — no work to do", role)
                summary["skipped_empty"] = summary.get("skipped_empty", 0) + 1
                continue

            if shadow:
                _log.info("[shadow] would launch entry %d (%s, %s), events=%d",
                          entry_id, role, stype, len(events))
                summary["due"].append(item)
                continue

            # Stamp consumed events BEFORE launching so a crash mid-tick
            # cannot re-fire the same event (idempotent dispatch).
            if events:
                self._stamp_consumed([str(e["id"]) for e in events])
                summary["events_consumed"] += len(events)

            result = self.launch_agent(entry)
            self.record_run(entry_id, result)
            if result.get("status") == "launched":
                summary["launched"] += 1
            else:
                summary["errors"] += 1
            summary["due"].append({**item, "result": result.get("status")})

            time.sleep(SLEEP_AFTER_LAUNCH)

        return summary

    def close(self):
        if self._conn:
            self._conn.close()


def main():
    parser = argparse.ArgumentParser(description="Tackle agent scheduler runner (single tick)")
    parser.add_argument("--shadow", action="store_true",
                        help="Evaluate only — do not launch or consume events (T15/T16 acceptance)")
    args = parser.parse_args()

    _setup_logging()
    _log.info("Agent scheduler runner starting (shadow=%s)", args.shadow)
    runner = Runner()
    try:
        runner.connect()
        summary = runner.evaluate_tick(shadow=args.shadow)
    finally:
        runner.close()
    _log.info("Tick complete — evaluated=%d launched=%d events_consumed=%d "
              "skipped_empty=%d skipped_interactive=%d errors=%d due=%s",
              summary["evaluated"], summary["launched"],
              summary["events_consumed"],
              summary.get("skipped_empty", 0), summary.get("skipped_interactive", 0),
              summary["errors"],
              [d["entry_id"] for d in summary["due"]])


if __name__ == "__main__":
    main()
