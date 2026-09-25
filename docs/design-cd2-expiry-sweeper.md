# CD-2 design: expiry enforcement + re-alert-to-reviewer (ruling 1867c88d ¶2)

**Status:** PROPOSAL for architect/engineer sign-off (design only; nothing
here mutates flow state). Supersedes the pre-ruling sketch; grounded in:

- **Ruling 1867c88d** (2026-09-24 22:10): Conduit core owns expiry
  semantics; the sweeper is only the enforcement mechanism. Every
  generation — including REVIEW_REJECT rework — must receive a durable
  expiry transition/audit event at `expires_at`. Expiry must not imply
  completion. *"An expired unclaimed implementation ticket re-alerts
  and returns according to the reject scope."*
- **Planner D-4 contract** (22:09 response): one page per **state
  fingerprint** to plan owner + ticket role; suppress duplicates until
  state changes; **never auto-close**.
- **Landed already:** mechanical caller — PR #541 wired
  `TicketExpirySweeper` into conduit-mcp's watcher (60s interval,
  stale-then-expired, single-flight). `detectExpiredTickets()`
  transitions `open|claimed|stale → expired` with
  `kernel.transition_event` records (`transition.rejected`,
  `authority=system`, reason `expiry_detection`).

## What is still missing (the gap this design closes)

The sweeper transitions state but **alerts no one**: `onResult` only
console-logs counts. CD-2's *semantic* half — re-alert/return according
to reject scope — has no implementation, and D-4's page contract has no
emitter. Also, `detectExpiredTickets` writes `transition.rejected`
events into `kernel.transition_event`, but nothing downstream consumes
them.

## Design

### 1. Expiry semantics (Conduit core — already satisfied, keep)

`detectExpiredTickets` is correct per the ruling and needs no change:
durable transition + audit event at `expires_at`, terminal non-success
status, no completion implication. One addition is REQUIRED by the
ruling's audit clause (small, additive):

- **Event payload enrichment:** include `plan_id`, `role`,
  `generation` (position), `expires_at`, and `created_by_receipt` in
  the `expiry_detection` payload so consumers (the re-alerter, W-B6
  fixtures, the provenance reconcile) can process events without
  re-joining `vision.tickets`. Purely additive to the payload object.

### 2. Re-alert leg (new: `expiry-realet.ts` — pure core + sweeper hook)

A small pure module, tested hermetically, wired as a second callback
inside the existing sweeper `onResult` path (no second timer, no
second DB client):

```
interface ExpiryAlert {                      // one per (ticket, fingerprint)
  ticket_id: string;
  plan_id: string;
  role: string;                              // ticket role (builder|reviewer|…)
  fingerprint: string;                       // sha1 of state tuple (below)
  scope: "implementation" | "blueprint" | "none";
  recipients: string[];                      // assembly roles to notify
  reason: string;                            // expiry_detection | manual | …
}
```

**State fingerprint (D-4 dedupe key):** `sha1(plan_id | ticket_id |
from_status | to_status | expires_at | reject_scope)` — computed from
the ticket row + latest REVIEW_REJECT receipt's scope. D-4's "one page
per state fingerprint, suppressed until state changes" falls out
naturally: re-running the sweeper over an unchanged expired ticket
yields the same fingerprint → suppressed; a re-scope that changes
`expires_at`/scope/plan yields a new fingerprint → new page.

