#!/usr/bin/env python3
"""calendar-fleet-rollout — one command, every machine on the calendar.

Encodes the three proven manual extensions (titanium live, vanadium 2026-09-18,
helium 2026-09-18) into a single idempotent invocation so machine #4 is:

    python3 bin/calendar-fleet-rollout.py --hosts newbox \
        --probe-provision newbox:<kind>:<probe-url>

Per host the pipeline is exactly the sequence validated by hand:

  1. REACH     ssh BatchMode probe (R15: reality-check before operating)
  2. AUDIT     pre-pull inventory — root-owned artifacts (admin-notes pitfall
               746e6169: they wedge ff pulls), dirty count, ahead/behind
  3. SYNC      git pull --ff-only <remote> main — remote auto-detected
               (origin preferred; helium's legacy `github` handled), refused
               unless a pure fast-forward
  4. INSTALL   calendar-emit.py install --write + daemon-reload, then VERIFY
               the template unit actually exists (helium lesson: exit code
               alone lies — verify installed state)
  5. PROBE     optional per-host rhythm provisioning: <kind>-health timer with
               the OnSuccess drop-in (sonar-health pattern). Hosts without a
               --probe-provision spec are synced+installed; their rhythm is a
               design decision, never invented by this tool.
  6. VERIFY    trigger the probe once (or a one-shot emit for probe-less
               hosts) and assert the JSONL gained a machine=<host> event —
               first emission proven, not assumed
  7. LINGER    reported, never forced (needs one operator command on some boxes)

Every remote operation goes through run_remote() — the seam tests inject.
Idempotent throughout: re-running on a current host is a clean no-op plus an
honest verification event. No sudo anywhere; the JSON report is the receipt.

Usage:
  calendar-fleet-rollout.py --hosts helium vanadium
  calendar-fleet-rollout.py --hosts newbox --probe-provision newbox:ollama:http://localhost:11434/api/version
  calendar-fleet-rollout.py --hosts helium --dry-run
  calendar-fleet-rollout.py --hosts helium --report /tmp/fleet.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field

REPO = "~/dev/nexus"
SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
EMITTER = f"{REPO}/bin/calendar-emit.py"
USER_UNITS = "~/.config/systemd/user"


# ── remote seam ──────────────────────────────────────────────────────────

def run_remote(host: str, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run cmd on host via ssh. The seam: tests replace this."""
    try:
        proc = subprocess.run(
            SSH_BASE + [host, cmd], capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


# ── helpers ──────────────────────────────────────────────────────────────

def sh(cmd: str) -> str:
    """Quote for safe embedding inside an ssh command string."""
    return "'" + cmd.replace("'", "'\\''") + "'"


@dataclass
class Step:
    name: str
    status: str  # ok | skip | fail
    detail: str = ""


@dataclass
class HostResult:
    host: str
    ok: bool = True
    steps: list[Step] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append(Step(name, status, detail))
        if status == "fail":
            self.ok = False

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "ok": self.ok,
            "steps": [s.__dict__ for s in self.steps],
        }


# ── pipeline steps ───────────────────────────────────────────────────────

def step_reach(res: HostResult) -> bool:
    try:
        rc, out, err = run_remote(res.host, "echo ok")
    except subprocess.TimeoutExpired:  # defense in depth: the seam normally
        rc, out, err = 124, "", "timeout"  # maps this itself; never propagate
    if rc == 0 and out.strip() == "ok":
        res.add("reach", "ok")
        return True
    res.add("reach", "fail", (err or out).strip()[:200] or "no answer (BatchMode)")
    return False


