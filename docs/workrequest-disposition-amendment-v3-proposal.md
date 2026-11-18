# Proposed v3 amendment to the WorkRequest disposition ledger — one row (`66b9d2e0`)

**Status: PROPOSED — NOT RATIFIED, NOT APPLIED.** This is a proposal for the
Planner. It is not the ledger, it carries no `ratified_by` record, and it changes
no `resolution.work_request` row. `bin/verify-workrequest-disposition.py` still
reads `docs/workrequest-disposition-amendment.json` (v2, ratified 2026-11-12) and
ignores this file. Until the Planner ratifies, the only standing statement about
`66b9d2e0` is the disposition guard's identity pin — residual 5 of
[`ac13-bookkeeping-window-8261650.md`](lilac/ac13-bookkeeping-window-8261650.md).

## Why a v3 amendment is needed

Ledger v2 was ratified 2026-11-12 (Planner record
`97b650f0-5398-428d-91ea-67c122f3164b`; Architect
`64e1aa2b-1af1-49d0-a1ab-8b997ba8be87`) over a 46-row snapshot of
`resolution.work_request`. Row `66b9d2e0-dfd2-4589-9d7f-55192825a9d8` was created
**2026-11-14T05:07:10.503916Z**, two days after that ratification, so the 46-row
snapshot could not have covered it. It is out of window by construction, not
overlooked — which is why the guard pins it instead of failing closed as
"unclassified".

A pin records *where* a row sits; it does not give the row a disposition. Only a
ratified amendment can, so the row needs a v3.

## Live row, re-read 2026-11-18 (read-only)

| Field | Value |
|---|---|
| `id` | `66b9d2e0-dfd2-4589-9d7f-55192825a9d8` |
| `legacy_id` | `wr-8261673-1794650830-98004713` |
| `title` | Remove orphaned escalates_to expression blocking V217 preflight |
| `business_status` | `DRAFT` |
| `request_kind` | `build` |
| `plan_id` | NULL — no backfill has happened, as v2 defers it |
| `created_at` | 2026-11-14T05:07:10.503916Z |
| `updated_at` | 2026-11-14T05:07:10.503916Z (unchanged since creation) |
| Plan `8261673` | `nebula.implementation_plans.status = 'pending'`; receipt-derived `derived_status = REVIEW_PASS` |
| Rows for plan `8261673` | 1 — this is the only `wr-8261673-*` row |

