# M1 Traffic Canary — zero-search-traffic observation procedure

> **Purpose:** the queryable evidence surface for the M1 sign-off gate item
> "zero-legacy-traffic observed" (To Do `b3f8bf70`) and the Day-0
> "invocation-lineage canary" requirement (discussion `5e149937`).
> **Scope:** search cutover only (Day 1). Other services get their own
> canary rows when their waves start.

## Why this exists

`BrokerTrafficStreamService.publish()` was SSE-fanout only: events went to
currently-connected subscribers, nothing was stored. An observation window
with no subscriber is unprovable — so "zero traffic observed" could never
be evidenced. The canary adds cumulative counters on both sides.

## Endpoints (both in-memory; a restart resets the window — see below)

| Side | Endpoint | Shape |
|---|---|---|
| Legacy (`:8081`) | `GET /api/v1/broker/traffic/counts` | `{startedAt, total, counts: {"service/operation": n, ...}}` |
| Moleculer (`:4050`) | `GET /api/traffic/counts` | `{startedAt, total, counts: {"action.name": n, ...}}` |

Legacy counts every `submitRequest`/`testBroker` dispatch at the
`BrokerController.publishTrafficEvent` choke point (same events as the SSE
stream). Moleculer counts every local action invocation via the
`TrafficCounter` broker middleware (covers direct broker calls, not just
HTTP aliases).

## Observation procedure (sign-off gate)

1. **Baseline (pre-migration):** record both snapshots. Expect legacy
   `googleSearchService/simpleSearch|forceSearch` > 0 (IdeaStream traffic),
   moleculer counts at whatever the current callers produce.
2. **Migrate** the caller (IdeaStream → `:4050`, slice 4).
3. **Zero-window:** poll both endpoints over the agreed window (suggest
   ≥24h covering a full IdeaStream usage cycle). PASS iff the legacy
   search counts do not increase while moleculer `google-search.*`
   counts do. `testBroker` and unrelated services are out of scope —
   compare only the search rows.
4. **Reset discipline:** if either unit restarts mid-window (`startedAt`
   moves), the window restarts. Never compare counts across a restart.

## Worked example (curl + jq)

```bash
# Baseline
curl -s http://localhost:8081/api/v1/broker/traffic/counts | jq '{startedAt, search: {simple: .counts["googleSearchService/simpleSearch"], force: .counts["googleSearchService/forceSearch"]}}'
curl -s http://localhost:4050/api/traffic/counts | jq '{startedAt, search: .counts["google-search.simpleSearch"]}'
# ... migrate, wait, repeat. Legacy deltas must be zero.
```

## Non-goals

Per-caller identity (the `source` event field is constant today);
cross-service canary rows (added per wave); persistent history
(deliberately in-memory — the gate needs window truth, not a ledger).