def step_audit(res: HostResult, remote: str) -> bool:
    """Pre-pull inventory. Returns False only on hard refusal (no repo,
    dirty tree, or root-owned TRACKED paths — those wedge ff pulls).
    Root-owned UNTRACKED artifacts (runtime logs, docker mounts) are a
    warning, not a refusal: checkout never touches them."""
    rc, out, _ = run_remote(
        res.host,
        f"cd {REPO} && git rev-parse --is-inside-work-tree && "
        f"git status --porcelain | wc -l && "
        f"git rev-list --count HEAD..{sh(remote)}/main 2>/dev/null; "
        f"git rev-list --count {sh(remote)}/main..HEAD 2>/dev/null; "
        f"find {REPO} -user root 2>/dev/null | head -20 | "
        f"while IFS= read -r p; do "
        f"if git ls-files --error-unmatch -- \"$p\" >/dev/null 2>&1; "
        f"then echo \"TRACKED $p\"; else echo \"untracked $p\"; fi; done",
    )
    if rc != 0:
        res.add("audit", "fail", "not a git checkout (or unreadable)")
        return False
    lines = [l.strip() for l in out.strip().splitlines()]
    # order: is-inside-work-tree, dirty, behind, ahead, then classifications
    if lines[0] != "true":
        res.add("audit", "fail", f"unexpected audit output: {lines[:2]}")
        return False
    dirty, behind, ahead = lines[1], lines[2], lines[3]
    tracked = [l.split(" ", 1)[1] for l in lines[4:] if l.startswith("TRACKED ")]
    untracked = [l.split(" ", 1)[1] for l in lines[4:] if l.startswith("untracked ")]
    detail = f"dirty={dirty} behind={behind} ahead={ahead}"
    if untracked:
        detail += f" root-owned-untracked={len(untracked)} (warn only)"
    res.add("audit", "ok", detail)
    if tracked:
        res.add("audit-root-owned", "fail",
                f"{len(tracked)} root-owned TRACKED path(s) — ff pull may wedge "
                f"(admin-notes 746e6169): " + "; ".join(tracked[:3]))
        return False
    if dirty != "0":
        res.add("audit-dirty", "fail",
                f"{dirty} dirty entries — refusing to pull over uncommitted state")
        return False
    return True


def pick_remote(res: HostResult) -> str | None:
    """Auto-detect the sync remote: origin preferred, else any remote whose
    fetch URL points at the nexus GitHub repo (helium's legacy `github`)."""
    rc, out, _ = run_remote(res.host, f"cd {REPO} && git remote -v")
    if rc != 0 or not out.strip():
        res.add("remote", "fail", "no git remotes configured")
        return None
    candidates = []
    for line in out.splitlines():
        if not line.endswith("(fetch)"):
            continue
        name, url = line.split(maxsplit=1)
        candidates.append((name, url))
    named = [n for n, u in candidates if n == "origin"]
    if named:
        res.add("remote", "ok", "origin")
        return "origin"
    github = [n for n, u in candidates if "github.com" in u or "nexus" in u]
    if github:
        res.add("remote", "ok", f"{github[0]} (non-standard name, URL-matched)")
        return github[0]
    res.add("remote", "fail", "no origin and no URL match among: "
            + ", ".join(f"{n}={u}" for n, u in candidates))
    return None


def step_sync(res: HostResult, remote: str) -> None:
    rc, out, err = run_remote(
        res.host, f"cd {REPO} && git fetch {sh(remote)} -q && git pull --ff-only {sh(remote)} main"
    )
    combined = (out + err).strip()
    if rc != 0:
        res.add("sync", "fail", combined[-300:] or f"pull --ff-only {remote} main failed")
        return
    if "Already up to date" in combined or "Already up-to-date" in combined:
        res.add("sync", "skip", "already current")
        return
    rc2, head, _ = run_remote(res.host, f"cd {REPO} && git rev-parse --short HEAD && git status --porcelain | wc -l")
    lines = head.strip().splitlines()
    dirty_after = lines[1].strip() if len(lines) > 1 else "?"
    if dirty_after != "0":
        res.add("sync", "fail", f"pull reported success but {dirty_after} dirty entries remain")
        return
    res.add("sync", "ok", f"fast-forwarded to {lines[0].strip()}")


