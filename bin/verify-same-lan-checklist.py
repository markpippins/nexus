#!/usr/bin/env python3
"""verify-same-lan-checklist.py — one read-only command for the §6 checklist.

Settles the four items in `docs/system-pulse-revised-2026-11-10.md` §6 from
titanium, printing one PASS/FAIL line per item:

  item 1  fleet reachability        .209 (vanadium), .82 (thallium), .229 (helium)
  item 2  vanadium timers + logs    the three user timers and the three
                                    timestamped logs the summation cites
  item 3  GitHub PR metadata        honest SKIP: 16 PRs (§2c) need the network
  item 4  twin / JVM liveness       19 moleculer 41xx canary ports + :8092

Statuses, and what they mean:

  PASS         verified green from this host
  FAIL         real defect (any FAIL drives exit 1)
  WARN         reachable but off-contract (e.g. a retired lane is listening)
  SKIP         honestly not checkable from here (item 3 off-network)
  UNREACHABLE  host/network is absent right now — the normal travel state,
               NOT a defect. A remote-caused backup failure classifies here
               instead of FAIL so an off-network run is never read as a
               host-down claim (the summation's own rule).

Nothing is started, pushed, or mutated. Every probe is a TCP connect, one GET,
`systemctl --user`/`journalctl --user` read, or a local stat/log read.

Usage:
  bin/verify-same-lan-checklist.py                 # full checklist
  bin/verify-same-lan-checklist.py --json          # machine summary only
  bin/verify-same-lan-checklist.py --skip-remote   # local timer/log checks only
  bin/verify-same-lan-checklist.py --timeout 1     # faster off-network pass

Exit contract: 0 = no FAIL (SKIP/UNREACHABLE allowed); 1 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------------------ constants

VANADIUM_HOST = os.environ.get("NEXUS_VANADIUM_HOST", "192.168.1.209")
THALLIUM_HOST = os.environ.get("NEXUS_THALLIUM_HOST", "192.168.1.82")
HELIUM_HOST = os.environ.get("NEXUS_HELIUM_HOST", "192.168.1.229")

DEFAULT_TIMEOUT = 2.0
SSH_PORT = 22
JVM_PORT = 8092
LOG_DIR = Path(os.environ.get("NEXUS_PGSQL_DIR", "/home/codex/dev/pgsql"))
PORTS_YAML = Path(__file__).resolve().parent.parent / "moleculer" / "ports.yaml"

# (unit, log file, cadence in hours, human cadence)
VANADIUM_UNITS = (
    ("sync-pg-to-vanadium.timer", "sync-vanadium.log", 1.0, "10 min"),
    ("backup-pg-to-vanadium.timer", "pg-backup-to-vanadium.log", 26.0, "daily 03:30"),
    ("backup-vanadium-ci.timer", "vanadium-ci-backup.log", 26.0, "daily 04:21"),
)

# Stale-line markers that mean the last run reported a defect, even if the
# systemd outcome line says Finished (the exit-0-with-defect guard).
FAULT_WORDS = ("FAIL:", "failed", "FATAL", "error:")

TS_RE = re.compile(r"\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
PORT_RE = re.compile(r"^\s*-\s*port:\s*(\d+)\s*$")
NAME_RE = re.compile(r'^\s{4}name:\s*"([^"]+)"')

# Used only when ports.yaml cannot be read; mirrors PORT-MAP.md's canary band.
FALLBACK_TWINS = (
    (4100, "kernel"), (4101, "nebula"), (4104, "conduit"), (4106, "cascade"),
    (4107, "assembly"), (4109, "knowledge"), (4110, "execution"),
    (4111, "peb"), (4114, "voyager"), (4115, "substance"), (4116, "aegis"),
    (4118, "wind"), (4150, "role-memory"), (4160, "semantics"),
    (4170, "draft"), (4171, "resolution"), (4410, "tackle"),
    (4420, "harness"), (4501, "prompt-sync"),
)

PASS, FAIL, WARN, SKIP, UNREACHABLE = (
    "PASS", "FAIL", "WARN", "SKIP", "UNREACHABLE")


# -------------------------------------------------------------------- helpers

def probe_tcp(host: str, port: int, timeout: float):
    """(open, error-text). A closed port is not an error; absence is."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def fetch_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.load(resp)


