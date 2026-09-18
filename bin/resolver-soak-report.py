#!/usr/bin/env python3
"""bin/resolver-soak-report.py — wind-resolver enforce-flip readiness report.

Turns the warn-mode resolver-check journal (PR #323, To Do 0577c018) into the
FOUR MECHANICAL FLIP CRITERIA from the enforce-flip gate design (thread
1adce409, comment 0402e9b2) — the lease-check soak pattern applied to the
wind resolver, so the 2026-10-02 review is ONE command:

  (a) WINDOW      — first observation is >= 14 days old (daily coverage shown)
  (b) RESOLUTION  — 100% of REAL demand observations end outcome=ok
                    (probes prove the GATE works; real traffic proves the
                    SEMANTICS — criterion (b) is judged on the real stream)
  (c) VOCABULARY  — zero verdicts outside the V174 six; verdict=unknown lines
                    are flagged REVIEW (lawful absent-capability unknowns vs a
                    classifyVerdict fallback firing is human attribution);
                    any verdict OUTSIDE the six is an outright FAIL
  (d) ERRORS      — zero outcome=error lines across BOTH streams

Honest-separation doctrine (inherited from the lease soak): synthetic probe
lines (probe=synthetic, the daily resolver-probe timer) prove the gate
works; they are reported for visibility and drive window coverage, but are
never counted toward criterion (b).

The report is a decision AID, not the decision: exit code is 0 by default
regardless of readiness. Pass --fail-not-ready to use it as an automation
gate (exit 1 when NOT_READY, 2 for usage errors).

Usage:
    python3 bin/resolver-soak-report.py                 # human-readable
    python3 bin/resolver-soak-report.py --json          # machine-readable
    python3 bin/resolver-soak-report.py --since "30 days ago"
    python3 bin/resolver-soak-report.py --print-lines   # every parsed line
    python3 bin/resolver-soak-report.py --fail-not-ready  # gate mode
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

UNIT_DEFAULT = "wind-srv.service"
SINCE_DEFAULT = "21 days ago"  # margin over the 14d window
WINDOW_DAYS = 14

# The V174 six — mirror of wind-srv capability-resolver.js SATISFACTION_STATES.
# test_resolver_soak_report.py cross-checks this list against the JS source,
# so the two cannot drift silently.
V174_STATES = [
    "satisfied", "satisfied-stale", "unsatisfied", "unreachable", "refused", "unknown",
]


# ── Parsing (hermetic; tests feed lines directly) ────────────────────────────

_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


def _parse_journal_ts(ts_raw: str):
    """Parse a journalctl short-iso timestamp on any supported Python.

    Python < 3.11 fromisoformat() REJECTS UTC offsets without a colon
    ('2026-09-18T05:13:44-0400' — exactly what --output short-iso emits),
    silently yielding ts=None on the 3.10 CI leg while working on 3.11+
    dev shells. Normalize the offset form before parsing (caught by CI,
    pinned by test)."""
    if not ts_raw:
        return None
    try:
        return datetime.fromisoformat(ts_raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(_OFFSET_RE.sub(r"\1:\2", ts_raw))
    except ValueError:
        return None


def parse_resolver_check_line(line: str):
    """Parse one journal line into a dict, or None if not a resolver-check line.

    Expects journalctl --output short-iso lines:
      2026-09-18T05:13:44-0400 titanium node[123]: resolver-check node=review demand=capability:has-active-shrapnel-protocol verdict=satisfied outcome=ok mode=warn probe=synthetic
    """
    marker = "resolver-check "
    idx = line.find(marker)
    if idx < 0:
        return None
    ts_raw = line[:idx].strip().split(" ")[0] if idx > 0 else ""
    ts = _parse_journal_ts(ts_raw)
    fields = {}
    for tok in line[idx + len(marker):].split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            fields[k] = v
    if "outcome" not in fields or "verdict" not in fields:
        return None
    return {
        "ts": ts,
        "node": fields.get("node"),
        "demand": fields.get("demand"),
        "verdict": fields.get("verdict"),
        "outcome": fields.get("outcome"),
        "mode": fields.get("mode"),
        "probe": fields.get("probe"),  # "synthetic" or absent/other (real)
        "raw": line.strip(),
    }


def analyze(lines, now=None, window_days=WINDOW_DAYS):
    """Compute the four criteria from raw journal lines. Pure function."""
    now = now or datetime.now(timezone.utc)
    parsed = [p for p in (parse_resolver_check_line(l) for l in lines) if p]
    synthetic = [p for p in parsed if p.get("probe") == "synthetic"]
    real = [p for p in parsed if p.get("probe") != "synthetic"]

    tss = [p["ts"] for p in parsed if p["ts"]]
    first = min(tss) if tss else None
    age_days = (now - first).total_seconds() / 86400 if first else 0.0
    dates = sorted({p["ts"].date().isoformat() for p in parsed if p["ts"]})

    real_ok = [p for p in real if p["outcome"] == "ok"]
    real_error = [p for p in real if p["outcome"] != "ok"]
    errors = [p for p in parsed if p["outcome"] == "error"]
    # (c) vocabulary — over BOTH streams: the classifyVerdict fallback firing
    # anywhere is signal. unknown may be lawful (absent capability rows emit
    # unknown) → REVIEW with the lines listed; outside the six → FAIL.
    unknown_v = [p for p in parsed if p["verdict"] == "unknown"]
    outside_v = [p for p in parsed if p["verdict"] not in V174_STATES]

    # (a) window
    window_pass = bool(first) and age_days >= window_days

    # (b) real resolution rate — target 100% outcome=ok
    real_decided = len(real_ok) + len(real_error)
    if real_decided == 0:
        resolution_status = "NO_DATA"  # honest: no real traffic yet
        resolution_rate = None
    else:
        resolution_rate = len(real_ok) / real_decided
        resolution_status = "PASS" if len(real_error) == 0 else "FAIL"

    # (c) vocabulary
    if outside_v:
        vocab_status = "FAIL"
    elif unknown_v:
        vocab_status = "REVIEW"  # lawful-unknown vs fallback — human attribution
    else:
        vocab_status = "PASS"

    # (d) errors — both streams
    errors_status = "PASS" if len(errors) == 0 else "FAIL"

    ready = window_pass and resolution_status == "PASS" and \
        vocab_status == "PASS" and errors_status == "PASS"

    return {
        "generated_at": now.isoformat(),
        "window": {
            "first_observation": first.isoformat() if first else None,
            "age_days": round(age_days, 2),
            "required_days": window_days,
            "active_dates": len(dates),
            "status": "PASS" if window_pass else "PENDING",
        },
        "counts": {
            "total": len(parsed),
            "real": len(real),
            "synthetic": len(synthetic),
            "real_ok": len(real_ok),
            "real_error": len(real_error),
            "errors": len(errors),
            "unknown_verdicts": len(unknown_v),
            "outside_vocabulary": len(outside_v),
            "active_dates": len(dates),
        },
        "criteria": {
            "a_window_ge_14d": {"status": "PASS" if window_pass else "PENDING",
                                "age_days": round(age_days, 2)},
            "b_real_resolution_100pct": {"status": resolution_status,
                                         "rate": resolution_rate,
                                         "note": "real stream only (synthetic excluded)"},
            "c_vocabulary_v174_cold": {"status": vocab_status,
                                       "unknown_lines": [p["raw"] for p in unknown_v],
                                       "outside_lines": [p["raw"] for p in outside_v],
                                       "note": "unknown=REVIEW (attribution), outside-six=FAIL"},
            "d_zero_errors": {"status": errors_status,
                              "error_lines": [p["raw"] for p in errors]},
        },
        "synthetic_by_node": _count_by(synthetic, "node"),
        "real_by_node": _count_by(real, "node"),
        "verdict_histogram": _count_by(parsed, "verdict"),
        "ready": ready,
        "verdict": "READY" if ready else "NOT_READY",
    }


def _count_by(rows, key):
    out = {}
    for p in rows:
        out[p[key]] = out.get(p[key], 0) + 1
    return dict(sorted((k or "-", v) for k, v in out.items()))


def render(report: dict) -> str:
    c, w, cr = report["counts"], report["window"], report["criteria"]
    b_rate = cr["b_real_resolution_100pct"]["rate"]
    rate_s = f"{b_rate:.0%}" if isinstance(b_rate, float) else "n/a"
    lines = [
        "== resolver-soak report ==",
        f"generated: {report['generated_at']}",
        f"window: first observation {w['first_observation'] or '(none)'} "
        f"({w['age_days']}d old, {w['active_dates']} active date(s))",
        f"resolutions: {c['total']} total = {c['real']} real + {c['synthetic']} synthetic "
        f"| errors {c['errors']} | unknown verdicts {c['unknown_verdicts']}",
        "",
        f"(a) window >= {w['required_days']}d ....... {cr['a_window_ge_14d']['status']:8} "
        f"({cr['a_window_ge_14d']['age_days']}d)",
        f"(b) real resolution 100% . {cr['b_real_resolution_100pct']['status']:8} "
        f"({c['real_ok']}/{c['real_ok'] + c['real_error']} = {rate_s}; synthetic excluded)",
        f"(c) vocabulary V174 cold . {cr['c_vocabulary_v174_cold']['status']:8} "
        f"({c['unknown_verdicts']} unknown, {c['outside_vocabulary']} outside)",
        f"(d) zero errors ......... {cr['d_zero_errors']['status']:8} "
        f"({c['errors']} error(s))",
        "",
        f"synthetic by node: {report['synthetic_by_node'] or '{}'}",
        f"real by node:      {report['real_by_node'] or '{}'}",
        f"verdict histogram: {report['verdict_histogram'] or '{}'}",
        "",
        f"VERDICT: {report['verdict']}",
    ]
    for u in cr["c_vocabulary_v174_cold"]["unknown_lines"]:
        lines.append(f"  unknown verdict: {u}")
    for o in cr["c_vocabulary_v174_cold"]["outside_lines"]:
        lines.append(f"  OUTSIDE V174:    {o}")
    for e in cr["d_zero_errors"]["error_lines"]:
        lines.append(f"  error:           {e}")
    return "\n".join(lines)


def fetch_journal(unit: str, since: str) -> "tuple[int, list]":
    """journalctl fetch; returns (rc, lines). rc 2 = tool/usage failure.

    A timeout is caught (tool error, exit 2 with a hint to narrow --since) —
    never an unhandled traceback. The lease tool should inherit this when
    next touched."""
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", unit, "--output", "short-iso",
             "--since", since, "--no-pager"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            print(f"ERROR: journalctl failed: {out.stderr.strip()[:200]}",
                  file=sys.stderr)
            return 2, []
        return 0, out.stdout.splitlines()
    except subprocess.TimeoutExpired:
        print("ERROR: journalctl timed out after 60s — narrow the window "
              "(e.g. --since '7 days ago') or trim the unit's journal",
              file=sys.stderr)
        return 2, []
    except FileNotFoundError:
        print("ERROR: journalctl not available", file=sys.stderr)
        return 2, []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--unit", default=UNIT_DEFAULT,
                    help=f"journal unit (default {UNIT_DEFAULT})")
    ap.add_argument("--since", default=SINCE_DEFAULT,
                    help=f"journalctl --since (default '{SINCE_DEFAULT}')")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--print-lines", action="store_true", help="append all parsed lines")
    ap.add_argument("--fail-not-ready", action="store_true",
                    help="exit 1 when verdict is NOT_READY (automation gate)")
    args = ap.parse_args()

    rc, raw_lines = fetch_journal(args.unit, args.since)
    if rc != 0:
        return rc

    report = analyze(raw_lines)
    if args.json:
        if args.print_lines:
            report["lines"] = [p["raw"] for p in
                               (parse_resolver_check_line(l) for l in raw_lines) if p]
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
        if args.print_lines:
            for p in (parse_resolver_check_line(l) for l in raw_lines):
                if p:
                    ts = p["ts"].isoformat() if p["ts"] else "?"
                    stream = "S" if p["probe"] == "synthetic" else "R"
                    print(f"  {ts} [{stream}] {p['node']} {p['demand']} "
                          f"{p['verdict']} {p['outcome']} mode={p['mode']}")
    if args.fail_not_ready and not report["ready"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