def step_install(res: HostResult) -> None:
    rc, out, err = run_remote(res.host, f"cd {REPO} && python3 {EMITTER} install --write && systemctl --user daemon-reload")
    if rc != 0:
        res.add("install", "fail", (err or out).strip()[-300:])
        return
    # VERIFY installed state, not exit code (helium lesson).
    rc2, units, _ = run_remote(
        res.host, "systemctl --user list-unit-files 'calendar-emit@*' --no-legend 2>/dev/null | wc -l"
    )
    if rc2 != 0 or not units.strip() or units.strip() == "0":
        res.add("install", "fail",
                f"install exited 0 but template unit missing (list count={units.strip()!r})")
        return
    rc3, dropins, _ = run_remote(
        res.host,
        f"ls {USER_UNITS}/*.service.d/calendar-emit-on-success.conf 2>/dev/null | wc -l",
    )
    n = dropins.strip() if rc3 == 0 else "?"
    res.add("install", "ok", f"template present, drop-ins wired: {n} timer(s) emitting")


def probe_unit_names(kind: str) -> tuple[str, str]:
    return f"{kind}-health.service", f"{kind}-health.timer"


def step_probe(res: HostResult, spec: dict | None) -> None:
    """Provision the host's rhythm probe if specified and absent."""
    if not spec:
        res.add("probe", "skip", "no --probe-provision for this host")
        return
    kind, url = spec["kind"], spec["url"]
    service, timer = probe_unit_names(kind)
    rc, exists, _ = run_remote(res.host, f"systemctl --user list-unit-files '{timer}' --no-legend 2>/dev/null | wc -l")
    if rc == 0 and exists.strip() not in ("0", ""):
        res.add("probe", "skip", f"{timer} already provisioned")
        return
    units = (
        f"mkdir -p {USER_UNITS} && "
        # service: probe the workload
        f"printf '[Unit]\\nDescription={kind} health probe (fleet calendar)\\n\\n[Service]\\n"
        f"Type=oneshot\\nExecStart=/usr/bin/curl -sf --max-time 10 {url}\\n' "
        f"> {USER_UNITS}/{service} && "
        # timer: every 15 min, persistent
        f"printf '[Unit]\\nDescription=probe {kind} every 15 min (fleet calendar)\\n\\n[Timer]\\n"
        f"OnCalendar=*:0/15\\nPersistent=true\\nUnit={service}\\n\\n[Install]\\n"
        f"WantedBy=timers.target\\n' > {USER_UNITS}/{timer} && "
        # drop-in: OnSuccess chain into the emitter
        f"mkdir -p {USER_UNITS}/{service}.d && "
        f"printf '[Unit]\\nOnSuccess=calendar-emit@{timer}.service\\n' "
        f"> {USER_UNITS}/{service}.d/calendar-emit-on-success.conf && "
        "systemctl --user daemon-reload && "
        f"systemctl --user enable --now {timer}"
    )
    rc, out, err = run_remote(res.host, units)
    if rc != 0:
        res.add("probe", "fail", (err or out).strip()[-300:])
        return
    rc2, state, _ = run_remote(res.host, f"systemctl --user is-enabled {timer} 2>&1")
    if state.strip() != "enabled":
        res.add("probe", "fail", f"wrote units but {timer} not enabled ({state.strip()}) — "
                "exit code lied again; verify installed state")
        return
    res.add("probe", "ok", f"{timer} provisioned (15-min cadence, OnSuccess→emitter)")