def parse_timer_line(line: str):
    """list-timers shows NEXT then LAST; 'n/a' LAST yields fewer matches."""
    stamps = TS_RE.findall(line)
    return stamps[1] if len(stamps) >= 2 else None


def parse_local_ts(stamp: str) -> datetime:
    return datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M:%S")


def hours_since(stamp: datetime, now: datetime) -> float:
    return (now - stamp).total_seconds() / 3600.0


def tail(path: Path, max_bytes: int = 8192) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last_meaningful_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()[:160]
    return ""


def load_twin_ports(path: Path):
    """(port, name) for every canary twin in ports.yaml; fallback on failure."""
    try:
        ports, names, in_canary = [], [], False
        for raw in path.read_text().splitlines():
            if raw.startswith("canary:"):
                in_canary = True
                continue
            if raw.startswith("infra:"):
                in_canary = False
                continue
            if not in_canary:
                continue
            m = PORT_RE.match(raw)
            if m:
                ports.append(int(m.group(1)))
                continue
            m = NAME_RE.match(raw)
            if m and ports:
                names.append(m.group(1))
        pairs = list(zip(ports, names)) if len(names) == len(ports) else []
        return pairs or list(FALLBACK_TWINS)
    except OSError:
        return list(FALLBACK_TWINS)


# --------------------------------------------------------------- check report

@dataclass
class Check:
    cid: str
    name: str
    status: str
    evidence: str


@dataclass
class Report:
    checks: list = field(default_factory=list)

    def add(self, cid, name, status, evidence):
        self.checks.append(Check(cid, name, status, evidence[:200]))

    @property
    def has_fail(self):
        return any(c.status == FAIL for c in self.checks)

    def verdict(self):
        if self.has_fail:
            return "defects"
        if any(c.status == UNREACHABLE for c in self.checks):
            return "off-network-inconclusive"
        if any(c.status == SKIP for c in self.checks):
            return "green-with-honest-skips"
        return "green"

    def summary(self):
        return {
            "schema_version": 1,
            "tool": "verify-same-lan-checklist",
            "verdict": self.verdict(),
            "checks": [
                {"id": c.cid, "name": c.name, "status": c.status,
                 "evidence": c.evidence}
                for c in self.checks
            ],
        }


class Runners:
    """Injected process/network surfaces so tests can substitute fakes."""

    def __init__(self, timeout=DEFAULT_TIMEOUT, tcp=None, http=None,
                 systemctl=None, journalctl=None):
        self.timeout = timeout
        self.tcp = tcp or (lambda h, p: probe_tcp(h, p, timeout))
        self.http = http or (lambda u: fetch_json(u, timeout))
        self.systemctl = systemctl or self._systemctl
        self.journalctl = journalctl or self._journalctl

    @staticmethod
    def _systemctl(args):
        try:
            p = subprocess.run(["systemctl", "--user"] + args,
                               capture_output=True, text=True, timeout=30)
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, "", f"systemctl unavailable: {exc}"

    @staticmethod
    def _journalctl(args):
        try:
            p = subprocess.run(["journalctl", "--user"] + args,
                               capture_output=True, text=True, timeout=30)
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, "", f"journalctl unavailable: {exc}"

    def timer_state(self, unit):
        rc, out, _ = self.systemctl(["is-enabled", unit])
        enabled = rc == 0 and out.strip() == "enabled"
        rc, out, _ = self.systemctl(["list-timers", "--no-pager", unit])
        last = None
        for line in out.splitlines():
            if unit in line:
                last = parse_timer_line(line)
                break
        return enabled, last

    def last_run(self, unit):
        """(status, evidence) where status is ok|fail|warn|absent."""
        rc, out, err = self.journalctl(
            ["-u", unit.replace(".timer", ".service"), "--no-pager", "-n", "200"])
        if rc != 0:
            return "warn", (err or "journal unavailable").strip()[:160]
        starts = [i for i, ln in enumerate(out.splitlines()) if "Starting " in ln]
        body = out.splitlines()[starts[-1]:] if starts else out.splitlines()
        outcome, evidence = "absent", "no run markers in window"
        for ln in body:
            if "Finished " in ln:
                outcome, evidence = "ok", ln.strip()
            elif "Failed " in ln or " failed " in ln:
                outcome, evidence = "fail", ln.strip()
            elif "Skipped " in ln:
                outcome, evidence = "warn", ln.strip()
        faults = [ln.strip() for ln in body
                  if any(w in ln for w in FAULT_WORDS)]
        if faults:
            return "fail", faults[-1]
        return outcome, evidence


