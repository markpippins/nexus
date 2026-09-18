#!/usr/bin/env python3
"""calendar-emit — local CalendarEvent emitter (Q3 slice, design a330914e).

The machine's existing rhythm — systemd user timers, boot-shim session
events — writes contract-shaped events (PR #331 vocabulary: CalendarEvent,
kind epistemics, deterministic ids per Q2) to a local JSONL accumulation
file. The file IS the local calendar. No DB writes anywhere: consolidation
into the primary store is a later slice gated on roundtable Q1/Q2.

Modes:
  emit         append one CalendarEvent, deduped by eventId
  observe      reserved intake for kind=observed (post-consolidation, Q1/Q2)
  scan-timers  inventory user timers: emitting vs would-emit
  install      write calendar-emit@.service + per-timer OnSuccess drop-ins
               (print-only by default; --write performs)

Systemd wiring: the template unit calendar-emit@.service runs the emitter
with %i (= the triggering timer unit name); each timer's service gains a
drop-in with OnSuccess=calendar-emit@<timer>.service. Emission only fires
on SUCCESS — failed runs are visible in the journal, and the operator can
later decide whether failures deserve their own kind.

Window semantics: for timer emission, window.start is the timer's actual
LastTriggerUSec elapse instant (the occurrence), resolved via systemctl —
not the emit instant. Fallback (no resolvable elapse): second-truncated
UTC now. Event identity (Q2): uuid5 over machine|emitter|window_start, so
one occurrence = one id regardless of which witness recorded it (within
the same second) and re-writes dedupe.

Kind epistemics (V174 discipline applied to time): scheduled/occurred/
observed are all facts; none is an error state. Outcomes ride in payload,
never in the event's existence.

Never raises in emit paths: an emitter that fails its host (boot shim,
OnSuccess chain) would poison the very rhythm it observes — failures are
recorded as data, not exceptions.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Deterministic UUID namespace for Q2 event identity. Fixed value so every
# machine derives identical ids for identical (machine, emitter, window).
EVENT_NS = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")

DEFAULT_STATE_DIR = Path(os.environ.get(
    "CALENDAR_STATE_DIR",
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
)) / "nexus-calendar"

WIRE_KINDS = ("scheduled", "occurred", "observed")


# ── primitives ───────────────────────────────────────────────────────────

def event_id(machine: str, emitter: str, window_start: str) -> str:
    """Deterministic event id (Q2): machine|emitter|window_start -> uuid5."""
    return str(uuid.uuid5(EVENT_NS, f"{machine}|{emitter}|{window_start}"))


def now_iso() -> str:
    """Second-truncated UTC now — two witnesses in the same second share an id."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hostname() -> str:
    return socket.gethostname()


# ── accumulation ─────────────────────────────────────────────────────────