def step_verify(res: HostResult, spec: dict | None) -> None:
    """Prove first emission: JSONL must gain a machine=<host> event."""
    def count() -> tuple[str, str]:
        rc, out, _ = run_remote(
            res.host,
            f"wc -l < ~/.local/state/nexus-calendar/calendar.jsonl 2>/dev/null || echo 0",
        )
        # emitter serializes with json.dumps defaults: '"machine": "x"'
        # (space after colon). grep -c prints 0 on no match; empty output
        # only when the file itself is missing.
        rc2, machine_ok, _ = run_remote(
            res.host,
            f"grep -c '\"machine\": \"{res.host}\"' "
            f"~/.local/state/nexus-calendar/calendar.jsonl 2>/dev/null",
        )
        return (out.strip() if rc == 0 else "?"), (machine_ok.strip() or "0")

    before, _ = count()
    if spec:
        kind = spec["kind"]
        service, _ = probe_unit_names(kind)
        rc, out, err = run_remote(res.host, f"systemctl --user start {service}")
        if rc != 0:
            res.add("verify", "fail", f"probe trigger failed: {(err or out).strip()[-200:]}")
            return
    else:
        payload = sh('{"verify":true}')  # single-quoted for the remote shell
        rc, out, err = run_remote(
            res.host,
            f"cd {REPO} && python3 {EMITTER} emit --kind occurred "
            f"--emitter fleet-rollout:verify --title 'fleet rollout verification event' "
            f"--payload {payload}",
        )
        if rc != 0:
            res.add("verify", "fail", f"verification emit failed: {(err or out).strip()[-200:]}")
            return
    after, machine_count = count()
    try:
        grew = int(after) > int(before) and int(machine_count) >= 1
    except ValueError:
        grew = False
        after, machine_count = after, machine_count
    if grew:
        res.add("verify", "ok", f"events {before}→{after}, machine-attributed={machine_count}")
    else:
        res.add("verify", "fail",
                f"no machine={res.host} event after trigger (before={before} after={after} "
                f"machine_count={machine_count})")


def step_linger(res: HostResult) -> None:
    rc, out, _ = run_remote(res.host, "loginctl show-user codex -p Linger 2>/dev/null")
    linger = out.strip().split("=", 1)[-1] if rc == 0 and out.strip() else "unknown"
    if linger == "yes":
        res.add("linger", "ok", "enabled — timers fire unattended")
    else:
        res.add("linger", "skip",
                f"Linger={linger} — timers fire only while a session exists. "
                "Operator one-liner if unattended coverage is wanted: "
                f"loginctl enable-linger codex (on {res.host})")


# ── driver ───────────────────────────────────────────────────────────────

def rollout_host(host: str, spec: dict | None) -> HostResult:
    res = HostResult(host=host)
    if not step_reach(res):
        return res
    remote = pick_remote(res)  # audit needs the remote's tracking ref
    if not remote:
        return res
    if not step_audit(res, remote):
        return res
    step_sync(res, remote)
    if res.ok:
        step_install(res)
    if res.ok:
        step_probe(res, spec)
        step_verify(res, spec)
        step_linger(res)
    return res


def parse_probe_specs(pairs: list[str]) -> dict[str, dict]:
    """--probe-provision host:kind:url (repeatable) → {host: {kind, url}}."""
    specs: dict[str, dict] = {}
    for pair in pairs:
        parts = pair.split(":", 2)
        if len(parts) != 3 or not all(parts):
            raise SystemExit(f"--probe-provision must be host:kind:url, got: {pair}")
        host, kind, url = parts
        specs[host] = {"kind": kind, "url": url}
    return specs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="One-command calendar rollout across machines.")
    ap.add_argument("--hosts", nargs="+", required=True, help="machines to roll out")
    ap.add_argument("--probe-provision", action="append", default=[],
                    metavar="HOST:KIND:URL", help="provision <kind>-health probe per host (repeatable)")
    ap.add_argument("--report", help="write the JSON report to this path")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the plan, touch nothing")
    args = ap.parse_args(argv)

    specs = parse_probe_specs(args.probe_provision)
    unknown = set(specs) - set(args.hosts)
    if unknown:
        ap.error(f"--probe-provision for hosts not in --hosts: {sorted(unknown)}")

    if args.dry_run:
        print(json.dumps({
            "mode": "dry-run",
            "hosts": args.hosts,
            "probe_specs": specs,
            "pipeline": ["reach", "remote", "audit", "sync", "install", "probe", "verify", "linger"],
            "note": "no ssh performed; add --report to persist results of a real run",
        }, indent=2))
        return 0

    results = [rollout_host(h, specs.get(h)) for h in args.hosts]
    report = {
        "tool": "calendar-fleet-rollout",
        "hosts_ok": sum(1 for r in results if r.ok),
        "hosts_total": len(results),
        "results": [r.as_dict() for r in results],
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        with open(args.report, "w") as fh:
            fh.write(text + "\n")
    return 0 if report["hosts_ok"] == report["hosts_total"] else 1


if __name__ == "__main__":
    sys.exit(main())