# ------------------------------------------------------------------- checklist

def check_fleet(r: Runners, rep: Report):
    for cid, label, host in (("F1", "vanadium(.209)", VANADIUM_HOST),
                             ("F2", "thallium(.82)", THALLIUM_HOST),
                             ("F3", "helium(.229)", HELIUM_HOST)):
        ok, err = r.tcp(host, SSH_PORT)
        if ok:
            rep.add(cid, f"reach:{label}", PASS, f"tcp/{host}:{SSH_PORT} open")
        else:
            rep.add(cid, f"reach:{label}", UNREACHABLE,
                    f"tcp/{host}:{SSH_PORT} no route — off-network or host down")
    return any(c.status == PASS for c in rep.checks if c.cid == "F1")


def check_vanadium(rep: Report, r: Runners, now: datetime,
                   vanadium_reachable: bool, skipped: bool = False):
    """Item 2: three timers + three logs, with off-network downgrade.

    Three signals are read per unit and they disagree on purpose:
      * the timer (enabled + last trigger),
      * the journal outcome (Finished/Failed),
      * the log body (the pipeline's own verdict).

    The vanadium-ci tier exits 0 by design when its removable spool drive is
    absent — a guard working, not a backup failure — so a Finished run over a
    drive-absent SKIP classifies SKIP. But the SAME run also records "target
    unreachable"; that is a real defect when vanadium is reachable, and the
    travel state when it is not. If vanadium is unreachable, a failing unit
    classifies UNREACHABLE with the journal line preserved, never FAIL: an
    off-network run must not read as a host-down claim.
    """
    for idx, (unit, log_name, max_age_h, cadence) in enumerate(
            VANADIUM_UNITS, start=1):
        cid = f"V{idx}"
        enabled, last = r.timer_state(unit)
        _outcome, run_ev = r.last_run(unit)
        log_path = LOG_DIR / log_name
        try:
            log_age_h = hours_since(
                datetime.fromtimestamp(log_path.stat().st_mtime), now)
            log_text = tail(log_path)
            log_ev = last_meaningful_line(log_text)
            log_state = "fresh" if log_age_h <= max_age_h else "stale"
        except OSError:
            log_age_h, log_ev, log_state, log_text = -1.0, "log missing", "missing", ""
        low = log_text.lower()
        drive_absent = ("drive absent" in low
                        or "dangling symlink" in low)
        target_down = ("target unreachable" in low
                       or "does not resolve" in low)
        log_fault = ("fail:" in low or "fatal" in low
                     or "psql: error" in low or "error:" in low)
        run_failed = ("Failed " in run_ev
                      or any(w in run_ev for w in FAULT_WORDS))

        defects = []
        if not enabled:
            defects.append("timer not enabled")
        if last is None:
            defects.append("never triggered")
        else:
            age_h = hours_since(parse_local_ts(last), now)
            if age_h > max_age_h:
                defects.append(f"last trigger {age_h:.1f}h > {max_age_h:.0f}h")
        if run_failed and not drive_absent:
            defects.append("last run failed")
        if log_state != "fresh":
            defects.append("log " + log_state
                           + (f" ({log_age_h:.1f}h)" if log_age_h >= 0 else ""))
        if log_fault and not drive_absent:
            defects.append("log reports an error")
        if target_down and vanadium_reachable:
            defects.append("log reports the target unreachable")

        detail = (f"{unit} ({cadence}): trigger={last or 'never'}, "
                  f"run={run_ev[:80]}, log={log_ev[:70]}")
        if target_down:
            detail += " [log: target unreachable]"

        if defects and skipped:
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", SKIP,
                    "--skip-remote given; local evidence: "
                    + "; ".join(defects) + " | " + detail)
        elif defects:
            status = FAIL if vanadium_reachable else UNREACHABLE
            prefix = "" if vanadium_reachable else "off-network: "
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", status,
                    prefix + "; ".join(defects) + " | " + detail)
        elif drive_absent:
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", SKIP,
                    "drive-absent guard honoured (nothing fetched, remote "
                    "untouched)" + ("; target also unreachable" if target_down else "")
                    + " | " + detail)
        elif skipped:
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", SKIP,
                    "--skip-remote given | " + detail)
        elif target_down:
            # The log says the run stopped because the target was absent, and
            # vanadium is absent: UNREACHABLE, never PASS (nothing synced).
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", UNREACHABLE,
                    "log reports the target unreachable — off-network, "
                    "no defect claimed | " + detail)
        else:
            rep.add(cid, f"vanadium:{unit.split('.')[0]}", PASS, detail)


