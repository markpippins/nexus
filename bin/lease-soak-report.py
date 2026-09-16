#!/usr/bin/env python3
"""bin/lease-soak-report.py — enforce-flip readiness report from the soak journal.

Turns the warn-mode lease-check journal (PRs #272/#273/#276, continuity thread
65fe85a8) into the FOUR MECHANICAL FLIP CRITERIA from the soak review
(thread comment 42a11672):

  (a) WINDOW      — first observation is ≥ 14 days old (daily coverage shown)
  (b) ADOPTION    — 100% of REAL (non-probe) /chat observations adopted
  (c) ATTRIBUTION — zero unadopted real lines; otherwise they are listed
                    for human attribution (attribution is judgment, not math)
  (d) ERRORS      — zero resolver errors across the window (both streams)

Honest-separation doctrine (the baseline post's core position): synthetic
probes (probe=synthetic, the daily lease-probe timer) prove the GATE works;
criteria (b)/(c) are judged on the REAL stream only. Synthetic counts are
reported for visibility but never counted toward adoption evidence.

The report is a decision AID, not the decision: exit code is 0 by default
regardless of readiness. Pass --fail-not-ready to use it as a CI/automation
gate (exit 1 when not READY).

Usage:
    python3 bin/lease-soak-report.py                 # human-readable report
    python3 bin/lease-soak-report.py --json          # machine-readable
    python3 bin/lease-soak-report.py --since "30 days ago"
    python3 bin/lease-soak-report.py --print-lines   # show every parsed line
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

UNIT_DEFAULT = "operator-svc.service"
SINCE_DEFAULT = "14 days ago"
WINDOW_DAYS = 14


# ── Parsing (hermetic; tests feed lines directly) ────────────────────────────


def parse_lease_check_line(line: str):
    """Parse one journal line into a dict, or None if not a lease-check line.

    Expects journalctl --output short-iso lines:
        2026-09-16T12:51:05-0400 titanium python3[123]: lease-check role=dba mode=warn outcome=unadopted probe=synthetic
    """
    marker = "lease-check "
    idx = line.find(marker)
    if idx < 0:
        return None
    ts_raw = line[:idx].strip().split(" ")[0] if idx > 0 else ""
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        ts = None
    fields = {}
    for tok in line[idx + len(marker):].split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            fields[k] = v
    if "role" not in fields or "outcome" not in fields:
        return None
    return {
        "ts": ts,
        "role": fields.get("role"),
        "mode": fields.get("mode"),
        "outcome": fields.get("outcome"),
        "lease_ref": fields.get("lease_ref"),
        "probe": fields.get("probe"),          # "synthetic" or absent (real)
        "error": fields.get("error"),
        "raw": line.strip(),
    }


def analyze(lines, now=None, window_days=WINDOW_DAYS):
    """Compute the four criteria from raw journal lines. Pure function."""
    now = now or datetime.now(timezone.utc)
    parsed = [p for p in (parse_lease_check_line(l) for l in lines) if p]
    synthetic = [p for p in parsed if p.get("probe") == "synthetic"]
    real = [p for p in parsed if p.get("probe") != "synthetic"]

    tss = [p["ts"] for p in parsed if p["ts"]]
    first = min(tss) if tss else None
    age_days = (now - first).total_seconds() / 86400 if first else 0.0
    dates = sorted({p["ts"].date().isoformat() for p in parsed if p["ts"]})

    real_adopted = [p for p in real if p["outcome"] == "adopted"]
    real_unadopted = [p for p in real if p["outcome"] == "unadopted"]
    errors = [p for p in parsed if p["outcome"] == "error" or p.get("error")]
    refused = [p for p in parsed if p["outcome"] == "refused"]

    # (a) window
    window_pass = bool(first) and age_days >= window_days

    # (b) real adoption rate — target 100%
    real_decided = len(real_adopted) + len(real_unadopted)
    if real_decided == 0:
        adoption_status = "NO_DATA"   # honest: no real traffic yet
        adoption_rate = None
    else:
        adoption_rate = len(real_adopted) / real_decided
        adoption_status = "PASS" if len(real_unadopted) == 0 else "FAIL"

    # (c) unadopted attribution
    attribution_status = "PASS" if len(real_unadopted) == 0 else "REVIEW"

    # (d) resolver errors
    errors_status = "PASS" if len(errors) == 0 else "FAIL"

    ready = window_pass and adoption_status == "PASS" and \
        attribution_status == "PASS" and errors_status == "PASS"

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
            "real_adopted": len(real_adopted),
            "real_unadopted": len(real_unadopted),
            "errors": len(errors),
            "refused": len(refused),
            "active_dates": len(dates),
        },
        "criteria": {
            "a_window_ge_14d": {"status": "PASS" if window_pass else "PENDING",
                                "age_days": round(age_days, 2)},
            "b_real_adoption_100pct": {"status": adoption_status,
                                       "rate": adoption_rate,
                                       "note": "real stream only (synthetic excluded)"},
            "c_unadopted_attributed": {"status": attribution_status,
                                       "unadopted_lines": [p["raw"] for p in real_unadopted]},
            "d_zero_resolver_errors": {"status": errors_status,
                                       "error_lines": [p["raw"] for p in errors]},
        },
        "synthetic_by_role": _count_by(synthetic, "role"),
        "real_by_role": _count_by(real, "role"),
        "ready": ready,
        "verdict": "READY" if ready else "NOT_READY",
    }


def _count_by(rows, key):
    out = {}
    for p in rows:
        out[p[key]] = out.get(p[key], 0) + 1
    return dict(sorted(out.items()))


def render(report: dict) -> str:
    c, w, cr = report["counts"], report["window"], report["criteria"]
    b_rate = cr["b_real_adoption_100pct"]["rate"]
    rate_s = f"{b_rate:.0%}" if isinstance(b_rate, float) else "n/a"
    lines = [
        "== lease-soak report ==",
        f"generated: {report['generated_at']}",
        f"window: first observation {w['first_observation'] or '(none)'} "
        f"({w['age_days']}d old, {w['active_dates']} active date(s))",
        f"observations: {c['total']} total = {c['real']} real + {c['synthetic']} synthetic "
        f"| errors {c['errors']} | refused {c['refused']}",
        "",
        f"(a) window >= {w['required_days']}d ....... {cr['a_window_ge_14d']['status']:8} "
        f"({cr['a_window_ge_14d']['age_days']}d)",
        f"(b) real adoption 100% ... {cr['b_real_adoption_100pct']['status']:8} "
        f"({c['real_adopted']}/{c['real_adopted'] + c['real_unadopted']} = {rate_s}; synthetic excluded)",
        f"(c) unadopted attributed . {cr['c_unadopted_attributed']['status']:8} "
        f"({c['real_unadopted']} line(s))",
        f"(d) zero resolver errors . {cr['d_zero_resolver_errors']['status']:8} "
        f"({c['errors']} error(s))",
        "",
        f"synthetic by role: {report['synthetic_by_role'] or '{}'}",
        f"real by role:      {report['real_by_role'] or '{}'}",
        "",
        f"VERDICT: {report['verdict']}",
    ]
    for u in cr["c_unadopted_attributed"]["unadopted_lines"]:
        lines.append(f"  unadopted real: {u}")
    for e in cr["d_zero_resolver_errors"]["error_lines"]:
        lines.append(f"  error:          {e}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--unit", default=UNIT_DEFAULT, help=f"journal unit (default {UNIT_DEFAULT})")
    ap.add_argument("--since", default=SINCE_DEFAULT, help=f"journalctl --since (default '{SINCE_DEFAULT}')")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--print-lines", action="store_true", help="append all parsed lines")
    ap.add_argument("--fail-not-ready", action="store_true",
                    help="exit 1 when verdict is NOT_READY (automation gate)")
    args = ap.parse_args()

    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", args.unit, "--output", "short-iso",
             "--since", args.since, "--no-pager"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            print(f"ERROR: journalctl failed: {out.stderr.strip()[:200]}", file=sys.stderr)
            return 2
        raw_lines = out.stdout.splitlines()
    except FileNotFoundError:
        print("ERROR: journalctl not available", file=sys.stderr)
        return 2

    report = analyze(raw_lines)
    if args.json:
        if args.print_lines:
            report["lines"] = [p["raw"] for p in
                               (parse_lease_check_line(l) for l in raw_lines) if p]
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
        if args.print_lines:
            for p in (parse_lease_check_line(l) for l in raw_lines):
                if p:
                    print(f"  {p['ts'].isoformat() if p['ts'] else '?'} "
                          f"[{'S' if p['probe'] == 'synthetic' else 'R'}] "
                          f"{p['role']} {p['outcome']} mode={p['mode']}")
    if args.fail_not_ready and not report["ready"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
