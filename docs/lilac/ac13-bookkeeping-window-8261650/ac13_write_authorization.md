# DRAFT — authorization record for the AC13 annotation + blueprint reconciliation write

**Not posted. Requires explicit operator approval before it is written.**

Command (to be run by the acting role, after operator approval):

```bash
python3 bin/post-agent-record.py -r engineer-ii \
  -t "Operator-approved AC13 annotation + pending-blueprint reconciliation for plan 8261650 (window R2)" \
  --tags "type:status-update,plan:8261650,ac13,window,operator-approved" \
  --model "$NEXUS_AGENT_MODEL" -F /tmp/ac13_write_authorization.md
```

Body:

## Authorization — AC13 annotation + blueprint reconciliation (plan 8261650)

The operator has approved a bounded follow-on to the live Stage-6 / AC13 bookkeeping
window (`25ba5496-82d5-42d7-8118-52d66e39b858`), which forbade exactly what this
record now authorizes. This record is the authorization for the write; the write is
run only after it is posted.

### What will change (one transaction, `nebula.blueprints_history`)

1. `8261650` — `payload.acceptance_criteria[12]` (the AC13 clause) is replaced with
   the close-out evidence string. The original criterion sentence is preserved
   verbatim inside the new string. No other criterion is touched (indexes 0–11 are
   byte-identical and asserted before the write).
2. `8261650`, `8261668`, `8261673` — `blueprint_status` `pending` → `completed`, to
   match the receipt-derived `derived_status = REVIEW_PASS` for all four 826164x/6x
   plans. `updated_at = now()` on each.

Explicitly **not** in scope: any WorkRequest row, `plan_id` backfill, requirement or
artifact link, schema/DDL, receipt issue, replication, kernel restart, unit file, or
the `wr-8261673` disposition-ledger drift.

### Pre-image (captured before the write; rollback literal)

- `8261650` payload md5 `207c537d067efbcaef0a61d60d92daf4`, `acceptance_criteria` md5
  `652e65763e4f9002a15fc9e6921dddda`, `blueprint_status` `pending`, `updated_at 2026-11-16 07:00:11+00`.
- `8261668` payload md5 `95f5f6263f5e193efe412456fa551514`, `blueprint_status` `pending`,
  `updated_at 2026-11-15 13:32:12+00`.
- `8261673` payload md5 `2354af0cb8d457bb06e41ebc11a591d4`, `blueprint_status` `pending`,
  `updated_at 2026-11-12 23:07:59+00`.
- Full pre-image JSON (13-element array) embedded in the write file.

### Execution

`/tmp/ac13_annotation_8261650.sql` (guarded, `ON_ERROR_STOP`): row locks →
pre-state guard (3 rows present / 3 pending / AC13 still verbatim) → payload update
guarded on the exact pre-image (expect `UPDATE 1`) → status update (expect
`UPDATE 3`) → in-transaction postconditions → `COMMIT`.

### Rollback

```sql
BEGIN;
UPDATE nebula.blueprints_history
   SET payload = jsonb_set(payload, '{acceptance_criteria}', $before$<BEFORE_JSON: the 13-element array embedded in /tmp/ac13_annotation_8261650.sql>$before$::jsonb, false),
       updated_at = now()
 WHERE plan_number = '8261650';
UPDATE nebula.blueprints_history
   SET blueprint_status = 'pending', updated_at = now()
 WHERE plan_number IN ('8261650','8261668','8261673');
COMMIT;
```

Rollback restores values only; the `NEBULA_AUDIT` rows in `tackle.system_logs` and
this record stay (append-only evidence).

### Verification after

- `ac13_verify_8261650.py` → `AC13 PASS — 133/133 markers`, exit 0 (unchanged;
  it does not read criterion text or blueprint status).
- `nebula.plan_status.derived_status` = `REVIEW_PASS` for 8261649/8261650/8261668/8261673
  (receipt-derived; must not change).
- `nebula.blueprints_history.blueprint_status` = `completed` for the three rows.
- `bin/verify-workrequest-disposition.py`: **expected to gain failures** — it asserts
  `6217db7f` projects plan 8261650 `pending` and expects exactly one pending plan.
  Its ratified ledger expectation must be updated in the same change set, or the
  blueprint write is held.
