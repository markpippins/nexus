#!/usr/bin/env python3
"""calendar-consolidate — the fold-back intake for the JSONL calendar.

The Q3 emitters (bin/calendar-emit.py, PRs #332/#333) accumulate
contract-shaped CalendarEvents in a local JSONL file. This tool is the
**observe** half of the design (thread a330914e): a deterministic,
idempotent intake that folds those events into the canonical store
(vision.calendar_events, V184) when — and only when — that store exists.

Inert by construction (same posture as V184 itself):

  - When vision.calendar_events is ABSENT (V184 not applied — awaiting the
    roundtable's Q1/Q2 answers plus an operator go), the tool refuses with
    a NAMED reason (exit 3). It never silently skips and never writes
    anywhere else: unreachable data must not be attested as stored (the
    V174 / auditor-grant epistemic discipline applied to consolidation).
  - When the store IS present, the V184 PK is the dedupe: INSERT ...
    ON CONFLICT (event_id) DO NOTHING. Deterministic ids (Q2,
    uuid5(machine|emitter|window_start)) make re-runs and cross-machine
    consolidation no-ops without coordination.

Fidelity stance:
  - Event ids are RE-DERIVED from (machine, emitter, window_start) and
    compared against the eventId on the wire; a mismatch is a validation
    failure ("foreign id"), never silently trusted.
  - Invalid/torn JSONL lines are collected as data (line numbers + reason),
    never crash the run; --strict turns any invalid line into exit 4.
  - recorded_by carries the CONSOLIDATOR identity (who folded the rows) —
    the source machine stays in source_machine — so the NEBULA_AUDIT trail
    attributes the intake, per R10/the audit-trigger pattern.

The DB seam is injectable (exec_fn(sql, params) -> rows); the default
implementation uses psycopg2 + CONDUIT_PG_DSN. No other side effects.

Usage:
  calendar-consolidate.py validate [--source P] [--json]      # parse/verify only
  calendar-consolidate.py observe  [--source P] [--by NAME]   # fold back (inert unless V184 live)
  calendar-consolidate.py observe  --dry-run                  # full check, zero writes

Exit codes: 0 ok (report carries the detail) · 1 hard error (missing file,
DB unreachable) · 3 inert refusal (V184 absent) · 4 invalid lines with
--strict.
"""
import argparse
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

_BIN = Path(__file__).resolve().parent
# The emitter file is dash-named; load it by path so Q2 identity and the
# kind vocabulary have exactly one source of truth (no copied constants).
_spec = importlib.util.spec_from_file_location("calendar_emit", _BIN / "calendar-emit.py")
_ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ce)
EVENT_NS = _ce.EVENT_NS
WIRE_KINDS = _ce.WIRE_KINDS

DEFAULT_STATE_DIR = Path(os.environ.get(
    "CALENDAR_STATE_DIR",
    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
)) / "nexus-calendar"

REQUIRED_KEYS = ("eventId", "kind", "source", "title", "window")


def now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hostname() -> str:
    import socket
    return socket.gethostname()


def load_events(path: Path) -> tuple[list[dict], list[dict]]:
    """Parse JSONL: (valid_events, invalid_lines). Torn lines are data."""
    events, invalid = [], []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("not a JSON object")
                events.append(obj)
            except (json.JSONDecodeError, ValueError) as exc:
                invalid.append({"line": lineno, "reason": str(exc)[:160]})
    return events, invalid


def derive_event_id(machine: str, emitter: str, window_start: str) -> str:
    """Same derivation as the emitter (Q2) — ids must match, not be trusted."""
    return str(uuid.uuid5(EVENT_NS, f"{machine}|{emitter}|{window_start}"))