Execution evidence under plan 8261673, from `resolution.receipt`:
`implementation` `171be0d9-2551-4292-a2f8-c670bf1b2bf4` ("Record existing merged
implementation: PR #983 cleanup at merge commit 44723cfb") and `review_pass`
`93002581-122e-470e-bccb-8fd561f75a2c` ("R1-R7 verified for plan 8261673; closure
only"). The row's own `execution_state` is `pending`, progress `0.0`: it was
emitted as the plan's build intent and then blocked (`block` receipt
`045d7baf-c702-4972-946d-c96e092bc103`, "no model chain resolved for
role=builder"). The work landed under the plan, not through this row.

## Proposed amendment — one row, one verdict

| Field | Proposed value |
|---|---|
| Verdict | **PROMOTE** |
| Target | `null` (non-MERGE) |
| `intent` | `outside-ledger` (recommended; see below) |
| `plan_number` | `8261673` |
| `plan_status` | `pending` |

Rationale:

- **PROMOTE** is v2's verdict for a plan's canonical intent row — precedent
  `6217db7f` (plan 8261650 pending), and `b4b2c226` / `45de0cbc` under completed
  plans. `66b9d2e0` is the single intent row for plan 8261673.
- **Not KEEP-COMPLETED**: reserved for rows whose `business_status` is
  `COMPLETED`. This row is `DRAFT`, and v2 never mutates status to fit a verdict.
- **Not MERGE**: there is exactly one row for plan 8261673, so there is no
  canonical row to fold it into.
- **Not RETIRE**: it is not an ad-hoc probe. It names a real plan whose
  implementation merged (PR #983) and whose review passed.
- **Not COMPILE**: plan 8261673 was not compiled into drafts; it executed and
  merged.

The `intent` label is the one open vocabulary choice. v2 uses `I1`–`I8` plus
`outside-ledger`, and none of `I1`–`I8` describes the V217 / `escalates_to`
cleanup. `outside-ledger` is recommended because v2 already uses it for rows that
postdate the 37-row ledger it superseded, so no new vocabulary entry is needed. If
the Planner prefers a new label, that is a Planner naming act — this proposal does
not mint one.

## Proposed artifact delta, if ratified

| Counter | v2 (ratified) | v3 (proposed) |
|---|---:|---:|
| total rows | 46 | **47** |
| PROMOTE | 12 | **13** |
| COMPILE / KEEP-COMPLETED / MERGE / RETIRE | 1 / 2 / 8 / 23 | unchanged |
| `business_status` DRAFT | 30 | **31** |
| `business_status` CANCELLED / COMPLETED | 14 / 2 | unchanged |
| plan_status `pending` | 1 | **2** |
| plan_status completed / archived / `""` | 30 / 4 / 11 | unchanged |
| artifact `version` | 2 | **3** |

v2 has no artifact UUID of its own, so the v3 `supersedes` points at the v2 file
rather than at another ledger id. The v2 `supersedes: 39443dee-…` (the original
37-row planner ledger) stays as history. Drafted against
`docs/workrequest-disposition-amendment.json` sha256
`be060670745ab48b3e2914394178ca50764272754b367dc6a603a0e359f5fd24`.

## What the ratification act must contain

One Planner record: `recordType: decision`, tagged to the engineer
(`to:engineer`, `type:decision`), stating that v3 is ratified with 47 rows and
this single new row and verdict as written. That record's id becomes
`ratified_by.planner.record_id`; `ratified_at` is its date. No agent may
pre-fill either.

Then a separate applying change, which nobody may skip:

1. In `bin/verify-workrequest-disposition.py`: `VERSION` → 3, `EXPECTED_TOTAL`
   → 47, `EXPECTED_COUNTS["PROMOTE"]` → 13, `EXPECTED_STATUS["DRAFT"]` → 31.
   `LIVE_QUEUE_EXPECTED_STATUS` is untouched — plan 8261650 is still pending.
2. Add the row to `docs/workrequest-disposition-amendment.json` with `ledger_ref`
   set to the ratification record id.
3. Remove `66b9d2e0` from `PINNED_OUT_OF_WINDOW_ROWS` / `PINNED_ROW_REASONS` and
   update `test_66b9d2e0_is_pinned_as_out_of_window_not_dispositioned` — a pin
   must not outlive the verdict it stood in for.
4. Amend residual 5 of the window doc to closed, citing the ratification record;
   that doc is a historical record, so it is amended, never deleted.
5. Re-run the verifier suite (must be green at 47 rows) and
   `ac13_verify_8261650.py` (must stay `133/133`; the AC13 baseline is unaffected
   by a ledger verdict).

Question left to the Planner: v2's Architect approval covers the disposition
surface and this verifier's contract, and a one-row addition changes neither, so
this proposal assumes Planner ratification alone is sufficient. If the Planner
wants a joint decision, it must be recorded before ratification.

## What ratification does not authorize

- No `business_status` mutation and no `plan_id` backfill — v2 keeps
  `live_schema_data_application: admin_gated_separate_step`.
- No change to plan 8261673's blueprint status; that stays residual 2 under its
  own operator approval.
- No re-opening of the AC13 window: no new disposition comments, no third
  close-out receipt, no plan-text edit.
- No change to the two `#8261674` pins.

## Confirm the row has not moved before ratifying

```bash
PGPASSWORD=pgpass psql -h localhost -U pguser -d nexus -Atc \
  "select business_status || '|' || coalesce(plan_id,'') || '|' || created_at \
     from resolution.work_request where id='66b9d2e0-dfd2-4589-9d7f-55192825a9d8';"
# DRAFT||2026-11-14 05:07:10.503916+00

python3 bin/verify-workrequest-disposition.py
# OK: ratified artifact matches live state   (exit 0; 66b9d2e0 named as a pinned warning)
```

If that row's status, `plan_id`, or creation time has changed, this proposal's
premise changed with it — re-derive the verdict before ratifying.

## Machine-readable draft (proposed, not ratified)

```json
{
  "artifact": "work_request_disposition_ledger",
  "version": 3,
  "status": "proposed_for_ratification",
  "supersedes": {
    "artifact": "docs/workrequest-disposition-amendment.json",
    "version": 2,
    "sha256": "be060670745ab48b3e2914394178ca50764272754b367dc6a603a0e359f5fd24"
  },
  "generated_at": "2026-11-18",
  "counts": {
    "KEEP-COMPLETED": 2,
    "RETIRE": 23,
    "PROMOTE": 13,
    "MERGE": 8,
    "COMPILE": 1
  },
  "rows": [
    {
      "row_id": "66b9d2e0-dfd2-4589-9d7f-55192825a9d8",
      "title": "Remove orphaned escalates_to expression blocking V217 preflight",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261673-1794650830-98004713",
      "plan_number": "8261673",
      "plan_status": "pending",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "ledger_ref": null,
      "rationale": "Only WorkRequest for plan 8261673 (implementation merged as PR #983, review_pass receipt 93002581); canonical build intent row for that plan, whose blueprint status is still pending."
    }
  ],
  "ratified_at": null,
  "ratified_by": null
}
```

`ledger_ref`, `ratified_at` and `ratified_by` are deliberately `null`: they are
filled only by the ratification act, never by the author of this proposal.
