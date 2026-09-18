#!/usr/bin/env python3
"""freebuff-boot.py — Freebuff-conformance boot shim (one-shot session boot).

Freebuff (and similar general-purpose harnesses) do not mount nexus MCP tools
and do not execute the R13/R16/R17/R18 session rituals. This shim closes that
gap voluntarily: one command any agent can run at session start that performs
the conformant boot and REPORTS degraded services instead of failing silently.

What it does, in order:

  1. Pre-flight  — probe every dependency (nebula-mcp, assembly-srv, timeclock,
                   tackle-mcp) with a cheap read-only request.
  2. Role lease  — renew the role's ACTIVE lease (role_lease_renew) or issue a
                   new one (role_lease_issue) via nebula-mcp. --lease skip opts
                   out; --dry-run computes the decision without mutating.
  3. Clock-in    — POST /clock-in on the timeclock (R13), unless --dry-run.
  4. Inbox       — nebula_get_inbox via nebula-mcp (R17); --update-pointer
                   advances the stored pointer to the newest record.
  5. Forum scan  — issues-and-open-questions + to-do threads via assembly-srv
                   (R13/R16), plus the latest change-log entries (R18).
  6. Procedures  — memory_get_procedures via tackle-mcp; count + top slugs.

Degraded-mode philosophy: a down OPTIONAL dependency (tackle-mcp, timeclock,
assembly forums) is reported as `degraded` and the boot continues; governance
here is protocol-portable, so surface the gap and let the operator decide.
Exit codes: 0 = ok (degraded allowed), 1 = strict mode or a requested mutation
failed, 2 = usage error.

Full documentation: docs/freebuff-boot.md (flags, boot phases, degraded mode,
service preconditions, known caveats).

Usage:
    freebuff-boot.py --role <role> [--model <model>] [--channel <channel>]
                     [--ttl <seconds>] [--budget <units>]
                     [--lease auto|skip] [--update-pointer] [--limit N]
                     [--dry-run] [--strict] [--json] [-h]

Env overrides (all optional, for tests and split deployments):
    NEBULA_MCP_BASE      nebula-mcp base      (default http://localhost:3102)
    TACKLE_MCP_BASE      tackle-mcp base      (default http://localhost:3400)
    ASSEMBLY_API         assembly-srv API     (default http://localhost:3107/api)
    NEXUS_TIMECLOCK_URL  timeclock base       (default http://localhost:3600)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "python", "nebula-mcp-client"))

try:
    from nebula_mcp_client import McpClient  # type: ignore
except Exception as _e:  # pragma: no cover - only when repo layout broken
    McpClient = None  # type: ignore
    _MCP_IMPORT_ERROR = _e

NEBULA_MCP = os.environ.get("NEBULA_MCP_BASE", "http://localhost:3102")
TACKLE_MCP = os.environ.get("TACKLE_MCP_BASE", "http://localhost:3400")
ASSEMBLY_API = os.environ.get("ASSEMBLY_API", "http://localhost:3107/api")
TIMECLOCK = os.environ.get("NEXUS_TIMECLOCK_URL", "http://localhost:3600")
# Local CalendarEvent accumulation (Q3 slice, design a330914e): the JSONL
# calendar the --calendar step and timer emitters append to. File-backed,
# no DB — consolidation waits on Q1/Q2.
CALENDAR_STATE_DIR = os.environ.get(
    "CALENDAR_STATE_DIR",
    os.path.expanduser("~/.local/state/nexus-calendar"))

DEFAULT_TTL = 4 * 3600          # 4h lease window
DEFAULT_BUDGET = 10             # budget units
DEFAULT_CHANNEL = "interactive"  # R18: Freebuff agents lease the interactive channel


# ── helpers ───────────────────────────────────────────────────────────────

def iso_of(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def text_of(mcp_result: object) -> str:
    """Flatten an MCP tool result into its text payload."""
    if isinstance(mcp_result, dict) and isinstance(mcp_result.get("content"), list):
        return "".join(
            c.get("text", "") for c in mcp_result["content"]
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return mcp_result if isinstance(mcp_result, str) else json.dumps(mcp_result or {})


def parse_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def http_json(url: str, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    """REST helper. Returns (status, parsed_body). Raises on transport errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode() or "{}"
        return r.status, (json.loads(raw) if raw.strip() else {})