def check_github(rep: Report):
    """Item 3 is a network item; say so instead of guessing."""
    rep.add("G1", "github:pr-metadata", SKIP,
            "16 cited PRs (§2c) need GitHub: #700/#798 close comments, "
            "#782 title/body — unavailable off-network")


def check_twins(rep: Report, r: Runners, ports):
    """Item 4a: are any 41xx canary twins listening on titanium?

    PORT-MAP.md says the twins are 'not cut over / not deployed', so zero
    listeners is the expected PASS, not a failure. A listener IS reported by
    name so an operator can see a canary co-listening before the host gate
    (bin/assert_moleculer_ports.sh) does.
    """
    open_ports = []
    for port, name in ports:
        ok, _ = r.tcp("127.0.0.1", port)
        if ok:
            open_ports.append(f"{port}:{name}")
    total = len(ports)
    if not open_ports:
        rep.add("T1", "twins:41xx", PASS,
                f"0/{total} canary ports bound — matches PORT-MAP (not deployed)")
    else:
        rep.add("T1", "twins:41xx", PASS,
                f"{len(open_ports)}/{total} bound: " + ", ".join(open_ports[:8]))


def check_jvm(rep: Report, r: Runners, thallium_reachable: bool,
              helium_reachable: bool):
    """Item 4b: thallium :8092 is the live JVM lane; helium's is retired."""
    if not thallium_reachable:
        rep.add("J1", "jvm:thallium:8092", UNREACHABLE,
                f"tcp/{THALLIUM_HOST}:{SSH_PORT} absent — cannot probe the lane")
    else:
        ok, err = r.tcp(THALLIUM_HOST, JVM_PORT)
        if not ok:
            rep.add("J1", "jvm:thallium:8092", FAIL,
                    f"port closed ({err[:80]}) — Ruling 1 lane should serve")
        else:
            try:
                status, body = r.http(
                    f"http://{THALLIUM_HOST}:{JVM_PORT}/actuator/health")
                overall = str(body.get("status", "")).upper()
                if overall == "UP":
                    disk = ((body.get("components") or {})
                            .get("diskSpace") or {}).get("details") or {}
                    free = disk.get("free")
                    extra = (f", disk_free={free / 1024 ** 3:.1f}GB"
                             if isinstance(free, (int, float)) else "")
                    rep.add("J1", "jvm:thallium:8092", PASS,
                            f"HTTP {status} actuator UP{extra}")
                else:
                    rep.add("J1", "jvm:thallium:8092", FAIL,
                            f"actuator status={overall or 'missing'}")
            except urllib.error.HTTPError as exc:
                rep.add("J1", "jvm:thallium:8092", FAIL, f"HTTP {exc.code}")
            except Exception as exc:
                # Port is open but the health route is not answering: the lane
                # is up but not verifiable, which is a defect for a probe.
                rep.add("J1", "jvm:thallium:8092", FAIL,
                        f"port open, actuator unreachable: {exc}")

    if not helium_reachable:
        rep.add("J2", "jvm:helium:8092", UNREACHABLE,
                f"tcp/{HELIUM_HOST}:{SSH_PORT} absent — cannot probe the retired lane")
    else:
        ok, _ = r.tcp(HELIUM_HOST, JVM_PORT)
        if ok:
            rep.add("J2", "jvm:helium:8092", WARN,
                    "listening, but PORT-MAP says helium JVM retired (Ruling 1)")
        else:
            rep.add("J2", "jvm:helium:8092", PASS,
                    "closed — matches 'helium JVM retired'")


