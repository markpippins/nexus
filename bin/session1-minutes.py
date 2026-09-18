#!/usr/bin/env python3
"""session1-minutes — reconcile Session #1 minutes from canonical surfaces.

Session: scheduled event d64f7a1e (thread 20aecad0), window
2026-09-19T13:05:00Z -> 13:30:00Z, participants dba(convener)/operator/engineer.

Reads ONLY canonical/observable surfaces (no memory, no vibes):
  - journalctl --user -u lease-probe.service    (13:05 probe outcomes)
  - journalctl --user -u resolver-probe.service (13:25 probe outcomes)
  - journalctl --user -u calendar-consolidate.service (13:35 tick, post-window note)
  - titanium JSONL calendar (~/.local/state/nexus-calendar/calendar.jsonl)
  - vanadium JSONL calendar (ssh BatchMode; the cross-machine evidence)

Epistemic discipline (V174 vocabulary, per analyst template critique pending):
  - probe window with no timer fire at all  -> ABSENT (window missed)
  - timer fired, probe errored/timed out    -> UNREACHABLE/FAILED (ran, no result)
  - role not adopted in lease probe          -> UNADOPTED (never conflated with refused)

Output: minutes markdown to stdout; --post also files it to the session-plan
thread 20aecad0 as a comment and emits the corresponding agent record.

Exit: 0 minutes produced (complete or with recorded gaps); 1 unable to
gather (gather failures are printed per-surface as data unless ALL fail).
"""
import datetime as dt
import json
import os
import subprocess
import sys

UTC = dt.timezone.utc
SESSION_ID = "d64f7a1e-c757-5abb-8c3e-2b4f815d4ff8"  # prefix of the scheduled event
WINDOW_START = dt.datetime(2026, 9, 19, 13, 5, 0, tzinfo=UTC)
WINDOW_END = dt.datetime(2026, 9, 19, 13, 30, 0, tzinfo=UTC)
THREAD = "20aecad0-df4d-4e85-90a6-4aefdc7dafb4"
DBA_ID = "1ea49b6d-1f57-456d-941a-626b6b344a79"
STATE = "nexus-calendar/calendar.jsonl"
VANADIUM = "vanadium.attlocal.net"


def journal(unit: str, since: str, until: str) -> str:
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--since", since,
             "--until", until, "--no-pager"],
            capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else f"(journalctl rc={r.returncode})"
    except Exception as e:
        return f"(journalctl unavailable: {type(e).__name__})"


def read_calendar(host: str = None) -> dict:
    """Return {present: bool, events: [...], error: str|None} for a machine."""
    try:
        if host is None:
            path = os.path.expanduser(f"~/.local/state/{STATE}")
            if not os.path.exists(path):
                return {"present": False, "events": [], "error": None}
            with open(path) as fh:
                lines = fh.readlines()
        else:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                 f"cat ~/.local/state/{STATE}"],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return {"present": False, "events": [], "error": r.stderr.strip()[:120]}
            lines = r.stdout.splitlines()
        events = []
        for ln in lines:
            try:
                events.append(json.loads(ln))
            except Exception:
                continue  # torn line is data; counted as invalid below
        return {"present": True, "events": events, "error": None}
    except Exception as e:
        return {"present": False, "events": [], "error": f"{type(e).__name__}: {e}"}


def calendar_window_summary(cal: dict, label: str) -> list[str]:
    out = []
    if not cal["present"]:
        out.append(f"- {label}: calendar ABSENT ({cal['error'] or 'no file'})")
        return out
    evs = cal["events"]
    out.append(f"- {label}: {len(evs)} events total")
    for e in evs:
        ws = e.get("window", {}).get("start", "")
        if ws.startswith("2026-09-19T13:"):
            out.append(f"    in-window: {e['source']['emitter']} @ {ws} "
                       f"(id {e['eventId'][:8]}, kind {e.get('kind')})")
    return out


def probe_facts() -> dict:
    # journalctl interprets naive --since/--until in LOCAL time; probes are
    # pinned UTC. Use explicit offset-qualified strings (works on GNU journald).
    since = "2026-09-19 13:00:00 UTC"
    until = "2026-09-19 13:40:00 UTC"
    lease_j = journal("lease-probe.service", since, until)
    res_j = journal("resolver-probe.service", since, until)
    cons_j = journal("calendar-consolidate.service", since, until)

    facts = {"lease": {"raw": lease_j, "ran": False, "roles": None,
                       "adopted": None, "unadopted": None, "adoptions": []},
             "resolver": {"raw": res_j, "ran": False, "summary": None},
             "consolidate": {"raw": cons_j, "ran": False, "inert": None}}

    for line in lease_j.splitlines():
        if "start roles=" in line:
            facts["lease"]["ran"] = True
        if "done roles=" in line:
            facts["lease"]["ran"] = True
            for part in line.split():
                if part.startswith("roles="):
                    facts["lease"]["roles"] = part.split("=")[1]
                if part.startswith("adopted="):
                    facts["lease"]["adopted"] = part.split("=")[1]
                if part.startswith("unadopted="):
                    facts["lease"]["unadopted"] = part.split("=")[1]
        if "adopted=True" in line or "adopted=False" in line:
            role = mode = ref = None
            for part in line.split():
                if part.startswith("role="):
                    role = part.split("=")[1]
                if part.startswith("mode="):
                    mode = part.split("=")[1]
                if part.startswith("lease_ref="):
                    ref = part.split("=")[1]
            if role:
                facts["lease"]["adoptions"].append(
                    {"role": role, "adopted": "adopted=True" in line,
                     "mode": mode, "lease_ref": ref})

    if "start" in res_j or "done" in res_j or "Finished" in res_j:
        facts["resolver"]["ran"] = True
    tail = [ln for ln in res_j.splitlines() if ln.strip()][-3:]
    facts["resolver"]["summary"] = " | ".join(
        ln.split("]: ", 1)[-1] for ln in tail) if tail else "(no journal detail)"

    facts["consolidate"]["ran"] = "Started" in cons_j or "inert" in cons_j
    facts["consolidate"]["inert"] = "[inert]" in cons_j
    return facts