**Scope resolution (ruling ¶3 interlock):** reject scope comes from the
plan's latest `review_reject` receipt payload (`scope` field; default
`implementation` when absent, since that matches all current rows).
- `scope=blueprint` → recipients = `[planner]`; alert text carries the
  "return-to-planner" semantics (CD-3's edge in notification form;
  the planner-return *ticket/event* itself is W-B6/CD-3 implementation,
  not the sweeper's).
- `scope=implementation` → recipients = `[builder owner role, plan owner]`;
  today that is the engineer/builder lane.
- `scope=none` (expiry with no reject in chain, e.g. orphan tickets like
  8261638) → recipients = plan owner only.

**Emission channel (D-4 "page"):** one agent record per unique
fingerprint, filed as the sweeper's service identity (role `engineer`,
series tag `series:conduit-expiry`), title carrying plan/ticket/role,
body carrying the ruling reference (1867c88d ¶2), the reject-scope
verdict, and the transition-event id from `expiry_detection`. Agent
records are the fleet's existing attention surface (the same mechanism
that just routed this whole W-B/W-C arc); no new paging infra is
introduced. A later channel (email/TTS) can subscribe to the series tag
without contract change.

**Suppression store:** the fingerprint set lives in
`conduit.expiry_alert_state` (new small table: `fingerprint text pk,
ticket_id, first_seen_at, last_seen_at, alert_record_id`). First
sight → insert + emit. Repeat sight → update `last_seen_at`, no emit.
State change (fingerprint absent) → the old row is left for history;
the new fingerprint emits. Table creation rides the existing
conduit-mcp bootstrap migration path.

### 3. Wiring (conduit-mcp watcher — minimal diff)

```
const reAlerter = new ExpiryReAlerter({ db, emitRecord });
// inside TicketExpirySweeper onResult, after logging:
await reAlerter.processNewExpiryEvents();   // reads kernel.transition_event
                                            // since last cursor, groups by
                                            // ticket, resolves scope, dedupes
                                            // by fingerprint, emits records
```

- Cursor: `last_seen_event_id` persisted in the same state table →
  crash-safe replay (at-least-once; dedupe makes it idempotent).
- The sweeper's 60s cadence bounds alert latency to ≤1 minute after
  expiry — adequate for D-4 and avoids a new timer.
- Failure isolation: re-alert errors are caught and logged by the
  existing `onError` path; they must never fail the state transition
  (ruling: enforcement may not block semantics).

### 4. Transition-event attestation pass (added 2026-09-25)

The re-alert leg (§2) consumes events that are *emitted*. This pass closes
the other half: it watches for state that was **mutated without an event
at all** — terminal tickets carrying no `kernel.transition_event` row for
their aggregate. That class is real and measured: at design time,
363/371 cancelled, 139/142 completed, 86/86 failed tickets had zero
transition events (the Python adapter's silent write paths, fixed in
PR #556; the TS paths were already conformant).

**Grounding correction (record ae18bba7):** the gen-2 8261654
cancellation — originally cited as the motivating incident — actually
DID emit (`transition.rejected`, actor `conduit-mcp`, 2026-09-24
22:05:09.970). The earlier "zero events" finding was a failed query
misread as an empty result. Two design rules follow directly:

1. **Fail loud, never empty:** the checker treats any query error as
   FATAL (exit 2) — absence of evidence must never be concluded from a
   failing probe. Encoded as an exit contract in the checker's tests.
2. **The join is the measurement:** no per-ticket existence probes that
   can fail independently; one LEFT JOIN between `vision.tickets` and
   `kernel.transition_event` is the single source of the verdict.

**Check definition (read-only, `bin/check_transition_attestation.py`):**

- Population: `vision.tickets` rows with `status IN (cancelled,
  superseded, abandoned, expired, failed, completed)` and
  `closed_at IS NOT NULL`, LEFT JOIN-counted against
  `kernel.transition_event (aggregate_type='ticket', aggregate_id=t.id)`.
- **UNATTESTED** = event count 0. Any event at all (claim/release
  cycles included) attests the aggregate reached the event stream; the
  terminal-state-specific event is #556's coverage guarantee going
  forward.
- **Adoption cutoff 2026-09-22** (walk-through date, when the fleet
  began enforcing durable transitions per generation): unattested
  tickets closed AT/AFTER the cutoff → verdict DRIFT (exit 1).
  Pre-cutoff silence is counted as HISTORICAL and never fails — a
  backfill decision belongs to the architect, not to a checker exit
  code. The cutoff is a flag (`--since`), not dogma.
- **Wiring (same host, one more slot):** nightly user timer at 06:50
  UTC — after pg-logging-check (06:40), completing the drift/heartbeat
  cluster (SDK stamp 06:10, bcrypt 06:20, durability 06:30, pglog
  06:40, attestation 06:50). Unit files in `bin/` per the corpus
  doctrine (host-only units drift; the 2026-09-21 helium probe
  incident). Wrapper files green-heartbeat records on
  `series:conduit-attestation` (commissioning + weekly, daily
  suppressed) and a drift record per non-clean run — the same contract
  as PRs #527/#540 parity.
- **Deliberately NOT done here:** no auto-backfill of events for
  historical silence (fabricating audit rows is out-of-band mutation,
  the exact class this fleet is hunting); no auto-close of unattested
  tickets; no event regeneration — the checker surfaces, owners rule.
- **First-run result (2026-09-25, the check working as designed):**
  verdict DRIFT with two post-cutoff catches, both on 8261654's gen-1
  pair — builder `completed` 09-22 19:30 (`receipt:IMPLEMENTATION`) and
  reviewer `failed` 09-22 19:33 (`receipt:REVIEW_REJECT`), both with
  zero events. Root cause: the TS **receipt-advance path**
  (`advanceTicketsOnReceipt`, db.ts) closes tickets without emitting —
  a remaining silent surface distinct from #556's Python scope (cancel/
  claim/release in TS do emit). Fix ask routed to the engineer; the
  checker holds the class visible daily until it lands, then the series
  goes green and watches.

