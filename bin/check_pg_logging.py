#!/usr/bin/env python3
"""Nightly pg-logging integrity checker.

Verifies, for each PostgreSQL leg of the R9 backup chain, that
YESTERDAY's pglogs file exists and carries DDL lines with the
attribution prefix — i.e. the durable DDL-attribution posture held
through the previous whole day, not just at the moment of the check.

Legs:
  - titanium (local container): /home/codex/dev/pgsql/pglogs
  - vanadium (ssh, batch):     /home/codex/db/pgsql/pglogs

Filename-date rule: log_filename=postgresql-%Y-%m-%d.log with
log_rotation_size=0 (pinned on both hosts, 2026-09-24) means exactly
one log file per UTC day, so "yesterday's file" is unambiguous. The
checker additionally runs a live attribution probe (CREATE + grep the
log + DROP) to prove DDL is being captured *right now*, not just that
old files look right.

Exit codes: 0 = all legs verified; 1 = findings (missing file, no DDL
lines, DDL lines missing attribution prefix, stale mtime, live probe
not captured); 2 = tool/environment error (ssh failure, unreadable
path, probe connection failure) — the wrapper files nothing on 2.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

VD_SSH = os.environ.get("PGLOGGING_VD_SSH", "vanadium")
TI_LOGDIR = os.environ.get("PGLOGGING_TI_LOGDIR", "/home/codex/dev/pgsql/pglogs")
VD_LOGDIR = "/home/codex/db/pgsql/pglogs"

# Attribution prefix set on both hosts (2026-09-24):
#   %m [%p] db=%d user=%u app=%a client=%h
_ATTR = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)? UTC \[\d+\] "
    r"db=\S* user=\S* app=\S* client=\S* ")
# DDL statement lines: "... LOG:  statement: CREATE TABLE ..."
_DDL = re.compile(
    r"LOG:\s+statement:\s+(CREATE|ALTER|DROP|COMMENT|GRANT|REVOKE)\b")

DDL_KEYWORDS_SHELL = (
    '"statement: CREATE"*|"statement: ALTER"*|"statement: DROP"*'
    '|*"statement: COMMENT"*|"statement: GRANT"*|"statement: REVOKE"*')


def log_name(day) -> str:
    return f"postgresql-{day.isoformat()}.log"


def _classify_lines(text: str) -> tuple[int, int]:
    """Return (ddl_lines, ddl_lines_with_full_attribution_prefix)."""
    ddl = ddl_attr = 0
    for line in text.splitlines():
        if _DDL.search(line):
            ddl += 1
            if _ATTR.match(line):
                ddl_attr += 1
    return ddl, ddl_attr


def check_titanium(yesterday, probe: str, baseline: bool = False) -> dict:
    res: dict = {"leg": "titanium", "file": f"{TI_LOGDIR}/{log_name(yesterday)}"}
    path = res["file"]
    if not os.path.exists(path):
        if baseline:
            res["notes"] = [f"no log for {yesterday} (pre-deployment day); baseline run"
                            " — posture verified against today's log instead"]
        else:
            res["findings"] = [f"yesterday's log file missing: {path}"]
            return res
    else:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as exc:
            return {**res, "tool_error": f"unreadable: {exc}"}
        ddl, ddl_attr = _classify_lines(text)
        res["ddl_lines"] = ddl
        res["ddl_with_attribution"] = ddl_attr
        mtime = datetime.fromtimestamp(os.stat(path).st_mtime, tz=timezone.utc)
        res["mtime_utc"] = mtime.isoformat(timespec="seconds")
        findings: list[str] = []
        if mtime.date() < yesterday:
            findings.append(f"stale: last write {mtime.date()}, expected >= {yesterday}")
        if ddl == 0:
            # Possibly a genuinely DDL-free day; the live probe disambiguates.
            findings.append("no DDL statement lines in yesterday's log")
        elif ddl_attr < ddl:
            findings.append(f"{ddl - ddl_attr}/{ddl} DDL lines missing attribution prefix")
        res["findings"] = findings
        if findings:
            return res
    # Live probe: DDL must be captured in TODAY's log, right now. In
    # baseline mode this (plus today's attribution shape) is the
    # evidence, since yesterday's file predates deployment.
    today_name = f"{TI_LOGDIR}/{log_name(datetime.now(timezone.utc).date())}"
    probe_sql = (f"CREATE TABLE {probe} ();"
                 f"DROP TABLE {probe};")
    ran = subprocess.run(
        ["docker", "exec", "pgvector_db", "psql", "-U", "pguser", "-d",
         "nexus", "-v", "ON_ERROR_STOP=1", "-c", probe_sql],
        capture_output=True, text=True, timeout=60)
    if ran.returncode != 0:
        return {**res, "tool_error": f"live probe failed: {ran.stderr.strip()[-200:]}"}
    try:
        today_text = open(today_name, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return {**res, "tool_error": f"today's log unreadable: {exc}"}
    res["live_probe_captured"] = probe in today_text
    t_ddl, t_attr = _classify_lines(today_text)
    res["today_ddl_lines"] = t_ddl
    res["today_ddl_with_attribution"] = t_attr
    if not res["live_probe_captured"]:
        res.setdefault("findings", []).append(
            f"live probe DDL not captured in {today_name}")
    elif baseline and t_attr < t_ddl:
        res.setdefault("findings", []).append(
            f"{t_ddl - t_attr}/{t_ddl} of today's DDL lines missing attribution prefix")
    res["findings"] = res.get("findings", [])
    return res


def check_vanadium(yesterday, probe: str, baseline: bool = False) -> dict:
    """One batched ssh (bash -s) that prints key=value lines."""
    fname = log_name(yesterday)
    today_fname = log_name(datetime.now(timezone.utc).date())
    script = f"""
