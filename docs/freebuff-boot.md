# freebuff-boot.py — conformant session start in one command

`bin/freebuff-boot.py` performs the full session-start ritual — role lease,
clock-in, inbox check, forum scans, procedure-card load — in a single command,
and reports any degraded dependency instead of failing silently.

Background: agents running in harnesses that do not execute the boot protocol
themselves (e.g. Freebuff) still need lease/clock-in/inbox conformance
(R13/R17). The shim makes conformance the lazy path. See the admin-notes
thread "Using the boot shim: one command for a conformant session start" and
the governance case study "Protocol-portable vs harness-enforced governance".

## Usage

```bash
python3 nexus/bin/freebuff-boot.py --role DBA --model "$NEXUS_AGENT_MODEL"
```

| Flag | Effect |
|------|--------|
| `--role` (required) | Role name. **Case matters** at the procedure-registry layer: `DBA` loads 11 cards, `dba` loads 0 (sync-layer quirk). |
| `--model` | Model ID, recorded on clock-in/out so same-role agents on different models do not close each other's sessions. |
| `--ttl N` | Lease time limit, minutes (default 240). |
| `--budget N` | Lease consumption budget (default 10). |
| `--lease skip` | Opt out of the lease step. |
| `--dry-run` | Pre-flight + read-only scans only. **Zero mutations**: no lease, no clock-in, no pointer write. |
| `--strict` | Escalate degraded dependencies to exit 1. |
| `--update-pointer` | Advance the inbox pointer to now after surfacing items. |
| `--limit N` | Max inbox items to list. |

## What it does, in order

1. **Pre-flight** — probes nebula-mcp :3102, assembly-srv :3107, timeclock
   :3600, tackle-mcp :3400. Any HTTP answer counts as "up": the question is
   whether something is listening, not whether it is healthy.
2. **Role lease** — renews the role's ACTIVE lease or issues a new one.
   One lease per role is honored; a renewed lease is not duplicated.
3. **Clock-in** (R13) — accepts both the live timeclock response shape
   (`{"success": true, "record": {...}}`) and the documented one.
4. **Inbox** (R17) — lists records tagged `to:<role>` created since the
   stored pointer, via `nebula_get_inbox` on nebula-mcp.
5. **Forum scan** — open issues (`issues-and-open-questions`), open todos
   (`to-do`), and recent change-log entries, via assembly-srv REST.
6. **Procedures** — loads the role's card index via tackle-mcp
   (`memory_get_procedures`).

## Degraded mode

A down optional dependency is **reported** (`[DEGRADED]`) and the boot
continues with exit 0. The final report is the honest list of which
governance surfaces were *not* enforced this session. Agents that want hard
failure use `--strict`.

Exit codes: `0` boot complete (possibly degraded), `1` degraded under
`--strict`, `2` boot could not proceed (e.g. missing required arg).

## Service preconditions

The procedure tier needs **four** services, not two:

- tackle-mcp :3400 proxies card reads to **tackle-srv :3410**. If tackle-srv
  is down, `memory_get_procedures` throws `fetch failed` — a dead service,
  not bad data. Start it before debugging the registry.
- role-memory-srv :3500 + Redis :6379 serve the PG→Redis card sync.
  `POST http://localhost:3500/refresh` re-syncs (49 cards / 21 role indices
  as of 2026-09-14).

Every endpoint is overridable via env (`NEBULA_MCP_URL`, `ASSEMBLY_URL`,
`TIMECLOCK_URL`, `TACKLE_MCP_URL`) — the hermetic tests use exactly that.

## Known caveats

- The live timeclock returns `200 {"success": true, "record": {...}}`;
  the older `bin/verify-session.sh` greps for `{"status":"ok"}` and will
  misreport. The shim accepts both shapes; the older script deserves a fix.
- Role-name case in the procedure registry is inconsistent: indices sync
  under whatever case the role was registered with, so try the role's
  canonical case when an index comes back empty. The shim surfaces a caveat
  in that situation. (Verified 2026-09-14: `engineer` = 39 cards, but no
  `dba`/`DBA` index exists at all in either case — a role_memory content
  gap, not a shim defect. The shim reports it and the boot continues,
  which is the degraded-mode philosophy working as intended.)

## Tests

`tests/bin/freebuff-boot_checks.py` — 11 hermetic tests (mock MCP + REST
servers, same convention as `tests/bin/checks.py`). They pin: dry-run
silence, renew-vs-issue semantics, pointer advancement, degraded exit codes,
and the live timeclock response shape.

```bash
python3 tests/run_all.py bin
```
