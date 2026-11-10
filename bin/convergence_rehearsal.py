#!/usr/bin/env python3
"""Bounded, non-authoritative Night-Shift rehearsal for plan 8261645 (track B).

What is REAL in this run:
  * the plan-receipt transition law is read from the production source
    (`typescript/conduit-mcp/src/receipts.ts`, the ALLOWED map), not restated here;
  * receipts and ticket transitions are driven over HTTP against a gateway bound
    to an ephemeral loopback port (never :3100/:3103);
  * the accepted chain is materialised into a throwaway PostgreSQL database that
    is created for the run and dropped at the end, inside a transaction that is
    rolled back.

What is SIMULATED: the gateway stands in for conduit-mcp. There is no merge, no
attestation, no live receipt, no PEB/activation change and no sonar write. The
only claim this run supports is the one track B allows: "the flow runs end to
end inside its bounds, and rolls back".

Writes evidence to docs/convergence/evidence/ and refuses (exit 1) if any slot
cannot be produced from observed output.
"""

import datetime as dt
import http.server
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECEIPTS_TS = ROOT / "typescript" / "conduit-mcp" / "src" / "receipts.ts"
EVIDENCE = ROOT / "docs" / "convergence" / "evidence"
MAX_WORK_REQUESTS = 5


# ── transition law, read from the production source ──────────────────────────
def load_allowed_transitions():
    source = RECEIPTS_TS.read_text()
    start = source.index("const ALLOWED")
    body = source[start: source.index("\n};", start)]
    allowed, current = {}, None
    for line in body.splitlines()[1:]:
        key = re.match(r'\s*"?([A-Z_]*)"?:\s*\[', line)
        if key:
            current = key.group(1)
            allowed.setdefault(current, [])
            for token in re.findall(r'"([A-Z_]+)"', line):
                allowed[current].append(token)
    required = {
        "PLAN_CREATE": ["IMPLEMENTATION", "BLOCK"],
        "IMPLEMENTATION": ["CRITIQUE", "REVIEW_PASS"],
        "CRITIQUE": ["CRITIQUE_PASS"],
        "CRITIQUE_PASS": ["REVIEW"],
        "REVIEW": ["REVIEW_PASS"],
        "BLOCK": ["REQUEUED"],
        "REVIEW_PASS": [],
    }
    for src, targets in required.items():
        if src not in allowed:
            raise SystemExit(f"transition law: {src} missing from {RECEIPTS_TS.name}")
        for target in targets:
            if target not in allowed[src]:
                raise SystemExit(f"transition law: {src} -> {target} not allowed by receipts.ts")
    return allowed


