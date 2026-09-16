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
                 dry_run: bool, strict: bool, want_digest: bool = False):
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
            else:
                res = parse_json(text_of(client.call("role_lease_issue", {
                    "role": self.role, "channel": self.channel, "model": self.model,
                    "ttlSeconds": self.ttl, "budgetUnits": self.budget}))) or {}
                self.record("lease", "ok", f"issued {res.get('id', '?')} for {self.role}@{self.channel} (ttl={self.ttl}s)")
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
        except Exception as e:
            self.record("clock-in", "degraded", f"timeclock unavailable: {type(e).__name__}: {str(e)[:120]}")

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
            self.record("digest", "ok",
                        f"preview assembled: inbox={c.get('open_inbox', 0)} "
                        f"threads={c.get('open_threads', 0)} "
                        f"records={c.get('recent_records', 0)} "
                        f"degraded={len(digest.get('sources_degraded', []))} "
                        f"disposition={digest.get('disposition')}")
            print(json.dumps(digest, indent=2, default=str))
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
        self.clock_in()
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
                want_digest=args.digest)
    code = boot.run()
    if args.json:
        print(json.dumps({"role": args.role, "model": args.model, "channel": args.channel,
                          "dry_run": args.dry_run, "steps": boot.steps}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