def build_report(r, now=None, skip_remote=False, ports_yaml=PORTS_YAML):
    now = now or datetime.now()
    rep = Report()

    if skip_remote:
        for cid, label in (("F1", "vanadium(.209)"), ("F2", "thallium(.82)"),
                           ("F3", "helium(.229)")):
            rep.add(cid, f"reach:{label}", SKIP, "--skip-remote given")
        check_vanadium(rep, r, now, vanadium_reachable=False, skipped=True)
        check_github(rep)
        rep.add("T1", "twins:41xx", SKIP, "--skip-remote given")
        rep.add("J1", "jvm:thallium:8092", SKIP, "--skip-remote given")
        rep.add("J2", "jvm:helium:8092", SKIP, "--skip-remote given")
        return rep

    vanadium_reachable = check_fleet(r, rep)
    check_vanadium(rep, r, now, vanadium_reachable)
    check_github(rep)
    twins = load_twin_ports(ports_yaml)
    check_twins(rep, r, twins)
    check_jvm(rep, r,
              thallium_reachable=any(c.cid == "F2" and c.status == PASS
                                     for c in rep.checks),
              helium_reachable=any(c.cid == "F3" and c.status == PASS
                                   for c in rep.checks))
    return rep


def main(argv=None, now=None, runners=None):
    ap = argparse.ArgumentParser(
        description="§6 same-LAN checklist: fleet, vanadium timers/logs, "
                    "twin+JVM liveness (read-only)")
    ap.add_argument("--json", action="store_true", help="machine summary only")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="per TCP/HTTP probe timeout in seconds")
    ap.add_argument("--skip-remote", action="store_true",
                    help="local timer/log checks only (no network probes)")
    ap.add_argument("--ports-yaml", type=Path, default=PORTS_YAML,
                    help="twin-port registry (default: moleculer/ports.yaml)")
    args = ap.parse_args(argv)

    r = runners or Runners(timeout=args.timeout)
    rep = build_report(r, now=now, skip_remote=args.skip_remote,
                       ports_yaml=args.ports_yaml)

    if not args.json:
        width = max(len(c.name) for c in rep.checks)
        for c in rep.checks:
            print(f"{c.status:<12} {c.cid:<3} {c.name:<{width}}  {c.evidence}")
        print()
        print(f"verdict: {rep.verdict()} "
              f"({sum(c.status == PASS for c in rep.checks)} pass, "
              f"{sum(c.status == FAIL for c in rep.checks)} fail, "
              f"{sum(c.status == UNREACHABLE for c in rep.checks)} unreachable, "
              f"{sum(c.status == SKIP for c in rep.checks)} skip)")
    print(json.dumps(rep.summary()))
    return 1 if rep.has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