def probe(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Cheap liveness probe. ANY HTTP response counts as up (even 4xx/5xx —
    the question is 'is something listening', R15)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code} (listening)"
    except Exception as e:
        return False, type(e).__name__


# ── boot steps ────────────────────────────────────────────────────────────

class Boot:
    def __init__(self, role: str, model: str, channel: str, ttl: int, budget: int,
                 lease_policy: str, update_pointer: bool, limit: int,
                 dry_run: bool, strict: bool, want_digest: bool = False,
                 want_conn: bool = False, want_attest_scan: bool = True,
                 want_calendar: bool = True):
        self.role = role
        self.model = model
        self.channel = channel
        self.ttl = ttl
        self.budget = budget
        self.lease_policy = lease_policy
        self.update_pointer = update_pointer
        self.limit = limit
        self.dry_run = dry_run
        self.strict = strict
        self.want_digest = want_digest
        self.want_conn = want_conn
        self.want_attest_scan = want_attest_scan
        self.want_calendar = want_calendar
        self.lease_instant: str | None = None  # captured at clock-in (Q2 anchor)
        # --attest payload: (cites_id, evidence list, session_id) or None
        self.attest_cmd: tuple | None = None
        self.lease_id = None        # captured by the lease step (census provenance)
        self.digest_summary = None  # captured by the digest step (handoff affordance)
        self.steps: list[dict] = []

    def record(self, name: str, status: str, detail: str) -> None:
        self.steps.append({"step": name, "status": status, "detail": detail})
        mark = {"ok": "[ok]", "skipped": "[skip]", "degraded": "[DEGRADED]", "failed": "[FAILED]"}.get(status, "[?]")
        print(f"  {mark} {name}: {detail}")

    # 1 ─ pre-flight -------------------------------------------------------
    def preflight(self) -> dict:
        targets = {
            "nebula-mcp": NEBULA_MCP,
            "assembly-srv": ASSEMBLY_API,
            "timeclock": TIMECLOCK,
            "tackle-mcp": TACKLE_MCP,
        }
        state = {}
        print("== pre-flight ==")
        for name, base in targets.items():
            ok, detail = probe(base)
            state[name] = ok
            self.record(f"preflight {name}", "ok" if ok else "degraded", f"{base} → {detail}")
        return state

    # 2 ─ role lease -------------------------------------------------------
    def lease(self) -> None:
        print("== role lease ==")
        if self.lease_policy == "skip":
            self.record("lease", "skipped", "--lease skip")
            return
        try:
            client = McpClient(NEBULA_MCP)
            result = client.call("role_lease_status", {"role": self.role, "status": "ACTIVE"})
            data = parse_json(text_of(result)) or {}
            items = data.get("items") or []
            mine = next((l for l in items if (l.get("channel") or self.channel) == self.channel), None)
            if mine is not None:
                action = "renew"
                lease_id = mine.get("id")
            else:
                action = "issue"
                lease_id = None
            if self.dry_run:
                self.record("lease", "skipped",
                            f"dry-run: would {action}" + (f" lease {lease_id}" if lease_id else
                                                          f" ({self.role}@{self.channel}, ttl={self.ttl}s, budget={self.budget})"))
                return
            if action == "renew":
                client.call("role_lease_renew", {
                    "id": lease_id, "ttlSeconds": self.ttl, "budgetUnits": self.budget})
                self.record("lease", "ok", f"renewed {lease_id} (ttl={self.ttl}s, budget={self.budget})")
                self.lease_id = lease_id
            else:
                res = parse_json(text_of(client.call("role_lease_issue", {
                    "role": self.role, "channel": self.channel, "model": self.model,
                    "ttlSeconds": self.ttl, "budgetUnits": self.budget}))) or {}
                self.record("lease", "ok", f"issued {res.get('id', '?')} for {self.role}@{self.channel} (ttl={self.ttl}s)")
                self.lease_id = res.get("id")
        except Exception as e:
            self.record("lease", "degraded", f"nebula-mcp lease tools unavailable: {type(e).__name__}: {str(e)[:120]}")

    # 3 ─ clock-in ---------------------------------------------------------
    def clock_in(self) -> None:
        print("== clock-in (R13) ==")
        if self.dry_run:
            self.record("clock-in", "skipped", f"dry-run: would POST {TIMECLOCK}/clock-in for {self.role}")
            return
        try:
            status, body = http_json(f"{TIMECLOCK}/clock-in", method="POST",
                                     body={"role": self.role, "model": self.model})
            # The timeclock answers 200 with {success: true, record: {...}}
            # (verified live); accept both that shape and the documented
            # {"status": "ok"} shape.
            ok = status == 200 and (body.get("success") is True or body.get("status") == "ok")
            detail = (f"clocked in ({(body.get('record') or {}).get('id', '?')})"
                      if ok else f"unexpected response: HTTP {status} {json.dumps(body)[:100]}")
            self.record("clock-in", "ok" if ok else "degraded", detail)
            # Q2 anchor: the clock-in instant is the session's window.start —
            # the calendar step emits anchored here, not at emit time.
            if ok:
                self.lease_instant = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            self.record("clock-in", "degraded", f"timeclock unavailable: {type(e).__name__}: {str(e)[:120]}")

    # 3b ─ calendar (session CalendarEvent, Q3 slice a330914e) --------------
    def calendar_step(self) -> None:
        """Emit the session CalendarEvent (Q3 slice, design a330914e).

        Records this boot as a kind=occurred event anchored at the lease
        instant (the session's true start, per PR #331 window semantics —
        the occurrence, not the emit instant). **Default-on** since PR
        #336: every session start lands in the calendar; --no-calendar is
        the explicit opt-out. Degrades, never fails:
        - --no-calendar          → step absent entirely
        - emitter subprocess any outcome → recorded as data, boot continues
        - --dry-run              → [skip] (zero-mutation stance)
        """
        if not self.want_calendar:
            return
        print("== calendar (session event) ==")
        if self.dry_run:
            self.record("calendar", "skipped",
                        "dry-run: session event not emitted (zero-mutation stance)")
            return
        window_start = self.lease_instant or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, "calendar-emit.py"),
                 "emit", "--kind", "occurred",
                 "--emitter", "boot-shim:session-start",
                 "--title", f"session start: {self.role}@{self.channel} ({self.model})",
                 "--role", self.role,
                 "--window-start", window_start,
                 "--state-dir", CALENDAR_STATE_DIR],
                capture_output=True, text=True, timeout=15,
            )
            detail = (r.stdout.strip().splitlines() or ["no output"])[-1][:160]
            status = "ok" if r.returncode == 0 and "[appended]" in detail else \
                     ("ok" if "[duplicate]" in detail else "degraded")
            self.record("calendar", status, detail)
        except Exception as e:  # emitter failure is data, not a boot failure
            self.record("calendar", "degraded",
                        f"emitter subprocess failed: {type(e).__name__}: {str(e)[:120]}")

    # 3c ─ connection record (V169 affordance census) -----------------------
    def connection_record(self) -> None:
        print("== connection record (affordance census) ==")
        if not self.want_conn:
            return
        if self.dry_run:
            self.record("conn-record", "skipped",
                        "dry-run: census not recorded (zero-mutation stance)")
            return
        try:
            # the digest step adds python/ to sys.path; --conn-record must not
            # depend on --digest having run first
            if os.path.join(SCRIPT_DIR, "..", "python") not in sys.path:
                sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "python"))
            from continuity.census import collect_census, record_connection
        except Exception as e:  # noqa: BLE001 — absence is a skip, not a failure
            self.record("conn-record", "skipped",
                        f"continuity census not importable ({e.__class__.__name__})")
            return
        try:
            row = collect_census(
                self.role, self.model, self.channel,
                session_id=os.environ.get("FREEBUFF_SESSION_ID"),
                lease_ref=self.lease_id,
                digest_result=self.digest_summary)
            result = record_connection(row)
            status = "ok" if result.get("recorded") else "skipped"
            detail = result.get("reason", "")
            if result.get("conn_id"):
                detail += f" ({str(result['conn_id'])[:8]})"
            self.record("conn-record", status, detail)
        except Exception as e:  # noqa: BLE001 — census must not fail the boot
            self.record("conn-record", "degraded",
                        f"census error: {e.__class__.__name__}: {str(e)[:120]}")

    # 3c ─ attest-scan (V179 open-chain surfacing; read-only, default-on) --
    def attest_scan(self) -> None:
        print("== attest-scan (open verification requests) ==")
        if not self.want_attest_scan:
            return
        if self.dry_run:
            self.record("attest-scan", "skipped", "dry-run: read-only scan not run")
            return
        try:
            # digest/conn-record steps add python/ to sys.path; standalone
            # boots must not depend on those flags having run first
            if os.path.join(SCRIPT_DIR, "..", "python") not in sys.path:
                sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "python"))
            from continuity import attest as attest_mod
            result = attest_mod.scan(None, self.role)
        except Exception as e:  # noqa: BLE001 — scan never fails the boot
            self.record("attest-scan", "degraded",
                        f"scan error: {e.__class__.__name__}: {str(e)[:120]}")
            return
        if not result.get("scanned"):
            self.record("attest-scan", "skipped", result.get("reason", "unavailable"))
            return
        actionable = [i for i in result.get("open", [])
                      if i.get("disposition") == "actionable"]
        for item in result.get("open", []):
            aid = str(item.get("attestation_id", ""))[:8]
            if item.get("disposition") == "actionable":
                print(f"    ACTIONABLE for {self.role}: {aid} — {item.get('note', '')}")
            elif item.get("disposition") == "g1_refused":
                print(f"    (yours, G1-refused: {aid} — {item.get('note', '')})")
        detail = (f"{len(result.get('open', []))} open request(s); "
                  f"{len(actionable)} actionable")
        self.record("attest-scan", "ok", detail)

    # 3d ─ attest --record (explicit gated recording; never default-on) ----
    def attest_record(self) -> None:
        if not self.attest_cmd:
            return
        cites_id, evidence, session_id = self.attest_cmd
        if self.dry_run:
            self.record("attest-record", "skipped",
                        "dry-run: no attestation recorded (zero-mutation stance)")
            return
        try:
            if os.path.join(SCRIPT_DIR, "..", "python") not in sys.path:
                sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "python"))
            from continuity import attest as attest_mod
            result = attest_mod.record_attestation(
                None, self.role, cites_id, evidence, session_id=session_id,
                agent_record_id=os.environ.get("FREEBUFF_ATTEST_RECORD_REF"))
        except Exception as e:  # noqa: BLE001 — recording failures are data
            self.record("attest-record", "failed",
                        f"record error: {e.__class__.__name__}: {str(e)[:120]}")
            return
        if result.get("recorded"):
            aid = str(result.get("attestation_id") or "")[:8]
            self.record("attest-record", "ok",
                        f"{result.get('reason', '')} ({aid}, txid {result.get('txid', '?')})")
        else:
            self.record("attest-record", "failed", result.get("reason", "refused"))

    # 4 ─ inbox ------------------------------------------------------------
    def inbox(self) -> None:
        print("== inbox (R17) ==")
        try:
            client = McpClient(NEBULA_MCP)
            result = client.call("nebula_get_inbox", {"role": self.role, "limit": self.limit})
            data = parse_json(text_of(result)) or {}
            items = data.get("items") or []
            pointer = data.get("pointer")
            self.record("inbox", "ok",
                        f"{len(items)} new record(s) since {pointer or '(no pointer)'}")
            for rec in items[: self.limit]:
                ts = rec.get("createdAt", 0)
                try:
                    iso = iso_of(ts)
                except Exception:
                    iso = str(ts)
                print(f"    - {iso} | {rec.get('recordType', '?')} | {str(rec.get('title', ''))[:80]}")
            if self.update_pointer and items:
                if self.dry_run:
                    self.record("inbox pointer", "skipped", "dry-run: would advance pointer")
                    return
                newest = max((r.get("createdAt") or 0) for r in items)
                if newest:
                    client.call("nebula_set_inbox_pointer", {"role": self.role, "timestamp": iso_of(newest)})
                    self.record("inbox pointer", "ok", f"advanced to {iso_of(newest)}")
        except Exception as e:
            self.record("inbox", "degraded", f"nebula-mcp inbox unavailable: {type(e).__name__}: {str(e)[:120]}")

    # 5 ─ forum scan -------------------------------------------------------
    def forums(self) -> None:
        print("== forum scan (R13/R16/R18) ==")
        counts = {}
        try:
            for slug, label in (("issues-and-open-questions", "issues"),
                                ("to-do", "todos")):
                _, body = http_json(f"{ASSEMBLY_API}/forums/{slug}/threads?pageSize=5")
                items = body.get("items") if isinstance(body, dict) else body
                items = items or []
                counts[label] = len(items)
                for t in items[:3]:
                    print(f"    [{label}] {str(t.get('title', ''))[:90]}")
            _, cl = http_json(f"{ASSEMBLY_API}/forums/change-log/threads?pageSize=3")
            cl_items = cl.get("items") if isinstance(cl, dict) else cl
            self.record("forums", "ok",
                        f"issues={counts.get('issues', 0)} open, todos={counts.get('todos', 0)} open, change-log fetched")
        except Exception as e:
            self.record("forums", "degraded", f"assembly-srv forums unavailable: {type(e).__name__}: {str(e)[:120]}")

    # 6 ─ procedures -------------------------------------------------------
    def procedures(self) -> None:
        print("== procedure registry ==")
        try:
            client = McpClient(TACKLE_MCP)
            result = client.call("memory_get_procedures", {"role": self.role})
            data = parse_json(text_of(result)) or {}
            count = data.get("count", 0)
            slugs = [p.get("slug") for p in (data.get("procedures") or [])][:5]
            detail = f"{count} card(s) for role {self.role!r}"
            if count == 0:
                detail += " (note: registry is case-sensitive — try the role's canonical case)"
            else:
                detail += ": " + ", ".join(filter(None, slugs))
            self.record("procedures", "ok", detail)
        except Exception as e:
            self.record("procedures", "degraded",
                        f"tackle-mcp unavailable — procedure cards NOT visible this session "
                        f"({type(e).__name__}: {str(e)[:100]})")

    # report ---------------------------------------------------------------
    def report(self) -> int:
        ok = sum(1 for s in self.steps if s["status"] == "ok")
        degraded = sum(1 for s in self.steps if s["status"] == "degraded")
        failed = sum(1 for s in self.steps if s["status"] == "failed")
        skipped = sum(1 for s in self.steps if s["status"] == "skipped")
        print(f"\n== boot report: {ok} ok, {skipped} skipped, {degraded} degraded, {failed} failed "
              f"({'dry-run' if self.dry_run else 'live'}, role={self.role}@{self.channel}) ==")
        if degraded:
            print("  DEGRADED MODE: some governance services are unreachable; the boot "
                  "continued, but the missing surfaces were NOT enforced this session.")
        exit_code = 0
        if self.strict and (degraded or failed):
            exit_code = 1
        return exit_code

    # 6 ─ continuity digest preview (optional, --digest) -------------------
    def digest_preview(self) -> None:
        """Print the role continuity digest preview (v0, read-only).

        Continuity thread 65fe85a8: at session start, an adopted role gets a
        compacted view assembled from canonical surfaces (open inbox, open
        to-do threads, recent record metadata — metadata-only, no LLM, no
        chat recycling). Runs AFTER the lease step so the preview is shown
        precisely when the role is (or just became) adopted. Persistence
        into session_context_snapshots is continuity step 4, gated on the
        roundtable's Q1/Q2 — this step only PREVIEWS.

        Degrades, never fails the boot:
        - digest mode off        → [skip]
        - continuity package absent (PR #274 not merged yet) → [skip]
        - assembly error         → [degraded]
        - --dry-run              → [skip] (preview is a read, but honor
                                   dry-run's zero-surprise stance)
        """
        if not self.want_digest:
            return
        if self.dry_run:
            self.record("digest", "skipped", "dry-run: preview not assembled (zero-mutation stance)")
            return
        try:
            sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "python"))
            from continuity.digest import _live_fetchers, assemble_digest  # noqa: I252
        except Exception as e:  # noqa: BLE001 — absence is a skip, not a failure
            self.record("digest", "skipped",
                        f"continuity package not importable ({e.__class__.__name__}) "
                        "— digest (PR #274) not merged yet?")
            return
        try:
            fetched = _live_fetchers(self.role)
            flease = fetched[4] if len(fetched) == 5 else None  # v0.1+: lease binding
            fin, fth, frec, ceiling = fetched[:4]
            import inspect
            if flease is not None and "fetch_lease" in inspect.signature(
                    assemble_digest).parameters:
                # v0.1+ assembler: pass the lease fetcher (adoption-time binding)
                digest = assemble_digest(self.role, self.model, fin, fth, frec,
                                         ceiling, fetch_lease=flease)
            else:
                # pre-v0.1 assembler: assemble unbound (still a valid v0 preview)
                digest = assemble_digest(self.role, self.model, fin, fth, frec, ceiling)
            c = digest.get("counts", {})
            self.digest_summary = {"counts": c,
                                   "digest_version": digest.get("digest_version"),
                                   "sources_degraded": digest.get("sources_degraded")}
            self.record("digest", "ok",
                        f"preview assembled: inbox={c.get('open_inbox', 0)} "
                        f"threads={c.get('open_threads', 0)} "
                        f"records={c.get('recent_records', 0)} "
                        f"degraded={len(digest.get('sources_degraded', []))} "
                        f"disposition={digest.get('disposition')}")
            print(json.dumps(digest, indent=2, default=str))
            # ── Step-4 pre-stage: attempt snapshot persistence ───────────
            # persist_digest is adoption-gated (bound digests only) and inert
            # by table detection until V167 is applied (roundtable Q1/Q2).
            # Import-guarded: pre-step-4 continuity modules simply skip.
            try:
                from continuity.persist import persist_digest
                result = persist_digest(digest)
                status = "ok" if result.get("persisted") else "skipped"
                self.record("snapshot", status, result.get("reason", ""))
            except ImportError:
                self.record("snapshot", "skipped",
                            "persistence hook not present in this continuity module "
                            "(pre-step-4)")
            except Exception as e:  # noqa: BLE001 — persist hook must not fail the boot
                self.record("snapshot", "degraded",
                            f"persist hook error: {e.__class__.__name__}")
        except Exception as e:  # noqa: BLE001 — surface as degraded, keep booting
            self.record("digest", "degraded", f"assembly error: {e}")

    def run(self) -> int:
        state = self.preflight()
        print()
        if not state.get("nebula-mcp"):
            self.record("lease", "degraded", "skipped: nebula-mcp down")
            self.record("inbox", "degraded", "skipped: nebula-mcp down")
        else:
            self.lease()
            self.inbox()
        self.digest_preview()
        self.connection_record()
        self.attest_scan()
        self.attest_record()
        self.clock_in()
        self.calendar_step()
        self.forums()
        self.procedures()
        return self.report()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="freebuff-boot.py",
        description="Freebuff-conformance boot shim: lease, clock-in, inbox, forums, procedures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[0],
    )
    ap.add_argument("--role", "-r", required=True, help="role name (e.g. engineer, DBA)")
    ap.add_argument("--model", default=os.environ.get("NEXUS_AGENT_MODEL", "freebuff/agent"),
                    help="model ID recorded on the lease and clock-in")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL, help=f"lease channel (default {DEFAULT_CHANNEL})")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL, help=f"lease ttl seconds (default {DEFAULT_TTL})")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"lease budget units (default {DEFAULT_BUDGET})")
    ap.add_argument("--lease", choices=("auto", "skip"), default="auto",
                    help="auto = renew ACTIVE lease or issue new; skip = leave leases alone")
    ap.add_argument("--update-pointer", action="store_true",
                    help="advance the inbox pointer to the newest record after listing")
    ap.add_argument("--limit", type=int, default=10, help="max inbox records to list (default 10)")
    ap.add_argument("--dry-run", action="store_true",
                    help="pre-flight and plan only — perform NO mutations (no lease, no clock-in, no pointer write)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any step is degraded/failed (default: degraded is reported, exit 0)")
    ap.add_argument("--json", action="store_true", help="append a machine-readable JSON report")
    ap.add_argument("--digest", action="store_true",
                    help="print the role continuity digest preview (v0, read-only) after "
                         "the lease step — adoption-gated continuity, thread 65fe85a8")
    ap.add_argument("--conn-record", action="store_true",
                    help="record the session affordance census (nebula.agent_connections, "
                         "V169): MCP tools, procedure cards, inbox, handoff, keychains "
                         "— inert until V169 is applied")
    ap.add_argument("--calendar", action="store_true", default=True,
                    help=argparse.SUPPRESS)  # default-on since PR #336; kept for back-compat
    ap.add_argument("--no-calendar", action="store_false", dest="calendar",
                    help="skip the session CalendarEvent (default-on): no kind=occurred "
                         "session-start event is appended to the local JSONL calendar")
    ap.add_argument("--no-attest-scan", action="store_true",
                    help="skip the default read-only attest-scan (open V179 chains)")
    ap.add_argument("--attest", metavar="CITES_ID",
                    help="record an attestation citing the given verification_request "
                         "row (explicit, gated: G1/G2/G3 enforced client-side and "
                         "server-side; requires --evidence)")
    ap.add_argument("--evidence", metavar="REF[,REF...]",
                    help="comma-separated evidence refs for --attest (citable runs, "
                         "artifacts, txids — never bare claims)")
    ap.add_argument("--attest-session", metavar="SID",
                    help="optional session_id to stamp on the --attest row")
    args = ap.parse_args(argv)

    if McpClient is None:
        print(f"ERROR: nebula-mcp-client library not importable: {_MCP_IMPORT_ERROR}", file=sys.stderr)
        return 1
    if args.limit < 1 or args.ttl < 1 or args.budget < 1:
        print("ERROR: --limit/--ttl/--budget must be >= 1", file=sys.stderr)
        return 2

    boot = Boot(role=args.role, model=args.model, channel=args.channel, ttl=args.ttl,
                budget=args.budget, lease_policy=args.lease, update_pointer=args.update_pointer,
        limit=args.limit, dry_run=args.dry_run, strict=args.strict,
        want_digest=args.digest, want_conn=args.conn_record,
        want_attest_scan=not args.no_attest_scan,
        want_calendar=args.calendar)

    if args.attest:
        if not args.evidence:
            print("ERROR: --attest requires --evidence REF[,REF...] (G2: an "
                  "attestation without citable evidence is fabrication)", file=sys.stderr)
            return 2
        boot.attest_cmd = (args.attest,
                           [e.strip() for e in args.evidence.split(",") if e.strip()],
                           args.attest_session)
    code = boot.run()
    if args.json:
        print(json.dumps({"role": args.role, "model": args.model, "channel": args.channel,
                          "dry_run": args.dry_run, "steps": boot.steps}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