# ── rehearsal store + gateway ────────────────────────────────────────────────
class Store:
    def __init__(self, allowed):
        self.allowed = allowed
        self.receipts = []  # dicts: plan_id, seq, type, role, session, summary
        self.last = {}
        self.tickets = {}
        self.events = []
        self.lock = threading.Lock()

    def _event(self, **kw):
        kw["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
        self.events.append(kw)
        return kw

    def create_plan(self, plan_id, role="planner"):
        with self.lock:
            if plan_id in self.last:
                return 409, {"error": f"plan {plan_id} already exists"}
            self.last[plan_id] = "PLAN_CREATE"
            self.receipts.append(
                {"plan_id": plan_id, "type": "PLAN_CREATE", "role": role,
                 "session": "rehearsal", "summary": "rehearsal plan created"}
            )
            self._event(stage="plan", role=role, action="create_plan", plan_id=plan_id,
                        receipt="PLAN_CREATE", status=201)
            return 201, {"plan_id": plan_id, "derived": "PLAN_CREATE"}

    def append_receipt(self, plan_id, type_, role, session="rehearsal", summary=""):
        with self.lock:
            if plan_id not in self.last:
                return 404, {"error": f"unknown plan {plan_id}"}
            current = self.last[plan_id]
            if type_ not in self.allowed.get(current, []):
                self._event(stage="receipt", role=role, action=type_, plan_id=plan_id,
                            status=409, refused_from=current)
                return 409, {"error": f"{type_} not allowed from {current}",
                             "allowed": self.allowed.get(current, [])}
            self.receipts.append({"plan_id": plan_id, "type": type_, "role": role,
                                  "session": session, "summary": summary})
            self.last[plan_id] = type_
            self._event(stage="receipt", role=role, action=type_, plan_id=plan_id,
                        receipt=type_, status=200)
            return 200, {"plan_id": plan_id, "derived": type_}

    def claim(self, ticket_id, plan_id, role):
        with self.lock:
            state = self.tickets.setdefault(ticket_id, {"plan_id": plan_id, "state": "open", "history": []})
            if state["state"] != "open":
                self._event(stage="ticket", role=role, action="claim", ticket_id=ticket_id,
                            status=409, refused_from=state["state"])
                return 409, {"error": f"ticket {ticket_id} is {state['state']}"}
            state["state"] = "claimed"
            state["history"].append("open->claimed")
            self._event(stage="ticket", role=role, action="claim", ticket_id=ticket_id,
                        status=200, transition="open->claimed")
            return 200, {"ticket_id": ticket_id, "state": "claimed"}

    def transition_ticket(self, ticket_id, to_state, role):
        with self.lock:
            state = self.tickets.get(ticket_id)
            if not state:
                return 404, {"error": f"unknown ticket {ticket_id}"}
            edges = {("claimed", "completed"), ("claimed", "stale"), ("stale", "open"),
                     ("stale", "claimed"), ("open", "claimed")}
            if (state["state"], to_state) not in edges:
                self._event(stage="ticket", role=role, action=to_state, ticket_id=ticket_id,
                            status=409, refused_from=state["state"])
                return 409, {"error": f"{state['state']}->{to_state} not allowed"}
            previous = state["state"]
            state["state"] = to_state
            state["history"].append(f"{previous}->{to_state}")
            self._event(stage="ticket", role=role, action=to_state, ticket_id=ticket_id,
                        status=200, transition=f"{previous}->{to_state}")
            return 200, {"ticket_id": ticket_id, "state": to_state}


def make_gateway(store):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/state":
                self._reply(200, {"receipts": len(store.receipts), "tickets": len(store.tickets),
                                  "plans": sorted(store.last)})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/plan":
                self._reply(*store.create_plan(data["plan_id"], data.get("role", "planner")))
            elif self.path == "/receipt":
                self._reply(*store.append_receipt(
                    data["plan_id"], data["type"], data["role"],
                    data.get("session", "rehearsal"), data.get("summary", "")))
            elif self.path == "/claim":
                self._reply(*store.claim(data["ticket_id"], data["plan_id"], data["role"]))
            elif self.path == "/ticket":
                self._reply(*store.transition_ticket(data["ticket_id"], data["to"], data["role"]))
            else:
                self._reply(404, {"error": "not found"})

    return Handler


# ── disposable PostgreSQL ────────────────────────────────────────────────────
PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = os.environ.get("PGPORT", "5432")
PGUSER = os.environ.get("PGUSER", "pguser")


def psql(db, sql, check=True):
    proc = subprocess.run(
        ["psql", "-w", "-X", "-q", "-v", "ON_ERROR_STOP=1",
         "-h", PGHOST, "-p", PGPORT, "-U", PGUSER, "-d", db, "-At"],
        input=sql, capture_output=True, text=True, timeout=120,
        env=os.environ,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"psql failed on {db}: {proc.stderr.strip()[:500]}")
    return proc


def createdb(name):
    proc = subprocess.run(["createdb", "-w", "-h", PGHOST, "-p", PGPORT, "-U", PGUSER, name],
                          capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL, env=os.environ)
    if proc.returncode != 0:
        raise RuntimeError(f"createdb failed: {proc.stderr.strip()[:300]}")


def dropdb(name):
    subprocess.run(["dropdb", "-w", "-h", PGHOST, "-p", PGPORT, "-U", PGUSER, name],
                   capture_output=True, text=True,
                   timeout=120, stdin=subprocess.DEVNULL, env=os.environ)


def main():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db = f"nexus_ns_rehearsal_{os.getpid()}_{stamp}"
    allowed = load_allowed_transitions()
    store = Store(allowed)

    createdb(db)
    try:
        who = psql(db, "select current_database();").stdout.strip()
        if who != db:
            raise SystemExit(f"isolation pre-flight failed: connected to {who!r}, expected {db!r}")
        if who == "nexus":
            raise SystemExit("isolation pre-flight failed: rehearsal must not use the live database")

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_gateway(store))
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"

        def post(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b"{}")

        try:
            # ── bound: 3 work requests (<= 5), one same-file batch ───────────
            batch = ["rehearsal-R1", "rehearsal-R2"]
            for plan_id in batch:
                post("/plan", {"plan_id": plan_id})
            negative = "rehearsal-R3"
            post("/plan", {"plan_id": negative})
            work_requests = batch + [negative]
            if len(work_requests) > MAX_WORK_REQUESTS:
                raise SystemExit("bound violated: more than 5 work requests")

            # ── stage 2/3: ticket + implementation, batched consumers ────────
            for plan_id in batch:
                ticket = f"ticket-{plan_id}-builder"
                post("/claim", {"ticket_id": ticket, "plan_id": plan_id, "role": "builder"})
                post("/receipt", {"plan_id": plan_id, "type": "IMPLEMENTATION",
                                  "role": "builder", "session": "rehearsal-builder",
                                  "summary": "rehearsal implementation on a branch (no merge)"})
                post("/ticket", {"ticket_id": ticket, "to": "completed", "role": "builder"})

            # ── stage 4: critic gate (artifact position), then review ────────
            for plan_id in batch:
                post("/receipt", {"plan_id": plan_id, "type": "CRITIQUE", "role": "critic",
                                  "summary": "artifact critique admitted after IMPLEMENTATION"})
                post("/receipt", {"plan_id": plan_id, "type": "CRITIQUE_PASS", "role": "critic",
                                  "summary": "critic gate-pass (not merge authority)"})
                post("/receipt", {"plan_id": plan_id, "type": "REVIEW", "role": "reviewer",
                                  "summary": "review opened on rehearsal evidence"})
                post("/receipt", {"plan_id": plan_id, "type": "REVIEW_PASS", "role": "reviewer",
                                  "summary": "rehearsal judgement only; no merge issued"})

            # ── negative path + ticket takeover + rollback ───────────────────
            ticket = f"ticket-{negative}-builder"
            post("/claim", {"ticket_id": ticket, "plan_id": negative, "role": "builder"})
            post("/ticket", {"ticket_id": ticket, "to": "stale", "role": "builder"})
            post("/ticket", {"ticket_id": ticket, "to": "open", "role": "planner"})  # takeover
            post("/receipt", {"plan_id": negative, "type": "BLOCK", "role": "critic",
                              "summary": "rehearsal negative path: gate bounce"})
            post("/receipt", {"plan_id": negative, "type": "REQUEUED", "role": "planner",
                              "summary": "rollback: requeue after bounce"})

            state = json.loads(urllib.request.urlopen(base + "/state", timeout=10).read())
        finally:
            server.shutdown()
            server.server_close()

        # ── materialise into the throwaway DB, snapshot, roll back ──────────
        seq, inserts = {}, []
        for receipt in store.receipts:
            seq[receipt["plan_id"]] = seq.get(receipt["plan_id"], 0) + 1
            inserts.append(
                "INSERT INTO rehearsal_receipts (plan_id, seq, type, role, session, summary) VALUES "
                f"('{receipt['plan_id']}', {seq[receipt['plan_id']]}, '{receipt['type']}', "
                f"'{receipt['role']}', '{receipt['session']}', '{receipt['summary'].replace(chr(39), chr(39)*2)}');"
            )
        ticket_rows = []
        for ticket_id, data in sorted(store.tickets.items()):
            ticket_rows.append(
                "INSERT INTO rehearsal_tickets (ticket_id, plan_id, state, history) VALUES "
                f"('{ticket_id}', '{data['plan_id']}', '{data['state']}', "
                f"'{','.join(data['history'])}');"
            )
        script = "\n".join([
            "BEGIN;",
            "CREATE TEMP TABLE rehearsal_receipts (plan_id text, seq int, type text, role text, session text, summary text) ON COMMIT DROP;",
            "CREATE TEMP TABLE rehearsal_tickets (ticket_id text, plan_id text, state text, history text) ON COMMIT DROP;",
            *inserts, *ticket_rows,
            "\\echo __SNAPSHOT__",
            "SELECT plan_id || '|' || seq || '|' || type || '|' || role FROM rehearsal_receipts ORDER BY plan_id, seq;",
            "\\echo __TICKETS__",
            "SELECT ticket_id || '|' || state || '|' || history FROM rehearsal_tickets ORDER BY ticket_id;",
            "\\echo __COUNTS__",
            "SELECT 'rehearsal_store_receipts=' || count(*) FROM rehearsal_receipts;",
            "SELECT 'rehearsal_store_tickets=' || count(*) FROM rehearsal_tickets;",
            "\\echo __ROLLBACK__",
            "ROLLBACK;",
            "\\echo __AFTER_ROLLBACK__",
            "SELECT 'rehearsal_tables_remaining=' || (CASE WHEN "
            "to_regclass('rehearsal_receipts') IS NULL AND to_regclass('rehearsal_tickets') IS NULL "
            "THEN 0 ELSE 1 END);",
        ])
        out = psql(db, script)
        snapshot_text = out.stdout
        if "__AFTER_ROLLBACK__" not in snapshot_text:
            raise SystemExit(f"rehearsal store run did not complete: {out.stderr[:400]}")

        after = snapshot_text.split("__AFTER_ROLLBACK__", 1)[1].strip().splitlines()
        tables_remaining = after[-1] if after else "missing"
        if tables_remaining != "rehearsal_tables_remaining=0":
            raise SystemExit(f"rollback did not clean the store: {tables_remaining}")

        # live-store isolation proof (read-only)
        live = psql("nexus",
                    "select 'live_conduit_receipts_for_rehearsal_ids=' || count(*) "
                    "from nebula.receipts_unified where plan_id like 'rehearsal-%';").stdout.strip()
        if live != "live_conduit_receipts_for_rehearsal_ids=0":
            raise SystemExit(f"isolation violated: {live}")

        seed_gate = subprocess.run([sys.executable, str(ROOT / "bin" / "convergence_seed_gate.py")],
                                   capture_output=True, text=True, cwd=str(ROOT), timeout=180)
        if seed_gate.returncode != 0:
            raise SystemExit(f"seed gate failed:\n{seed_gate.stdout}\n{seed_gate.stderr}")

        dropdb(db)
        gone = psql("postgres", f"select 'rehearsal_db_present=' || count(*) from pg_database "
                               f"where datname = '{db}';").stdout.strip()
        if gone != "rehearsal_db_present=0":
            raise SystemExit(f"throwaway database not removed: {gone}")

        write_evidence(db=db, port=port, stamp=stamp, store=store, state=state,
                       snapshot=snapshot_text, live=live, gone=gone,
                       seed_gate_output=seed_gate.stdout, allowed=allowed)
        print(f"rehearsal complete: db={db} port={port} receipts={len(store.receipts)} "
              f"tickets={len(store.tickets)} tables_after_rollback=0 live_receipts=0")
        print(f"evidence written under {EVIDENCE.relative_to(ROOT)}/")
        return 0
    finally:
        dropdb(db)