### 5. What this design deliberately does NOT do

- No auto-close, no respawn (respawn-on-expiry was rejected by the
  ruling's "expiry must not imply completion" + the 18:37 no-bulk-close
  analysis; respawn decisions belong to scope routing: CD-3/W-B6).
- No blueprint-scope *routing* implementation (that is CD-3's
  planner-return edge, W-B6 scope) — the re-alerter only notifies per
  scope; it does not create tickets.
- No change to `detectExpiredTickets` semantics (ruling: core owns
  them; they are already conformant).
- No direct DB mutation path for 8261654 (already dispositioned by the
  planner at 22:04–22:11, correctly, with transition records).

### 6. Tests (hermetic, mirroring ticket-sweep.test.ts)

- fingerprint stability: same inputs → same fingerprint; any field
  change → different fingerprint
- scope resolution: implementation/blueprint/none → recipient sets
- dedupe: first sight emits; repeat suppresses; changed state emits
- cursor crash-replay: unprocessed events reprocessed, no dupes
- failure isolation: re-alerter throw does not fail sweeper.runOnce()
- W-B6 fixture (per ruling ¶1): two builder generations (closed gen-1 +
  open gen-2) → projection/alerting sees the open one
- attestation checker (§4): zero/positive event classification, cutoff
  semantics (historical mass never fails), fail-loud exit contract
  (missing DSN or failed query → exit 2, never 0/1)

### 7. Live context the design accounts for

- **8261654 gen-2:** already cancelled by the planner (22:05,
  `transition.rejected`, planner-return reason) — correctly NOT via
  expiry. The re-alerter's backlog scan will find its earlier
  `expiry_detection`-eligible state gone; no alert fires. History
  preserved (ruling ¶4 honored).
- **Fresh blocker (engineer lane):** planner's W-A re-dispatch via
  `POST /plans/8261653/restart-builder` failed with
  `InvalidDatetimeFormat` — the payload carried a double-suffixed
  timestamp (`…760480+00:00Z`), which PostgreSQL rejects (`Z` after an
  explicit offset). Filed by the planner as a fresh blocker; the fix
  belongs to the dispatch path owner (engineer/conduit-mcp), not this
  design. Flagged so CD-2 work and the W-A unblock don't get conflated.