f="{VD_LOGDIR}/{fname}"
tf="{VD_LOGDIR}/{today_fname}"
if [ ! -f "$f" ]; then echo "exists=0"; else echo "exists=1"
echo "mtime_epoch=$(stat -c %Y "$f")"
ddl=0; ddl_attr=0
while IFS= read -r line; do
  case "$line" in
    {DDL_KEYWORDS_SHELL}) ddl=$((ddl+1));
      case "$line" in *"client="*) ddl_attr=$((ddl_attr+1));; esac ;;
  esac
done < "$f"
echo "ddl=$ddl"; echo "ddl_attr=$ddl_attr"
fi
tddl=0; tattr=0
if [ -f "$tf" ]; then
while IFS= read -r line; do
  case "$line" in
    {DDL_KEYWORDS_SHELL}) tddl=$((tddl+1));
      case "$line" in *"client="*) tattr=$((tattr+1));; esac ;;
  esac
done < "$tf"
fi
echo "tddl=$tddl"; echo "tattr=$tattr"
docker exec pgvector_db psql -U pguser -d nexus -v ON_ERROR_STOP=1 \\
  -c "CREATE TABLE {probe} (); DROP TABLE {probe};" >/dev/null 2>&1 \\
  && echo "probe_ran=1" || echo "probe_ran=0"
if grep -q "{probe}" "$tf" 2>/dev/null; then echo "probe_captured=1"; else echo "probe_captured=0"; fi
"""
    ran = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", VD_SSH,
         "bash", "-s"],
        input=script, capture_output=True, text=True, timeout=120)
    out = (ran.stdout or "").strip()
    res: dict = {"leg": "vanadium", "file": f"{VD_LOGDIR}/{fname}"}
    if ran.returncode != 0 and not out:
        return {**res, "tool_error": f"ssh failed: {ran.stderr.strip()[-200:]}"}
    kv = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k] = v
    if kv.get("exists") == "0":
        if not baseline:
            res["findings"] = [f"yesterday's log file missing: {res['file']}"]
            return res
        res["notes"] = [f"no log for {yesterday} (pre-deployment day); baseline run"
                        " — posture verified against today's log instead"]
    else:
        ddl, ddl_attr = int(kv.get("ddl", 0)), int(kv.get("ddl_attr", 0))
        res["ddl_lines"] = ddl
        res["ddl_with_attribution"] = ddl_attr
        try:
            mtime = datetime.fromtimestamp(float(kv.get("mtime_epoch", 0)), tz=timezone.utc)
            res["mtime_utc"] = mtime.isoformat(timespec="seconds")
        except (ValueError, OSError, OverflowError):
            return {**res, "tool_error": f"unparseable ssh output: {out[:200]}"}
        findings: list[str] = []
        if mtime.date() < yesterday:
            findings.append(f"stale: last write {mtime.date()}, expected >= {yesterday}")
        if ddl == 0:
            findings.append("no DDL statement lines in yesterday's log")
        elif ddl_attr < ddl:
            findings.append(f"{ddl - ddl_attr}/{ddl} DDL lines missing attribution prefix")
        if findings:
            res["findings"] = findings
            return res
    if kv.get("probe_ran") != "1":
        return {**res, "tool_error": "live probe failed (psql error on vanadium)"}
    res["live_probe_captured"] = kv.get("probe_captured") == "1"
    res["today_ddl_lines"] = int(kv.get("tddl", 0))
    res["today_ddl_with_attribution"] = int(kv.get("tattr", 0))
    if not res["live_probe_captured"]:
        res.setdefault("findings", []).append(
            f"live probe DDL not captured in {VD_LOGDIR}/{today_fname}")
    elif baseline and res["today_ddl_with_attribution"] < res["today_ddl_lines"]:
        res.setdefault("findings", []).append(
            f"{res['today_ddl_lines'] - res['today_ddl_with_attribution']}/"
            f"{res['today_ddl_lines']} of today's DDL lines missing attribution prefix")
    res["findings"] = res.get("findings", [])
    return res


def main() -> int:
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date()
    probe = f"pglog_probe_{now.strftime('%Y%m%d%H%M')}"
    baseline = "--baseline" in sys.argv[1:]
    results = [check_titanium(yesterday, probe, baseline),
               check_vanadium(yesterday, probe, baseline)]

    tool_errors = [r for r in results if r.get("tool_error")]
    if len(tool_errors) == len(results):
        # Nothing verifiable at all — environment failure, not a posture break.
        print(json.dumps({"day": yesterday.isoformat(), "results": results}, indent=2))
        return 2
    findings_present = any(r.get("findings") for r in results)
    print(json.dumps({"day": yesterday.isoformat(),
                      "probe": probe,
                      "baseline": baseline,
                      "ok": not findings_present and not tool_errors,
                      "results": results}, indent=2))
    return 1 if findings_present else 0


if __name__ == "__main__":
    sys.exit(main())
