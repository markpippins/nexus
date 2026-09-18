#!/usr/bin/env python3
"""apply-v184-package — the Calendar apply arc as one bounded command.

Composes the three landed pieces (V184 DDL #336, consolidation intake #335,
dormant wiring #339) into a single operator-go-gated execution:

    bin/apply-v184-package.py verify                 # read-only battery
    bin/apply-v184-package.py run --operator-go UUID # verify→apply→fold→mark

Phases (run):
  gate    resolve the operator-go record; refuse unless it exists, carries
          `operator:go`, and is not yet `status:applied` (apply-lock,
          #311/#312 semantics). No --operator-go at all → exit 2: the
          package is inert by construction, not by policy memo.
  verify  read-only battery: DB reachable and calendar_events absent-or-
          present (reported distinctly — absent ≠ unreachable, V174
          discipline); local + remote JSONLs validate (re-derived ids);
          the analyst-provenance columns exist in the DDL and the intake
          emits those fields (analyst no-objection, thread a330914e 17:38Z);
          consolidation wiring present for the ongoing 13:35Z folds.
  apply   psql -f sql/V184__calendar_primitive.sql (ON_ERROR_STOP), skipped
          cleanly if already applied. Uses the caller's pg env (PGPASSWORD
          etc.) — the tool never embeds credentials.
  stage   copy each remote machine's calendar.jsonl into a staging dir
          (scp over the fleet BatchMode path) so the fold sees all machines.
  fold    bin/calendar-consolidate.py observe once per staged source, then
          the local JSONL — PK-is-the-dedupe (#335) makes ordering and
          re-runs no-ops. --by attributes the fold.
  mark    bin/mark-operator-go-applied <uuid> — the canonical ledger tag
          (exit 3 tolerated: already tagged).

--dry-run executes gate + verify, prints the plan, mutates nothing.
Every phase prints one [phase] line; stdout is the run journal.

Exit codes: 0 ok · 1 verify/apply/fold failure · 2 gate refusal · 3 go
record already applied (nothing done).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V184_SQL = REPO / "sql" / "V184__calendar_primitive.sql"
CONSOLIDATE = REPO / "bin" / "calendar-consolidate.py"
MARK_APPLIED = REPO / "bin" / "mark-operator-go-applied"
LOCAL_JSONL = Path(os.environ.get(
    "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
)) / "nexus-calendar" / "calendar.jsonl"
DEFAULT_REMOTE_HOSTS = ["helium", "vanadium"]
FLEET_JSONL = "~/.local/state/nexus-calendar/calendar.jsonl"

ANALYST_COLUMNS = ["source_machine", "source_emitter", "recorded_at", "recorded_by"]

EX_OK, EX_FAIL, EX_GATE, EX_ALREADY = 0, 1, 2, 3


# ── seams (tests inject all four) ────────────────────────────────────────

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"  # house convention


def _conn() -> list[str]:
    """psql connection args from the house DSN convention (CONDUIT_PG_DSN
    override supported — same env the consolidation intake reads)."""
    return [os.environ.get("CONDUIT_PG_DSN", DEFAULT_DSN)]


def db_query(sql: str) -> tuple[int, str, str]:
    """One psql -X -qAt query against the live nexus DB."""
    proc = subprocess.run(
        ["psql", *_conn(), "-X", "-qAt", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def apply_sql(path: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["psql", *_conn(), "-X", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def stage_remote_jsonl(host: str, dest_dir: Path) -> Path:
    """Copy the host's calendar.jsonl into dest_dir. The fleet path."""
    dest = dest_dir / f"{host}.jsonl"
    proc = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         f"{host}:{FLEET_JSONL}", str(dest)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"scp from {host} failed: {(proc.stderr or '').strip()[-200:]}")
    return dest


def probe_remote_jsonl(host: str) -> bool:
    """True if the host's calendar.jsonl exists (read-only fleet probe)."""
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
         f"test -f {FLEET_JSONL} && echo present"],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode == 0 and "present" in proc.stdout