def write_evidence(*, db, port, stamp, store, state, snapshot, live, gone,
                   seed_gate_output, allowed):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "unknown"
    branch = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"

    lines = [
        "# B1 rehearsal transcript — plan 8261645 (track B, Night Shift)",
        "",
        "**Non-authoritative.** This is a bounded rehearsal, not a production run:",
        "no merge, no attestation, no live receipt, no PEB or activation change, no sonar write.",
        "",
        "## Provenance",
        "",
        f"- produced by: `python3 bin/convergence_rehearsal.py`",
        f"- run at: `{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}`",
        f"- git: branch `{branch}`, head `{head}`",
        f"- isolated gateway: `http://127.0.0.1:{port}` (ephemeral loopback port; live :3100/:3103 untouched)",
        f"- isolated store: throwaway database `{db}` (created for the run, dropped after)",
        f"- transition law: read at run time from `typescript/conduit-mcp/src/receipts.ts` ALLOWED map",
        "- live conduit receipt rows for these rehearsal ids: 0 (read-only check)",
        "",
        "## Bound",
        "",
        f"- work requests in the cycle: {len(state['plans'])} (bound: <= {MAX_WORK_REQUESTS})",
        "- batching: `rehearsal-R1` and `rehearsal-R2` batched as one same-file item",
        "",
        "## Consumers exercised",
        "",
        "planner (triage/batch) -> conduit gateway (plan + receipt) -> builder (claim, implement, complete)",
        "-> critic (artifact CRITIQUE / gate-pass, and one bounce) -> reviewer (REVIEW / REVIEW_PASS, judgement only)",
        "",
        "## Observed events",
        "",
        "| at (UTC) | stage | role | action | target | status |",
        "|---|---|---|---|---|---|",
    ]
    for event in store.events:
        target = event.get("plan_id") or event.get("ticket_id") or ""
        detail = event.get("receipt") or event.get("transition") or ""
        lines.append(
            f"| {event['at']} | {event['stage']} | {event['role']} | {event['action']} "
            f"| {target}{(' (' + detail + ')') if detail else ''} | {event['status']} |"
        )
    (EVIDENCE / "b1-rehearsal-transcript.md").write_text("\n".join(lines) + "\n")

    (EVIDENCE / "b2-receipt-chain-snapshot.txt").write_text(
        "# B2 receipt-chain snapshot and rollback proof — plan 8261645 (track B)\n"
        "# Non-authoritative rehearsal store; no live receipt was written.\n"
        f"# isolated_db={db}\n"
        f"# isolated_port={port}\n"
        f"# transition_law=typescript/conduit-mcp/src/receipts.ts (ALLOWED)\n"
        f"{snapshot}\n"
        f"{live}\n"
        f"{gone}\n"
    )

    (EVIDENCE / "d3-seed-gate-output.txt").write_text(
        "# D3 seed-gate run — plan 8261645 (KG projection contract freeze)\n"
        "# Read-only: hashes, the freeze package's own contract test, vocabulary comparison.\n"
        f"# run_by=bin/convergence_seed_gate.py at {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n"
        f"{seed_gate_output}"
    )

    (EVIDENCE / "README.md").write_text(
        "# Convergence evidence — plan 8261645\n\n"
        "Evidence produced by the bounded rehearsal (`bin/convergence_rehearsal.py`) and the\n"
        "seed gate (`bin/convergence_seed_gate.py`). Every file here is generated by a run;\n"
        "none is hand-written. Regenerate both rather than editing them:\n\n"
        "```bash\n"
        "python3 bin/convergence_rehearsal.py   # writes b1 + b2 and re-runs the seed gate\n"
        "python3 bin/convergence_seed_gate.py   # writes d3 alone (read-only)\n"
        "```\n\n"
        "Scope: the rehearsal drives a gateway that stands in for conduit-mcp on an ephemeral\n"
        "loopback port, persists the accepted chain only in a throwaway PostgreSQL database\n"
        "inside a transaction that is rolled back, and proves zero live receipts for those ids.\n"
        "It supersedes nothing, seeds nothing, merges nothing and approves nothing.\n"
    )


if __name__ == "__main__":
    sys.exit(main())
