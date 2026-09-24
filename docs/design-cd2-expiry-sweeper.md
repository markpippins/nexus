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

### 4. What this design deliberately does NOT do

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

### 5. Tests (hermetic, mirroring ticket-sweep.test.ts)

- fingerprint stability: same inputs → same fingerprint; any field
  change → different fingerprint
- scope resolution: implementation/blueprint/none → recipient sets
- dedupe: first sight emits; repeat suppresses; changed state emits
- cursor crash-replay: unprocessed events reprocessed, no dupes
- failure isolation: re-alerter throw does not fail sweeper.runOnce()
- W-B6 fixture (per ruling ¶1): two builder generations (closed gen-1 +
  open gen-2) → projection/alerting sees the open one

### 6. Live context the design accounts for

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