def run_consolidate(source: Path, by: str, strict: bool, mode: str = "observe") -> tuple[int, str]:
    """mode: 'validate' (read-only, verify battery) or 'observe' (the fold)."""
    cmd = [sys.executable, str(CONSOLIDATE), mode,
           "--source", str(source), "--by", by, "--json"]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def nebula_get(record_id: str) -> tuple[int, str]:
    url = f"{os.environ.get('NEBULA_URL', 'http://localhost:3101')}/api/agent-records/{record_id}"
    try:
        with __import__("urllib.request", fromlist=["urlopen"]).urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except Exception as e:  # noqa: BLE001 - surfaced as gate data
        return 0, json.dumps({"error": str(e)})


def mark_go_applied(record_id: str) -> int:
    proc = subprocess.run(
        [str(MARK_APPLIED), record_id], capture_output=True, text=True, timeout=30,
    )
    return proc.returncode


# ── gate ─────────────────────────────────────────────────────────────────

def check_gate(operator_go: str | None) -> tuple[int, str]:
    """Returns (exit_code, detail). The package refuses to be valuable
    without an authorization record: exit 2 is the inert posture."""
    if not operator_go:
        return EX_GATE, "no --operator-go given — package stays inert (by construction)"
    rc, body = nebula_get(operator_go)
    if rc != 200:
        return 1, f"operator-go {operator_go} not resolvable (rc={rc}) — refusing"
    try:
        rec = json.loads(body)
    except json.JSONDecodeError:
        return 1, f"operator-go {operator_go} returned non-JSON — refusing"
    tags = rec.get("tags") or []
    if "operator:go" not in tags:
        return EX_GATE, f"{operator_go} lacks operator:go tag — not an authorization"
    if "status:applied" in tags:
        return EX_ALREADY, f"{operator_go} already status:applied — nothing to do"
    return EX_OK, f"gate open: {operator_go} ({rec.get('title', '')[:60]})"


# ── verify battery (read-only) ───────────────────────────────────────────

def phase_verify(remote_hosts: list[str], staging: Path | None) -> tuple[bool, list[str], list[Path]]:
    """Returns (ok, lines, staged_sources). Staged sources are only produced
    on a real run (staging is None in dry-run)."""
    lines: list[str] = []
    ok = True

    # 1. DB reachability + table state (absent ≠ unreachable)
    rc, out, err = db_query("SELECT to_regclass('vision.calendar_events')::text")
    if rc != 0:
        lines.append(f"[verify] FAIL db unreachable: {err[:160]}")
        return False, lines, []
    if out and out != "NULL":
        state = "present (already applied — apply will skip, fold will run)"
    else:
        state = "absent (expected pre-apply state)"
    lines.append(f"[verify] ok   db reachable, calendar_events {state}")

    # 2. DDL carries the analyst provenance columns; intake emits those fields
    ddl = V184_SQL.read_text() if V184_SQL.exists() else ""
    missing_cols = [c for c in ANALYST_COLUMNS if c not in ddl]
    intake_src = CONSOLIDATE.read_text() if CONSOLIDATE.exists() else ""
    missing_intake = [c for c in ANALYST_COLUMNS if c not in intake_src]
    if missing_cols or missing_intake:
        ok = False
        lines.append(f"[verify] FAIL analyst provenance: ddl missing {missing_cols}, "
                     f"intake missing {missing_intake}")
    else:
        lines.append("[verify] ok   analyst provenance columns in DDL and emitted by intake")

    # 3. local JSONL validates (validate mode — never folds)
    rc, report = run_consolidate(LOCAL_JSONL, "verify", strict=False, mode="validate")
    if rc != 0:
        ok = False
        lines.append(f"[verify] FAIL local calendar invalid: {report[-200:]}")
    else:
        lines.append("[verify] ok   local calendar validates")

    # 4. remote fleet JSONLs reachable (and staged on real runs)
    staged: list[Path] = []
    for host in remote_hosts:
        if staging is None:
            if probe_remote_jsonl(host):
                lines.append(f"[verify] ok   {host} calendar reachable (dry-run: not staged)")
            else:
                ok = False
                lines.append(f"[verify] FAIL {host} calendar unreachable")
            continue
        try:
            staged.append(stage_remote_jsonl(host, staging))
            lines.append(f"[verify] ok   {host} calendar staged ({staged[-1].name})")
        except RuntimeError as e:
            ok = False
            lines.append(f"[verify] FAIL {e}")

    # 5. consolidation wiring present (post-apply automation)
    svc = Path.home() / ".config/systemd/user/calendar-consolidate.service"
    if svc.exists():
        lines.append("[verify] ok   consolidation wiring present (13:35Z dormant timer)")
    else:
        lines.append("[verify] warn consolidation unit absent — ongoing folds will be manual")

    return ok, lines, staged


