#!/usr/bin/env python3
"""Thin wrapper: run the pg-logging integrity check and file records on
the pg-logging series.

Used by pg-logging-check.service (nightly user timer, 06:40 UTC slot —
after the record-durability sweep's 06:30 slot). Contract — identical
to sdk_drift_stamp_wrap / users_bcrypt_drift_wrap /
record_durability_wrap (green-heartbeat contract, PRs #527/#540/#542):

  - GREEN runs (both legs verified) are recorded on a heartbeat
    cadence: commissioning on first run ever, weekly heartbeat on the
    first clean run of an ISO week, recovery on the first clean run
    after findings.
  - FINDINGS (checker exit 1: missing file, unattributed DDL, stale
    log, live probe not captured) file an inspection record, at most
    one per UTC day (suppression state).
  - Tool errors (checker exit 2: ssh failure, unreadable logs, probe
    connection failure) file nothing and exit 2 — journal-visible.

Records are filed as DBA on the series:pg-logging tag.
Exit codes: 0 = ok or record filed, 2 = tool/environment error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "check_pg_logging.py"
STATE = Path(os.environ.get(
    "PG_LOGGING_STATE",
    "/home/codex/.cache/pg-logging-check/state.json"))

GREEN_TAGS = ('["type:report","db-a","pg-logging","pg-backup",'
              '"series:pg-logging"]')
DRIFT_TAGS = ('["type:inspection","db-a","pg-logging","pg-backup",'
              '"series:pg-logging"]')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision — shared contract with sdk_drift_stamp_wrap,
    users_bcrypt_drift_wrap, and record_durability_wrap. Keep in
    lockstep."""
    new_state = dict(state)
    if not state.get("ever_green"):
        action = "recovery" if state.get("last_drift_recorded") else "commissioning"
        new_state.pop("last_drift_recorded", None)
        new_state["ever_green"] = True
        new_state["last_green_recorded"] = week
        return action, new_state
    if state.get("last_green_recorded") != week:
        return "heartbeat", {**new_state, "last_green_recorded": week}
    return None, new_state


def _file_record(kind: str, when: str, extra: str = "") -> bool:
    titles = {
        "commissioning": f"PG logging posture GREEN (commissioning run {when}) — both R9 legs attributed",
        "heartbeat": f"PG logging posture GREEN (weekly heartbeat {when})",
        "recovery": f"PG logging posture RECOVERED (green after findings {when})",
    }
    intros = {
        "commissioning": "First run of pg-logging-check.timer: yesterday's pglogs "
                         "file verified on BOTH R9 legs (titanium local, vanadium "
                         "ssh) — present, containing DDL lines with full "
                         "attribution prefixes, freshly written, and a live "
                         "attribution probe captured in today's log. "
                         "Green-heartbeat contract: commissioning on first run, "
                         "weekly heartbeat thereafter, daily inspection records "
                         "while findings exist.",
        "heartbeat": "Weekly green heartbeat from pg-logging-check.timer: both "
                     "R9 PG legs (titanium, vanadium) still write DDL-attributed "
                     "durable logs.",
        "recovery": "The previously-reported pg-logging findings are resolved: "
                    "first green run after the inspection record. Weekly "
                    "heartbeat cadence resumes.",
    }
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", titles[kind],
         "--record-type", "report",
         "--level", "1",
         "--tags", GREEN_TAGS,
         "-c", intros[kind] + (extra or "")],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print(f"{kind} record filed:", ok)
    return ok


def _file_findings_record(when: str, payload: dict) -> bool:
    legs = []
    for r in payload.get("results", []):
        line = f"- **{r['leg']}** ({r.get('file', '?')}): "
        if r.get("tool_error"):
            line += f"TOOL ERROR — {r['tool_error']}"
        elif r.get("findings"):
            line += "; ".join(r["findings"])
        else:
            line += (f"verified ({r.get('ddl_lines', 0)} DDL lines, "
                     f"{r.get('ddl_with_attribution', 0)} attributed)")
        legs.append(line)
    body = (
        f"The nightly pg-logging integrity check ({when}, day "
        f"{payload.get('day')}) found problems in the R9 legs' durable "
        "DDL-attribution logging:\n\n" + "\n".join(legs)
        + "\n\nThis breaks the DDL-attribution guarantee that the "
        "public.users out-of-band reshape incident (2026-09-23) showed the "
        "need for. Investigate the affected leg: check container state "
        "(docker ps), ALTER SYSTEM settings (logging_collector, "
        "log_statement, log_line_prefix), and the compose volume mounts. "
        "This record repeats daily until the next clean run files a "
        "recovery record."
    )
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", f"PG logging integrity findings (nightly check, {when})",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", DRIFT_TAGS,
         "-c", body],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print("findings record filed:", ok)
    return ok


def main() -> int:
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    # First run ever is baseline: yesterday's file predates deployment,
    # so posture is verified against today's log and the run is
    # acknowledged in the commissioning record instead of alerting.
    baseline = not state.get("baseline_done")
    res = subprocess.run(
        [sys.executable, str(CHECK), *(("--baseline",) if baseline else ())],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO),
    )
    out = (res.stdout or "").strip()
    if out:
        print(out)
    if res.returncode == 2:
        print(f"tool error (checker exit 2): {(res.stderr or out)[-400:]}",
              file=sys.stderr)
        return 2
    if res.returncode not in (0, 1):
        print(f"tool error (unexpected checker exit {res.returncode})",
              file=sys.stderr)
        return 2
    try:
        payload = json.loads(out)
    except Exception:
        print(f"tool error (unparseable checker output): {out[-200:]}",
              file=sys.stderr)
        return 2
    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    week = _iso_week(now)

    if res.returncode == 0:
        action, new_state = decide_green_action(state, week)
        ok = True
        if action is not None:
            summary = (f"\n\nPer-leg: "
                       + "; ".join(
                           f"{r['leg']}: {r.get('ddl_lines', 0)} DDL lines, "
                           f"{r.get('ddl_with_attribution', 0)} attributed, "
                           f"live probe captured={r.get('live_probe_captured')}"
                           for r in payload.get("results", [])))
            if baseline:
                notes = [n for r in payload.get("results", [])
                         for n in r.get("notes", [])]
                today_bits = "; ".join(
                    f"{r['leg']} today: {r.get('today_ddl_lines', 0)} DDL lines, "
                    f"{r.get('today_ddl_with_attribution', 0)} attributed"
                    for r in payload.get("results", []))
                summary += ("\n\nBaseline run (" + payload.get("day", "") +
                            "): " + (" ".join(notes) if notes else "") +
                            " " + today_bits + ".")
            ok = _file_record(action,
                              week if action == "heartbeat" else today,
                              summary)
        new_state = {**state, **new_state, "baseline_done": True,
                     "output": out[-2000:]}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0 if ok else 2

    # Findings — daily-suppressed inspection record. The baseline
    # pre-deployment exemption applies here too: a first run that finds
    # real problems is a real alert, and it consumes the baseline.
    if state.get("last_drift_recorded") == today:
        print(f"findings already recorded today ({today}) — suppressing "
              "duplicate record")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({**state, "baseline_done": True}))
        return 0

    ok = _file_findings_record(today, payload)
    new_state = {**state,
                 "last_drift_recorded": today,
                 "ever_green": False,
                 "baseline_done": True,
                 "output": out[-2000:]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(new_state))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