def validate_event(ev: dict) -> list[str]:
    """Contract-shape validation (#331 types / V184 CHECKs). Returns errors."""
    errors = []
    for key in REQUIRED_KEYS:
        if key not in ev or ev[key] is None:
            errors.append(f"missing required key {key!r}")
    if errors:
        return errors

    kind = ev.get("kind")
    if kind not in WIRE_KINDS:
        errors.append(f"kind {kind!r} not in {list(WIRE_KINDS)}")

    src = ev.get("source") or {}
    if not isinstance(src, dict) or "machine" not in src or "emitter" not in src:
        errors.append("source must carry machine and emitter")
        src = src if isinstance(src, dict) else {}

    win = ev.get("window") or {}
    start = win.get("start") if isinstance(win, dict) else None
    if not isinstance(start, str) or not start:
        errors.append("window.start missing")
    elif _parse_ts(start) is None:
        errors.append(f"window.start not RFC3339: {start!r}")
    end = win.get("end") if isinstance(win, dict) else None
    if end is not None:
        if _parse_ts(end) is None:
            errors.append(f"window.end not RFC3339: {end!r}")
        elif _parse_ts(start) is not None and _parse_ts(end) < _parse_ts(start):
            errors.append("window.end precedes window.start")

    if isinstance(start, str) and isinstance(src.get("machine"), str) \
            and isinstance(src.get("emitter"), str):
        expected = derive_event_id(src["machine"], src["emitter"], start)
        if ev.get("eventId") != expected:
            errors.append(
                f"eventId mismatch: wire {ev.get('eventId')!r} != "
                f"derived {expected!r} (foreign id)")

    # V184 kind epistemics parity: observed rows must cite their lineage.
    if kind == "observed":
        cf = ev.get("consolidatedFrom")
        if not (isinstance(cf, list) and cf):
            errors.append("kind=observed requires non-empty consolidatedFrom")

    if not isinstance(ev.get("title"), str) or not ev["title"].strip():
        errors.append("title must be a non-empty string")

    participants = ev.get("participants", [])
    if not isinstance(participants, list):
        errors.append("participants must be an array")

    return errors


def _parse_ts(value: str):
    """RFC3339-ish parse; returns datetime or None. Tolerates 'Z'."""
    import datetime
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── DB seam ──────────────────────────────────────────────────────────────

def default_exec_factory():
    """Default seam: psycopg2 against CONDUIT_PG_DSN. Built lazily."""
    def exec_fn(sql: str, params: tuple = ()) -> list[tuple]:
        import psycopg2
        dsn = os.environ.get("CONDUIT_PG_DSN",
                             "postgresql://pguser:pgpass@localhost:5432/nexus")
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else []
    return exec_fn


def store_present(exec_fn) -> bool:
    rows = exec_fn("SELECT to_regclass(%s)", ("vision.calendar_events",))
    return bool(rows and rows[0][0])


def sink_calendar(exec_fn, calendar_id: str, machine: str) -> None:
    """Idempotent parent-calendar upsert (scope='local', owner=machine)."""
    exec_fn(
        "INSERT INTO vision.calendars (calendar_id, title, scope, owner) "
        "VALUES (%s, %s, 'local', %s) ON CONFLICT (calendar_id) DO NOTHING",
        (calendar_id, f"local calendar ({machine})", machine))


def sink_event(exec_fn, ev: dict, consolidator: str) -> str:
    """Insert one validated event; 'inserted' | 'skipped' by PK conflict."""
    src = ev["source"]
    cal_id = ev.get("calendarId") or derive_event_id(
        src["machine"], "local-calendar", "calendar-root")
    sink_calendar(exec_fn, cal_id, src["machine"])

    provenance = ev.get("provenance") or {}
    recorded_at = provenance.get("recordedAt")
    rows = exec_fn(
        "INSERT INTO vision.calendar_events ("
        "  event_id, calendar_id, kind, source_machine, source_emitter,"
        "  title, window_start, window_end, participants, session_ref,"
        "  payload, recorded_at, recorded_by, consolidated_from)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "  COALESCE(%s::timestamptz, now()), %s, %s)"
        " ON CONFLICT (event_id) DO NOTHING"
        " RETURNING event_id",
        (ev["eventId"], cal_id, ev["kind"], src["machine"], src["emitter"],
         ev["title"], ev["window"]["start"], ev["window"].get("end"),
         json.dumps(ev.get("participants") or []),
         ev.get("sessionRef"),
         json.dumps(ev.get("payload") or {}),
         recorded_at, consolidator,
         json.dumps(ev["consolidatedFrom"])
         if ev.get("consolidatedFrom") is not None else None))
    return "inserted" if rows else "skipped"