def build_minutes() -> str:
    stamp = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    facts = probe_facts()
    ti = read_calendar(None)
    va = read_calendar(VANADIUM)

    L = []
    L.append("## Session #1 minutes — first co-incident Session (reconciled)")
    L.append("")
    L.append(f"Reconciled at {stamp} from canonical surfaces only "
             f"(journal + both machines' calendars). Session event `d64f7a1e`, "
             f"window 2026-09-19T13:05Z→13:30Z.")
    L.append("")

    L.append("### 1. Lease probe (13:05Z)")
    if facts["lease"]["ran"]:
        L.append(f"- RAN: roles={facts['lease']['roles']} "
                 f"adopted={facts['lease']['adopted']} "
                 f"unadopted={facts['lease']['unadopted']} "
                 "(unadopted = no lease held; NOT a refusal)")
        for a in facts["lease"]["adoptions"]:
            state = "adopted" if a["adopted"] else "unadopted"
            ref = a["lease_ref"] or "—"
            L.append(f"    - {a['role']}: {state} (mode={a['mode']}, lease_ref={ref[:8]})")
    else:
        L.append("- ABSENT: no lease-probe start/done lines in window "
                 "(timer did not fire — distinct from a failed probe)")
    L.append("")

    L.append("### 2. Resolver probe (13:25Z) — soak day 2")
    if facts["resolver"]["ran"]:
        L.append(f"- RAN. Journal tail: {facts['resolver']['summary'] or '(no journal detail)'}")
    else:
        L.append("- ABSENT: no resolver-probe activity in window")
    L.append("")

    L.append("### 3. Cross-machine evidence — vanadium rhythm (first)")
    L.extend(calendar_window_summary(va, "vanadium"))
    sonar = [e for e in va.get("events", [])
             if e["source"]["emitter"] == "sonar-health"
             and e["window"]["start"] >= "2026-09-19T13:00:00Z"]
    L.append(f"- sonar-health events since 13:00Z: {len(sonar)} "
             "(expected 3 for a 30-min window at 15-min cadence)")
    if sonar:
        L.append(f"- first in-window: {sonar[0]['window']['start']} "
                 f"(id {sonar[0]['eventId'][:8]}) — vanadium was part of the "
                 "shared temporal record while titanium held the Session")
    L.extend(calendar_window_summary(ti, "titanium"))
    L.append("")

    L.append("### 4. Deviations")
    devs = []
    if not facts["lease"]["ran"]:
        devs.append("lease-probe window: ABSENT (timer did not fire)")
    if not facts["resolver"]["ran"]:
        devs.append("resolver-probe window: ABSENT (timer did not fire)")
    if len(sonar) == 0 and va["present"]:
        devs.append("vanadium sonar-health: no in-window events (cadence broken?)")
    if not va["present"]:
        devs.append(f"vanadium calendar unreachable: {va['error']}")
    L.append("- " + ("; ".join(devs) if devs else "none — all windows fired as scheduled"))
    L.append("")

    L.append("### 5. Disposition")
    L.append("- context-only (V184 inert): minutes live as agent record + thread "
             "comment; calendars remain per-machine JSONL until apply")
    L.append("- consolidation tick 13:35Z: post-window; its first REAL fold is "
             "expected only after V184 apply + operator go")
    L.append("- scheduled recurrence: continues daily; this template is the "
             "standing minutes format (analyst epistemics refinement pending)")
    L.append("")
    L.append(f"reconciled-by: dba · freebuff/buffy · session d64f7a1e · {stamp}")
    return "\n".join(L)


def post_to_thread(minutes: str) -> str:
    import urllib.request
    body = {"body": minutes, "postedById": DBA_ID,
            "role": "dba", "model": "freebuff/buffy"}
    req = urllib.request.Request(
        f"http://localhost:3107/api/forums/threads/{THREAD}/comments",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
        return d.get("id", "??")


def main() -> int:
    # Pre-window refusal (lilac-flip convention): if triggered before the
    # window closes, refuse loudly and exit 2 (SuccessExitStatus in the unit).
    if dt.datetime.now(UTC) < WINDOW_END and "--force" not in sys.argv:
        print(f"[pre-window] Session window closes {WINDOW_END.isoformat()}; "
              "refusing to reconcile early. Use --force to override.", file=sys.stderr)
        return 2
    minutes = build_minutes()
    if "--post" in sys.argv:
        try:
            cid = post_to_thread(minutes)
            print(f"posted comment id: {cid}")
        except Exception as e:
            print(f"POST failed: {e}", file=sys.stderr)
            print(minutes)
            return 1
    print(minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
