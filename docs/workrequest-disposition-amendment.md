# Amended WorkRequest disposition ledger — 46/46 rows

**Status: RATIFIED 2026-11-12.** Planner ratified all 46 verdicts as written (record `97b650f0-5398-428d-91ea-67c122f3164b`); Architect approved the disposition surface and verifier contract with revisions (record `64e1aa2b-1af1-49d0-a1ab-8b997ba8be87`). No `resolution.work_request` row has been changed by this document. It amends planner ledger `39443dee-5787-4dc7-a450-1bedc218a08e` after verifying every row against live payloads and plan state on 2026-11-12.

## Ratification

- **Planner:** RATIFY AS WRITTEN. Authority model (B): `nebula.implementation_plans` is authoritative; `resolution.work_request` is a derived read model. Append-only dispositions; no `business_status` mutation; `plan_id` backfill is a separate projection step. Engineering may land this artifact plus a read-only verifier now; live schema/data application is separate and admin-gated.
- **Architect:** APPROVED WITH REVISIONS. The disposition registry keeps the candidate columns/FKs, adds a partial unique index `WHERE supersedes_id IS NULL`, and enforces `MERGE => target_work_request_id IS NOT NULL` with NULL targets for every other verdict. A single mutable ledger row is rejected. The CREATE TABLE and 46 inserts are admin-gated; the verifier is read-only and must not write, DDL, mutate statuses/plan_id, or backfill.
- **Decisions:** Planner `97b650f0-5398-428d-91ea-67c122f3164b`; Architect `64e1aa2b-1af1-49d0-a1ab-8b997ba8be87`.
- **Thread:** `5825081e-31b8-4655-a5c2-2ca430bd092d`; comments `fdb928ce-742c-4e03-968e-3f16e4ba4e94` (Planner) and `a531423c-7f8e-4373-8ab5-29c1e0595c49` (Architect).

## Why the 37-row ledger cannot be ratified as written

- The ledger promotes `14fa4080` (CANCELLED, archived plan 8261655) and folds the COMPLETED `aefb7b6c` (plan 8261665) into it. The two carry different plans and different intent payloads.
- All 14 W-C rows the ledger calls "identical re-emissions" differ from the target in `intent`, `plan_id`, `decomposition`, `lineage` and `artifacts` after per-row identity fields are normalized away. They share a title, not a body.
- `68c8ac69` (W-B, plan 8261654) is not a duplicate of `54d8b414` (W-A, plan 8261653); it differs in intent, plan, requirements and success criteria.
- Only 4 of the ledger's 19 MERGE verdicts are content-identical after normalization: `7c44e59a`/`97f049b4` -> `45de0cbc`; `29f3e19a` -> `034cda98`; `777e8702` -> `54d8b414`.
- The ledger covers 37 rows; 9 rows were created later on 2026-11-10 (15:36-18:32Z) and need a supplement.

## Live state that frames the decision

- 46 rows: 30 DRAFT, 14 CANCELLED, 2 COMPLETED.
- 35 rows carry a plan number in `legacy_id`; 11 carry none. Plan status for those 35: **30 completed, 4 archived, 1 pending**.
- The only row projecting a pending plan is `6217db7f` (plan 8261650, WR canonicalization Option B).
- Pending plan 8261668 (Lilac W1-L2 disposition/topology schema) has **no WorkRequest at all**. A table that omits the queue cannot be the authoritative queue.
- All `plan_id` values are NULL even where `legacy_id` names a plan; there is no FK link from the table to `resolution.implementation_plan`.

## Ratified recommendation

1. **Ratify the 46 verdicts below** as the historical disposition of the table.
2. **Adopt the derived-read-model authority**: `nebula.implementation_plans` remains the authoritative queue; `resolution.work_request` is a projection. The live queue is plans with status `pending` (today: 8261650, 8261668).
3. **Do not mutate `business_status` to apply the ledger.** Record dispositions append-only so no history is rewritten. A `MERGE` stores the canonical target; it does not delete or relabel the source row.
4. **Backfill `plan_id` from `legacy_id`** as a separate, verified projection step; it is currently 0/46 and is the missing join that makes the table usable as a read model.