# ── commands ─────────────────────────────────────────────────────────────

def collect(source: Path) -> tuple[list[dict], list[dict], dict]:
    events, invalid = load_events(source)
    report = {"source": str(source), "total": len(events) + len(invalid)}
    for ev in events:
        errs = validate_event(ev)
        if errs:
            invalid.append({
                "line": None,
                "eventId": ev.get("eventId"),
                "reason": "; ".join(errs),
            })
            events.remove(ev)
    return events, invalid, report


def cmd_validate(args) -> int:
    source = Path(args.source)
    if not source.is_file():
        print(f"ERROR: calendar file not found: {source}", file=sys.stderr)
        return 1
    events, invalid, report = collect(source)
    report.update(status="validated", valid=len(events), invalid=len(invalid),
                  errors=invalid[:20])
    print(json.dumps(report, indent=2) if args.json else _human(report))
    return 4 if (args.strict and invalid) else 0


def cmd_observe(args) -> int:
    source = Path(args.source)
    if not source.is_file():
        print(f"ERROR: calendar file not found: {source}", file=sys.stderr)
        return 1
    exec_fn = default_exec_factory()

    # Inert gate FIRST — a named refusal, before any parsing side effects.
    try:
        present = store_present(exec_fn)
    except Exception as exc:  # DB unreachable is a hard error, not inertness
        print(f"ERROR: store unreachable: {exc}", file=sys.stderr)
        return 1
    if not present:
        msg = ("INERT: vision.calendar_events ABSENT — V184 not applied "
               "(gated on roundtable Q1/Q2 + operator go; thread a330914e). "
               "Nothing was written; the JSONL calendar remains the source of truth.")
        report = {"status": "inert", "reason": msg, "source": str(source),
                  "at": now_iso()}
        print(json.dumps(report, indent=2) if args.json else msg)
        return 3

    events, invalid, report = collect(source)
    if args.dry_run:
        report.update(status="dry-run", valid=len(events), invalid=len(invalid),
                      would_insert=len(events), errors=invalid[:20])
        print(json.dumps(report, indent=2) if args.json else _human(report))
        return 4 if (args.strict and invalid) else 0

    consolidator = args.by or hostname()
    inserted = skipped = 0
    for ev in events:
        try:
            outcome = sink_event(exec_fn, ev, consolidator)
        except Exception as exc:
            invalid.append({"eventId": ev.get("eventId"),
                            "reason": f"sink error: {str(exc)[:160]}"})
            continue
        if outcome == "inserted":
            inserted += 1
        else:
            skipped += 1
    report.update(status="consolidated", valid=len(events),
                  invalid=len(invalid), inserted=inserted, skipped=skipped,
                  by=consolidator, errors=invalid[:20], at=now_iso())
    print(json.dumps(report, indent=2) if args.json else _human(report))
    return 4 if (args.strict and invalid) else 0


def _human(report: dict) -> str:
    keys = ("status", "source", "total", "valid", "invalid", "inserted",
            "skipped", "by", "reason", "at")
    parts = [f"{k}={report[k]}" for k in keys if k in report]
    if report.get("errors"):
        parts.append(f"errors[:3]={json.dumps(report['errors'][:3])}")
    return " ".join(parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="calendar-consolidate",
        description="Fold the JSONL calendar back into vision.calendar_events "
                    "(observe mode; staged inert behind V184 / Q1+Q2).")
    p.add_argument("mode", choices=("validate", "observe"))
    p.add_argument("--source", default=str(DEFAULT_STATE_DIR / "calendar.jsonl"))
    p.add_argument("--by", default=None,
                   help="consolidator identity for recorded_by (default: hostname)")
    p.add_argument("--strict", action="store_true",
                   help="exit 4 when any line is invalid")
    p.add_argument("--dry-run", action="store_true",
                   help="observe: full validation + availability check, zero writes")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    args = p.parse_args(argv)
    return cmd_observe(args) if args.mode == "observe" else cmd_validate(args)


if __name__ == "__main__":
    sys.exit(main())