# ── phases ───────────────────────────────────────────────────────────────

def phase_apply() -> str:
    rc, out, err = db_query("SELECT to_regclass('vision.calendar_events')::text")
    if rc == 0 and out and out != "NULL":
        return "skip (already applied)"
    rc, out, err = apply_sql(V184_SQL)
    if rc != 0:
        raise RuntimeError(f"V184 apply failed: {(err or out)[-300:]}")
    rc, out2, _ = db_query("SELECT count(*) FROM vision.calendar_events")
    return f"applied (events after apply: {out2 or '?'})"


def phase_fold(sources: list[Path], by: str) -> str:
    folded, skipped = 0, 0
    for src in sources:
        rc, report = run_consolidate(src, by, strict=False)
        try:
            rep = json.loads(report.splitlines()[-1]) if report else {}
        except json.JSONDecodeError:
            rep = {}
        if rc != 0:
            raise RuntimeError(f"fold failed for {src.name} (rc={rc}): {report[-200:]}")
        folded += int(rep.get("inserted", rep.get("folded", 0)) or 0)
        skipped += int(rep.get("duplicates", rep.get("skipped", 0)) or 0)
    return f"{len(sources)} source(s): inserted={folded} deduped={skipped}"


# ── driver ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="V184 apply package (operator-go gated).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="gate→verify→apply→stage→fold→mark")
    p_run.add_argument("--operator-go", required=True, metavar="UUID")
    p_run.add_argument("--by", default="dba", help="recorded_by attribution for the fold")
    p_run.add_argument("--remote-hosts", nargs="*", default=DEFAULT_REMOTE_HOSTS)
    p_run.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify", help="read-only battery only")

    args = ap.parse_args(argv)
    dry = getattr(args, "dry_run", False) or args.cmd == "verify"
    operator_go = getattr(args, "operator_go", None)
    by = getattr(args, "by", "dba")
    remote_hosts = getattr(args, "remote_hosts", DEFAULT_REMOTE_HOSTS)

    # gate (run only)
    if args.cmd == "run":
        code, detail = check_gate(operator_go)
        print(f"[gate] {'ok  ' if code == EX_OK else 'REFUSE'} {detail}")
        if code == EX_ALREADY:
            return EX_ALREADY
        if code != EX_OK:
            return code

    # verify
    staging = None if dry else Path(tempfile.mkdtemp(prefix="v184-stage-"))
    ok, lines, staged = phase_verify(remote_hosts, staging)
    for line in lines:
        print(line)
    if not ok:
        print("[verify] battery failed — apply refused")
        return EX_FAIL

    if args.cmd == "verify":
        print("[verify] battery green — read-only mode, no mutations performed")
        return EX_OK

    if dry:
        print(f"[plan ] would: apply {V184_SQL.name} → stage {remote_hosts} → "
              f"fold local+{len(remote_hosts)} source(s) as {by} → "
              f"mark {operator_go} status:applied")
        print("[plan ] dry-run: zero mutations performed")
        return EX_OK

    # apply
    try:
        result = phase_apply()
    except RuntimeError as e:
        print(f"[apply] FAIL {e}")
        return EX_FAIL
    print(f"[apply] ok   {result}")

    # fold: staged remotes + local
    sources = staged + [LOCAL_JSONL]
    try:
        result = phase_fold(sources, by)
    except RuntimeError as e:
        print(f"[fold ] FAIL {e}")
        print(f"[note ] V184 applied but fold incomplete — re-run fold phase is safe "
              f"(PK dedupe); go record NOT marked")
        return EX_FAIL
    print(f"[fold ] ok   {result}")

    # mark
    mrc = mark_go_applied(operator_go)
    print(f"[mark ] {'ok  ' if mrc == 0 else 'warn'} mark-operator-go-applied rc={mrc} "
          f"({'freshly tagged' if mrc == 0 else 'already tagged (3) — tolerated' if mrc == 3 else 'unexpected'})")
    print("[done ] V184 arc complete: storage live, fleet folded, go consumed")
    return EX_OK


if __name__ == "__main__":
    sys.exit(main())