## Decision requested

Planner: ratify or amend the 46 verdicts and confirm the authority model. Architect: rule on the append-only disposition surface and the `plan_id` backfill. If approved, Engineering lands the ratified artifact plus a read-only verifier in one PR; live schema/data application remains a separate admin-gated step.

## Verdict summary

| Verdict | Count |
|---|---:|
| COMPILE | 1 |
| KEEP-COMPLETED | 2 |
| MERGE | 8 |
| PROMOTE | 12 |
| RETIRE | 23 |
| **Total** | **46** |

## The 46 rows

| # | Row | Status | Plan | Plan status | Verdict | Target | Reason |
|---:|---|---|---|---|---|---|---|
| 1 | `8f87c0bf` | COMPLETED | — | — | **KEEP-COMPLETED** | — | The only genuinely completed WR; keep as historical evidence. |
| 2 | `7dbf3112` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 3 | `d46cbae5` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 4 | `11bb39b8` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 5 | `ba7148f4` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 6 | `310f4e09` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 7 | `468aa85c` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 8 | `bea1c636` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 9 | `b4b2c226` | DRAFT | 8261645 | completed | **PROMOTE** | — | Canonical convergence intent; grounded as I6. |
| 10 | `09f7f5c9` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 11 | `777e8702` | DRAFT | 8261653 | completed | **MERGE** | `54d8b414` | Duplicate W-A emission; merged into the close-out source row. |
| 12 | `54d8b414` | DRAFT | 8261653 | completed | **PROMOTE** | — | Canonical W-A close-out source; plan 8261653 evidence. |
| 13 | `68c8ac69` | DRAFT | 8261654 | completed | **PROMOTE** | — | Distinct W-B intent under plan 8261654 (completed), not a duplicate of W-A row 54d8b414. Canonical W-B intent source; historical projection only. |
| 14 | `29f3e19a` | DRAFT | 8261662 | completed | **MERGE** | `034cda98` | Duplicate compile-to-plan emission; merged into canonical row. |
| 15 | `97f049b4` | DRAFT | 8261663 | completed | **MERGE** | `45de0cbc` | Duplicate of the canonical outcome-separation row; merged. |
| 16 | `7c44e59a` | DRAFT | 8261663 | completed | **MERGE** | `45de0cbc` | Duplicate of the canonical outcome-separation row; merged. |
| 17 | `45de0cbc` | DRAFT | 8261663 | completed | **PROMOTE** | — | Canonical intent row for I2. |
| 18 | `14fa4080` | CANCELLED | 8261655 | archived | **RETIRE** | — | CANCELLED attempt under archived plan 8261655; its payload differs from the 8261665 cluster (intent, plan_id, decomposition, lineage, artifacts). No open obligation. |
| 19 | `a895020d` | DRAFT | 8261664 | completed | **PROMOTE** | — | Single canonical row; grounded as I4. |
| 20 | `034cda98` | DRAFT | 8261662 | completed | **PROMOTE** | — | Canonical intent row for I3. |
| 21 | `9603e1d6` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 22 | `ea5e3696` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 23 | `b3627685` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 24 | `1d008974` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 25 | `848b3856` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 26 | `65b0292f` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 27 | `54959303` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 28 | `97da2a08` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 29 | `74c0f844` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 30 | `c7ba61a2` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 31 | `bf59a3b0` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 32 | `7be410a9` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 33 | `e2ecd36d` | CANCELLED | 8261665 | completed | **RETIRE** | — | CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold. |
| 34 | `aefb7b6c` | COMPLETED | 8261665 | completed | **KEEP-COMPLETED** | — | The one COMPLETED W-C run under plan 8261665. Keep as historical evidence; do not fold it into a CANCELLED row. |
| 35 | `0ec94c16` | DRAFT | — | — | **RETIRE** | — | Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness. |
| 36 | `e16cec45` | DRAFT | — | — | **PROMOTE** | — | Only probe expressing a reusable harness need; grounded as I8. |
| 37 | `91efbd14` | DRAFT | 8261666 | completed | **COMPILE** | — | Plan 8261666 is mature; compiled directly into I5 drafts. |
| 38 | `e079a74e` | DRAFT | 8261649 | completed | **PROMOTE** | — | Canonical WRP event-vocabulary intent under plan 8261649 (completed); distinct from 75349910 by path only. Historical projection. |
| 39 | `9cb64b7c` | DRAFT | 8261639 | archived | **PROMOTE** | — | Canonical Consolidation Wave 1 intent under plan 8261639 (archived); distinct from 166ccb73 by payload. Historical projection. |
| 40 | `166ccb73` | DRAFT | 8261639 | archived | **MERGE** | `9cb64b7c` | Content-identical to canonical 9cb64b7c after normalizing per-row identity/metadata/execution state. |
| 41 | `aef1bd80` | DRAFT | 8261645 | completed | **MERGE** | `b4b2c226` | Differs from canonical b4b2c226 only in path/artifacts/_plan_id; merge after review. |
| 42 | `75349910` | DRAFT | 8261649 | completed | **MERGE** | `e079a74e` | Differs from e079a74e only in path; merge after path review. |
| 43 | `6217db7f` | DRAFT | 8261650 | pending | **PROMOTE** | — | Canonical WR canonicalization (Option B) intent and the only row whose plan (8261650) is still pending. This is the one live projection. |
| 44 | `2de327a2` | DRAFT | 8261656 | completed | **PROMOTE** | — | Canonical Shrapnel polymorphism blueprint intent under plan 8261656 (completed); no duplicate. |
| 45 | `2c0683f7` | DRAFT | 8261661 | completed | **PROMOTE** | — | Canonical shrapnel 0009 tombstone intent under plan 8261661 (completed); no duplicate. |
| 46 | `a9a41b59` | DRAFT | 8261639 | archived | **MERGE** | `9cb64b7c` | Differs from 9cb64b7c only in artifacts; merge after artifact review. |