def append_event(path: Path, event: dict, max_bytes: int = 16 * 1024 * 1024) -> tuple[str, str]:
    """Append one event to the JSONL calendar, deduped by eventId.

    Returns (status, detail): status in {appended, duplicate, error}.
    Never raises. Rotation: at max_bytes the file is renamed with a
    timestamp suffix and a fresh file starts — append-only history.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        eid = event.get("eventId") or ""
        if eid and path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        if json.loads(line).get("eventId") == eid:
                            return ("duplicate", f"eventId {eid} already present")
                    except Exception:
                        continue  # torn tail line is data, not a blocker
        if path.exists() and path.stat().st_size > max_bytes:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path.rename(path.with_name(f"{path.name}.{stamp}"))
        event.setdefault("provenance", {})
        event["provenance"]["recordedAt"] = now_iso()
        with open(path, "a", encoding="utf-8") as f:
            # A torn tail without a trailing newline would glue the new JSON
            # onto the partial line and corrupt the JSONL — terminate it first.
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as fb:
                    fb.seek(-1, os.SEEK_END)
                    if fb.read(1) != b"\n":
                        f.write("\n")
            f.write(json.dumps(event, sort_keys=True) + "\n")
        return ("appended", f"{event.get('kind')}:{event.get('source', {}).get('emitter')} -> {path.name}")
    except Exception as e:  # emitter must never fail its host
        return ("error", f"{type(e).__name__}: {e}")


def load_calendar(path: Path) -> list[dict]:
    """Read the JSONL calendar; torn lines are skipped as data, not errors."""
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def make_event(kind: str, emitter: str, title: str, window_start: str,
               window_end: str | None = None, participants: list | None = None,
               session_ref: str | None = None, payload: dict | None = None,
               machine: str | None = None) -> dict:
    """Build a contract-shaped CalendarEvent (PR #331 vocabulary).

    participants are wire-shaped AgentRefs: {"role"?, "model"?, ...} —
    the contract encodes modelId with @encodedName("application/json",
    "model"), so the JSON key is `model`.
    """
    if kind not in WIRE_KINDS:
        raise ValueError(f"kind must be one of {WIRE_KINDS}: got {kind!r}")
    m = machine or hostname()
    return {
        "eventId": event_id(m, emitter, window_start),
        "calendarId": event_id(m, "local-calendar", "calendar-root"),
        "kind": kind,
        "source": {"machine": m, "emitter": emitter},
        "title": title,
        "window": {"start": window_start, "end": window_end},
        "participants": participants or [],
        "sessionRef": session_ref,
        "payload": payload or {},
    }


# ── systemd inventory ────────────────────────────────────────────────────

def iter_timer_units(unit_dir: Path) -> list[Path]:
    """Timer unit FILES the install manages (deterministic enumeration)."""
    if not unit_dir.exists():
        return []
    return sorted(unit_dir.glob("*.timer"))


def list_user_timers() -> list[dict]:
    """Live timer inventory via systemctl for scan-timers display.
    Absent systemctl / unsupported JSON = empty list — absence as data."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "list-timers", "--all", "--no-pager",
             "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return []
        rows = json.loads(r.stdout)
        return [t for t in rows if str(t.get("unit", "")).endswith(".timer")]
    except FileNotFoundError:
        return []
    except Exception:
        return []


def timer_last_trigger(timer_unit: str) -> str | None:
    """Resolve a timer's last elapse instant as second-truncated UTC ISO.

    Text value via `systemctl show`; parsed with GNU `date -u -d`, which
    resolves tz abbreviations (EDT/EST) correctly. strptime with %Z is the
    fallback and is DANGEROUS alone: it silently treats unknown abbreviations
    as UTC (observed 4h skew) — only used as a last resort when `date` is
    absent, with the local-tz interpretation corrected via astimezone when
    the offset parsed. Never-triggered (0 / n/a) -> None.
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", timer_unit,
             "-p", "LastTriggerUSec", "--value"],
            capture_output=True, text=True, timeout=10,
        )
        v = (r.stdout or "").strip()
        if not v or v in ("n/a", "0"):
            return None
        # Primary: GNU date resolves EDT/EST/etc correctly.
        r2 = subprocess.run(
            ["date", "-u", "-d", v, "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True, text=True, timeout=10,
        )
        if r2.returncode == 0 and r2.stdout.strip():
            return r2.stdout.strip()
        # Fallback: strptime (tz-abbreviation-unsafe; better than nothing).
        for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S %z"):
            try:
                dt = datetime.strptime(v, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
        return None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def resolve_window_start(window_start: str | None, from_unit: str | None) -> str:
    """Explicit --window-start > timer elapse instant > second-truncated now."""
    if window_start:
        return window_start
    if from_unit:
        resolved = timer_last_trigger(from_unit)
        if resolved:
            return resolved
    return now_iso()


def emitting_dropin_path(unit_dir: Path, timer_name: str) -> Path:
    """The OnSuccess drop-in on the timer's service unit."""
    service = timer_name[:-len(".timer")] + ".service"
    return unit_dir / f"{service}.d" / "calendar-emit-on-success.conf"


def timer_has_calendar_emission(unit_dir: Path, timer_name: str) -> bool:
    """True if this timer's service already carries the OnSuccess drop-in."""
    return emitting_dropin_path(unit_dir, timer_name).exists()


# ── modes ────────────────────────────────────────────────────────────────

def cmd_emit(args) -> int:
    try:
        wstart = resolve_window_start(args.window_start, args.from_unit)
        ev = make_event(
            kind=args.kind, emitter=args.emitter, title=args.title,
            window_start=wstart, window_end=args.window_end,
            participants=[{"role": args.role}] if args.role else [],
            session_ref=args.session_ref,
            payload=json.loads(args.payload) if args.payload else {},
        )
    except Exception as e:
        print(f"emit refused: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(ev, indent=2, sort_keys=True))
        return 0
    status, detail = append_event(Path(args.state_dir) / "calendar.jsonl", ev)
    print(f"[{status}] {detail}")
    return 0 if status in ("appended", "duplicate") else 1


def cmd_observe(args) -> int:
    """v0 observe: honest no-op — no consolidation source exists yet (Q1/Q2).
    Reserved so the CLI surface is stable when consolidation lands."""
    print("observe: nothing to observe yet — no consolidation source exists "
          "(waits on Q1/Q2); absence recorded as data, not an error")
    return 0


def cmd_scan_timers(args) -> int:
    """Report which user timers emit already and which would after install."""
    unit_dir = Path(args.unit_dir)
    unit_files = iter_timer_units(unit_dir)
    live = {t.get("unit") for t in list_user_timers()}
    if not unit_files:
        print("no timer units found in unit dir (absence as data)")
        return 0
    done = would = 0
    for u in unit_files:
        name = u.name
        has = timer_has_calendar_emission(unit_dir, name)
        live_mark = "" if name in live or not live else " (not loaded)"
        done += 1 if has else 0
        would += 0 if has else 1
        print(f"{'[emit]' if has else '[would]'} {name}{live_mark}")
    print(f"\n{len(unit_files)} timer units: {done} emit, {would} would-emit after install")
    return 0


def cmd_install(args) -> int:
    """Write the template unit + per-timer OnSuccess drop-ins.

    Two file kinds: calendar-emit@.service (template; ExecStart uses %i)
    and <service>.d/calendar-emit-on-success.conf with the literal
    OnSuccess=calendar-emit@<timer>.service line. Print-only by default;
    --write performs. Idempotent: existing drop-ins are skipped.
    """
    unit_dir = Path(args.unit_dir)
    script = args.script or str(Path(__file__).resolve())
    state_dir = args.state_dir or str(DEFAULT_STATE_DIR)
    timers = [p.name for p in iter_timer_units(unit_dir)]
    actions: list[tuple[str, Path, str | None]] = []
    tpl = unit_dir / "calendar-emit@.service"
    if not tpl.exists() or args.write:
        actions.append(("write", tpl,
                        TEMPLATE_UNIT.format(script=script, state_dir=state_dir)))
    for tname in timers:
        if timer_has_calendar_emission(unit_dir, tname):
            continue
        drop = emitting_dropin_path(unit_dir, tname)
        actions.append(("mkdir", drop.parent, None))
        actions.append(("write", drop,
                        DROPIN_ONSUCCESS.format(timer=tname)))
    if not args.write:
        for kind, path, _c in actions:
            print(f"[plan] {'mkdir ' if kind == 'mkdir' else 'write '} {path}")
        n_timers = sum(1 for t in timers if not timer_has_calendar_emission(unit_dir, t))
        print(f"\n{n_timers} timers would gain emission; {len(actions)} filesystem "
              f"actions. Re-run with --write to perform. Then: "
              f"systemctl --user daemon-reload")
        return 0
    count = 0
    for kind, path, content in actions:
        try:
            if kind == "mkdir":
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            count += 1
        except Exception as e:
            print(f"[error] {path}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"[done] {count}/{len(actions)} actions written. Now: "
          f"systemctl --user daemon-reload")
    return 0


TEMPLATE_UNIT = """\
[Unit]
Description=CalendarEvent emission for %i (calendar-emit, design a330914e)
# OnSuccess-chained from timer services via their drop-ins: records each
# completed occurrence as a contract-shaped CalendarEvent (PR #331) in the
# local JSONL calendar. Deterministic id (Q2): uuid5 over
# machine|emitter|window.start, where window.start is the timer's
# LastTriggerUSec elapse instant. Never fails the chain: emission failures
# are data (journal + exit note), not unit failures.

[Service]
Type=oneshot
# /usr/bin/python3 prefix: repo scripts are not executable by default
# (203/EXEC otherwise — found on the first real trigger, 2026-09-18).
ExecStart=/usr/bin/python3 {script} emit --kind occurred --emitter %i --from-unit %i \\
  --title "timer %i occurred" \\
  --state-dir {state_dir}
"""


DROPIN_ONSUCCESS = """\
# calendar-emit: record this timer's completed occurrences as CalendarEvents
# (design a330914e / PR #331). Chained on success only; remove this file to
# stop emission for this timer.
[Unit]
OnSuccess=calendar-emit@{timer}.service
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        prog="calendar-emit.py",
        description="local CalendarEvent emitter (Q3 slice, design a330914e)")
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("emit", help="append one CalendarEvent to the local calendar")
    p.add_argument("--kind", default="occurred", choices=WIRE_KINDS)
    p.add_argument("--emitter", required=True,
                   help="emitter id (systemd unit, boot-shim step, ...)")
    p.add_argument("--title", required=True)
    p.add_argument("--window-start", default=None,
                   help="ISO-8601 instant (explicit window; overrides --from-unit)")
    p.add_argument("--from-unit", default=None,
                   help="resolve window.start from this timer unit's LastTriggerUSec")
    p.add_argument("--window-end", default=None)
    p.add_argument("--role", default=None, help="participating role (AgentRef)")
    p.add_argument("--session-ref", default=None, help="anchored Session uuid")
    p.add_argument("--payload", default=None, help="JSON object string")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("observe",
                       help="reserved: observed-kind consolidation intake (Q1/Q2)")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("scan-timers",
                       help="inventory user timer units: emitting vs would-emit")
    p.add_argument("--unit-dir", default=str(Path.home() / ".config/systemd/user"))
    p.set_defaults(func=cmd_scan_timers)

    p = sub.add_parser("install",
                       help="write template unit + per-timer OnSuccess drop-ins (print-only default)")
    p.add_argument("--unit-dir", default=str(Path.home() / ".config/systemd/user"))
    p.add_argument("--script", default=None, help="emitter path to bake into units")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--write", action="store_true",
                   help="perform the writes (default: print the plan)")
    p.set_defaults(func=cmd_install)

    args = ap.parse_args()
    sys.exit(args.func(args))