## Machine-readable form

```json
{
  "artifact": "work_request_disposition_ledger",
  "version": 2,
  "status": "proposed_for_ratification",
  "supersedes": "39443dee-5787-4dc7-a450-1bedc218a08e",
  "generated_at": "2026-11-12",
  "counts": {
    "KEEP-COMPLETED": 2,
    "RETIRE": 23,
    "PROMOTE": 12,
    "MERGE": 8,
    "COMPILE": 1
  },
  "rows": [
    {
      "row_id": "8f87c0bf-378c-4ca4-8a43-f137031542f3",
      "title": "Submit a test WorkRequest to verify the full WRP \u2192 Wind event bridge \u2192 Default SDLC pipeline",
      "business_status": "COMPLETED",
      "legacy_id": "vision.work_requests:bb6fe7d7-8ba9-4436-8719-dc1971012390",
      "plan_number": "",
      "plan_status": "",
      "verdict": "KEEP-COMPLETED",
      "intent": "\u2014",
      "target": null,
      "reason": "The only genuinely completed WR; keep as historical evidence."
    },
    {
      "row_id": "7dbf3112-11d8-447e-8f7f-662b22212a4f",
      "title": "T19 end-to-end correlation/causation trace",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:290e5687-1bec-4e9a-a291-8c9b1b5bfa78",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "d46cbae5-51d1-4628-a449-dd18e2c8f684",
      "title": "T19 end-to-end correlation/causation trace",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:b28dffe2-8732-4ca0-8e5b-5e11143f4238",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "11bb39b8-faca-47f9-9093-c84681ff109d",
      "title": "T19 live NATS envelope observation",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:2acfc56f-900f-4970-9895-7d09f396db15",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "ba7148f4-3fb4-48a8-9cf3-53d470cb8b86",
      "title": "double-append check",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:bf495e7d-bfe4-49ea-b7e1-3faf9c0f29fd",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "310f4e09-1af1-4743-a0cd-8a7861bb41e3",
      "title": "D-T19-1 single-envelope validation",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:bb199c54-e63a-43a3-8364-fadf6a136082",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "468aa85c-8b75-44ea-885f-2d5ca9dfdcf2",
      "title": "Post maintenance cycle summary to Assembly syslog heartbeat",
      "business_status": "DRAFT",
      "legacy_id": "nebula.work_requests_history:468aa85c-8b75-44ea-885f-2d5ca9dfdcf2",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "bea1c636-2e11-4b2c-b57a-77d052757a72",
      "title": "T26 double-submit",
      "business_status": "DRAFT",
      "legacy_id": "wr-t26-5e2e2b8aa222",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "b4b2c226-84ff-4c87-aa7c-a6f1822eed33",
      "title": "Parallel convergence tracks: Moleculer, Night Shift, ontology, and Resolution",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261645-1789964248-61f58e9c",
      "plan_number": "8261645",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I6",
      "target": null,
      "reason": "Canonical convergence intent; grounded as I6."
    },
    {
      "row_id": "09f7f5c9-6202-4262-93c9-87e82c729d07",
      "title": "t",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:09f7f5c9-6202-4262-93c9-87e82c729d07",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "777e8702-bdba-4dcc-bb69-c4416c10c896",
      "title": "W-A decomposition: duplicate-history reconciliation into M-s",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261653-1792692329-9a5d1b17",
      "plan_number": "8261653",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "I7",
      "target": "54d8b414-382a-4ab5-8700-56eb2cc7c93f",
      "reason": "Duplicate W-A emission; merged into the close-out source row."
    },
    {
      "row_id": "54d8b414-382a-4ab5-8700-56eb2cc7c93f",
      "title": "W-A decomposition: duplicate-history reconciliation into M-s",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261653-1792708414-32e2c020",
      "plan_number": "8261653",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I7",
      "target": null,
      "reason": "Canonical W-A close-out source; plan 8261653 evidence."
    },
    {
      "row_id": "68c8ac69-cf92-4afc-b1f1-ac0abc03ea73",
      "title": "W-B decomposition: Conduit/Nebula visibility unification int",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261654-1792868664-963b3b05",
      "plan_number": "8261654",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I7",
      "target": null,
      "reason": "Distinct W-B intent under plan 8261654 (completed), not a duplicate of W-A row 54d8b414. Canonical W-B intent source; historical projection only."
    },
    {
      "row_id": "29f3e19a-d3b2-49d7-bb71-acd802cdf1c0",
      "title": "Implement requirement compile-to-plan capability with confor",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261662-1793050816-82e00fd6",
      "plan_number": "8261662",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "I3",
      "target": "034cda98-b8e7-4ff8-a216-49062a102781",
      "reason": "Duplicate compile-to-plan emission; merged into canonical row."
    },
    {
      "row_id": "97f049b4-6a41-4001-8094-c4b4c146854e",
      "title": "Separate execution, work, and governance outcomes in the Con",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261663-1793050996-1412b804",
      "plan_number": "8261663",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "I2",
      "target": "45de0cbc-4c3a-4641-8846-f125d746359d",
      "reason": "Duplicate of the canonical outcome-separation row; merged."
    },
    {
      "row_id": "7c44e59a-ce29-4ae7-9192-e3aa9333b71f",
      "title": "Separate execution, work, and governance outcomes in the Con",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261663-1793051605-16e65b7a",
      "plan_number": "8261663",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "I2",
      "target": "45de0cbc-4c3a-4641-8846-f125d746359d",
      "reason": "Duplicate of the canonical outcome-separation row; merged."
    },
    {
      "row_id": "45de0cbc-4c3a-4641-8846-f125d746359d",
      "title": "Separate execution, work, and governance outcomes in the Con",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261663-1793055523-48364e5e",
      "plan_number": "8261663",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I2",
      "target": null,
      "reason": "Canonical intent row for I2."
    },
    {
      "row_id": "14fa4080-b7cf-4ed4-8f9f-48fff965678f",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261655-1793062202-bf7771f3",
      "plan_number": "8261655",
      "plan_status": "archived",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under archived plan 8261655; its payload differs from the 8261665 cluster (intent, plan_id, decomposition, lineage, artifacts). No open obligation."
    },
    {
      "row_id": "a895020d-a541-488f-8c07-c19caafc5061",
      "title": "Require positive builder work evidence before implementation",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261664-1793095130-385b43e6",
      "plan_number": "8261664",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I4",
      "target": null,
      "reason": "Single canonical row; grounded as I4."
    },
    {
      "row_id": "034cda98-b8e7-4ff8-a216-49062a102781",
      "title": "Implement requirement compile-to-plan capability with confor",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261662-1793334834-9427a1d9",
      "plan_number": "8261662",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "I3",
      "target": null,
      "reason": "Canonical intent row for I3."
    },
    {
      "row_id": "9603e1d6-e652-4c6c-9bc6-b38c9a284b67",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793374792-c2b38b2e",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "ea5e3696-760d-4143-8db8-b0a56f5ce9e8",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793389640-a6d0fdd2",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "b3627685-6d97-463c-b4a6-2b92a005f970",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793391513-ae2194d9",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "1d008974-bb7d-4747-970d-b0375b8be6b9",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793396570-2751d448",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "848b3856-a5f5-43a7-b787-ac1ea83fd270",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793400468-63ed20f2",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "65b0292f-fb55-4d00-a4e4-43891c7a7360",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793435565-9970e0b2",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "54959303-b284-426b-a102-1ca03373a50c",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793437726-25c10548",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "97da2a08-71fc-45ce-9f1b-20a18fddcf49",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793452886-1d00a3ec",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "74c0f844-eb9c-401e-97cf-d15665ddb943",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793465422-b39abb46",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "c7ba61a2-37e5-4f7b-a9a0-9af979e12a79",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793467358-744ad460",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "bf59a3b0-57af-4b97-a7d4-4f7ddeecab6a",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793473117-3f599a9a",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "7be410a9-59ba-4a16-8783-468c60b05b04",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793561543-6c1d72bc",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "e2ecd36d-d646-4442-a343-7aa9006e3013",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "CANCELLED",
      "legacy_id": "wr-8261665-1793562491-1f5fa751",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "RETIRE",
      "intent": "I1",
      "target": null,
      "reason": "CANCELLED attempt under completed plan 8261665; distinct plan_id/entity_key and differing payload. Historical only; not a duplicate to fold."
    },
    {
      "row_id": "aefb7b6c-9b58-4f77-b44d-0781d7379bcd",
      "title": "W-C decomposition: consumer migration (writer fencing + Stag",
      "business_status": "COMPLETED",
      "legacy_id": "wr-8261665-1793563377-9cd2cb86",
      "plan_number": "8261665",
      "plan_status": "completed",
      "verdict": "KEEP-COMPLETED",
      "intent": "I1",
      "target": null,
      "reason": "The one COMPLETED W-C run under plan 8261665. Keep as historical evidence; do not fold it into a CANCELLED row."
    },
    {
      "row_id": "0ec94c16-0451-4c66-86d7-d017f317851a",
      "title": "resolved-op probe, no delivery",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:0ec94c16-0451-4c66-86d7-d017f317851a",
      "plan_number": "",
      "plan_status": "",
      "verdict": "RETIRE",
      "intent": "I8",
      "target": null,
      "reason": "Ad-hoc probe/test artifact, not an obligation; retained in history, superseded by the I8 harness."
    },
    {
      "row_id": "e16cec45-2085-4a67-a968-2e6b7dd4f5ff",
      "title": "drain probe",
      "business_status": "DRAFT",
      "legacy_id": "vision.work_requests:e16cec45-2085-4a67-a968-2e6b7dd4f5ff",
      "plan_number": "",
      "plan_status": "",
      "verdict": "PROMOTE",
      "intent": "I8",
      "target": null,
      "reason": "Only probe expressing a reusable harness need; grounded as I8."
    },
    {
      "row_id": "91efbd14-f009-4c20-b9f3-213ff5626dd1",
      "title": "Ruling 32 section 4 cross-runtime parity guard (Python, SQL,",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261666-1794224419-ad19d265",
      "plan_number": "8261666",
      "plan_status": "completed",
      "verdict": "COMPILE",
      "intent": "I5",
      "target": null,
      "reason": "Plan 8261666 is mature; compiled directly into I5 drafts."
    },
    {
      "row_id": "e079a74e-63df-4f3a-9054-37146c79e5b5",
      "title": "Close WRP event-vocabulary drift + deterministic fold: narro",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261649-1794342987-eae036cc",
      "plan_number": "8261649",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "reason": "Canonical WRP event-vocabulary intent under plan 8261649 (completed); distinct from 75349910 by path only. Historical projection."
    },
    {
      "row_id": "9cb64b7c-d3c2-4f80-a62a-298a1420dd1d",
      "title": "Consolidation Wave 1 \u2014 Lilac ticket, receipt, and execution-",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261639-1794343327-feab9059",
      "plan_number": "8261639",
      "plan_status": "archived",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "reason": "Canonical Consolidation Wave 1 intent under plan 8261639 (archived); distinct from 166ccb73 by payload. Historical projection."
    },
    {
      "row_id": "166ccb73-38ac-41c0-aef1-bd392242597c",
      "title": "Consolidation Wave 1 \u2014 Lilac ticket, receipt, and execution-",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261639-1794344836-c0470656",
      "plan_number": "8261639",
      "plan_status": "archived",
      "verdict": "MERGE",
      "intent": "outside-ledger",
      "target": "9cb64b7c-d3c2-4f80-a62a-298a1420dd1d",
      "reason": "Content-identical to canonical 9cb64b7c after normalizing per-row identity/metadata/execution state."
    },
    {
      "row_id": "aef1bd80-f239-441e-a6f6-6e41f5b54abc",
      "title": "Parallel convergence tracks: Moleculer, Night Shift, ontolog",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261645-1794345779-9cd52843",
      "plan_number": "8261645",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "outside-ledger",
      "target": "b4b2c226-84ff-4c87-aa7c-a6f1822eed33",
      "reason": "Differs from canonical b4b2c226 only in path/artifacts/_plan_id; merge after review."
    },
    {
      "row_id": "75349910-77d2-4922-9ab4-a7d27dc4a91f",
      "title": "Close WRP event-vocabulary drift + deterministic fold: narro",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261649-1794346267-ef97c55c",
      "plan_number": "8261649",
      "plan_status": "completed",
      "verdict": "MERGE",
      "intent": "outside-ledger",
      "target": "e079a74e-63df-4f3a-9054-37146c79e5b5",
      "reason": "Differs from e079a74e only in path; merge after path review."
    },
    {
      "row_id": "6217db7f-577d-4a6d-9708-61167279aa2e",
      "title": "WR canonicalization landing (Option B): legacy_id backfill,",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261650-1794346898-4b191d51",
      "plan_number": "8261650",
      "plan_status": "pending",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "reason": "Canonical WR canonicalization (Option B) intent and the only row whose plan (8261650) is still pending. This is the one live projection."
    },
    {
      "row_id": "2de327a2-9397-44db-b945-af3bcd966ce8",
      "title": "Shrapnel polymorphism and storage-adapter conformance matrix",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261656-1794347493-4743b807",
      "plan_number": "8261656",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "reason": "Canonical Shrapnel polymorphism blueprint intent under plan 8261656 (completed); no duplicate."
    },
    {
      "row_id": "2c0683f7-ddb8-4f2e-a407-6a4e13a5b79e",
      "title": "shrapnel 0009: retire/tombstone verb for the EAV catalog (co",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261661-1794348274-726191b3",
      "plan_number": "8261661",
      "plan_status": "completed",
      "verdict": "PROMOTE",
      "intent": "outside-ledger",
      "target": null,
      "reason": "Canonical shrapnel 0009 tombstone intent under plan 8261661 (completed); no duplicate."
    },
    {
      "row_id": "a9a41b59-e8c8-4fb7-9f91-08ca1f1a0c0b",
      "title": "Consolidation Wave 1 \u2014 Lilac ticket, receipt, and execution-",
      "business_status": "DRAFT",
      "legacy_id": "wr-8261639-1794353559-170f1da5",
      "plan_number": "8261639",
      "plan_status": "archived",
      "verdict": "MERGE",
      "intent": "outside-ledger",
      "target": "9cb64b7c-d3c2-4f80-a62a-298a1420dd1d",
      "reason": "Differs from 9cb64b7c only in artifacts; merge after artifact review."
    }
  ]
}
```
