# Which surface wins for a plan's status — and why `blueprint_status` goes stale

**Written:** 2026-11-21 · **Status:** analysis + recommendation. No DDL applied, no further writes made
beyond the two bookkeeping rows already reported (record `1eeed588`).

## 1. The question

Plan `8261668` was `pending` in every plan surface while the ticket layer showed it finished. Which
surface is authoritative, and why do the others drift?

## 2. What the surfaces actually are (measured, live)

| Surface | Kind | Where its status comes from |
|---|---|---|
| `resolution.receipt` | table | The **event stream**: one row per lifecycle fact (`plan_create`, `implementation`, `review_pass`, `block`, `review_reject`, …), keyed in `payload->>'plan_id'` |
| `nebula.receipts_unified` | **view** | Projects `resolution.receipt`-family rows; column `plan_id` |
| `nebula.plan_status` | **view** | `derived_status` = latest receipt of the plan (with `HOLD`/`CANCELLED` precedence) |
| `nebula.blueprints_history` | **table** (canonical row store, V171 / ruling `5e6d4b02`) | `blueprint_status` — a **denormalized copy** written at row-creation time |
| `nebula.v_blueprints` | view | `blueprint_status` of bitemporally-current rows |
| `nebula.implementation_plans` | view | `blueprint_status AS status`, folded over `payload` |

So `implementation_plans` is **not** a table and not authoritative: it is a compatibility fold over
`blueprints_history`. Writing to it writes through to the canonical table (it is auto-updatable), which is
why the `8261668` correction propagated to all three views at once.

## 3. The answer

**The receipt stream is the truth for status.** `blueprint_status` is a cache. `plan_status.derived_status`
is the intended derivation of the receipt stream and is the surface conduit reads.

Receipt stream for the five open plans (latest receipt per plan, `resolution.receipt`):

| Plan | Latest receipt | Meaning |
|---|---|---|
| `8261650` | `block` (2026-11-17) | not complete |
| `8261668` | `review_pass` (2026-11-16) | **complete** |
| `8261673` | `review_pass` (2026-11-16) | **complete** |
| `8261674` | `implementation` (2026-11-17) | review outstanding |
| `8261675` | `implementation` (2026-11-19) | review outstanding (ticket expired 11-20) |

**Correction to the previous report.** I reported `8261673` as unsupported because I searched
`kernel.receipt` and `kernel.transition_event`. Those are the **ticket** layer. The plan's lifecycle lives
in `resolution.receipt`, which has both `implementation` and `review_pass` for `8261673` on 2026-11-16.
The source claim was right; my evidence path was wrong. The row is synced accordingly in this change.

## 4. Why the cache goes stale — the actual mechanism

Two independent defects, and **neither is a live mirror trigger**:

1. **No writer on status change.** The only thing that ever wrote `blueprint_status` was the V171
   write-through mirror, an `AFTER INSERT` trigger on the *frozen legacy* table
   `nebula.implementation_plans_history`. It is not attached in the live database at all (the only trigger
   on `blueprints_history` is `trg_blueprints_audit`). Even where staged, `ON CONFLICT (id) DO NOTHING`
   means an `UPDATE` — which is what a status change is — can never propagate. V176 retires it outright
   once writers are cut over.
2. **The receipt→plan join is broken by identifier type.** `nebula.receipts_unified.plan_id` mixes key
   spaces: **171** rows carry a 7-digit plan number (`8261668`), **196** carry a UUID, **1934** carry
   something else. `nebula.plan_status` joins `r.plan_id = p.id` where `p.id` is a **uuid**. Every receipt
   keyed by plan number therefore fails to attach, `derived_status` comes back `NULL`, and every
   downstream view falls back to the stale `blueprint_status`. That is why the plan reads `pending` even
   when a `review_pass` receipt exists.

Defect 2 is the one to fix first: it makes the derived surface *wrong even when the cache is right*.

## 5. Recommended fix (three parts, in this order)

### 5.1 Resolve the identifier split (one migration, read-only to consumers)

```sql
-- Plan A (preferred): a view that resolves both key shapes, no data rewrite.
CREATE OR REPLACE VIEW nebula.v_plan_status_live AS
SELECT bh.plan_number,
       bh.blueprint_status                                   AS cached_status,
       (SELECT r.type
          FROM nebula.receipts_unified r
         WHERE r.plan_id IN (bh.plan_number, bh.id::text)
           AND r.type NOT IN ('PLANNING')
         ORDER BY r.created_at DESC
         LIMIT 1)                                            AS receipt_status
  FROM nebula.blueprints_history bh
 WHERE now() >= bh.recorded_on_dt AND now() < bh.recorded_until_dt;

-- Drift is now a query, not a suspicion:
SELECT plan_number, cached_status, receipt_status
  FROM nebula.v_plan_status_live
 WHERE receipt_status IS NOT NULL
   AND upper(receipt_status) <> upper(cached_status);
```

Normalizing `receipts_unified` itself (rewriting 171 numeric keys to UUIDs) is the cleaner end state but
touches a 2,301-row stream; do it only with the receipts owner's sign-off and a checksum manifest.

### 5.2 A versioned status writer (replaces the retired insert-only mirror)

`blueprints_history` is bitemporal: a status change is **close + insert**, never an in-place `UPDATE`,
so the prior state stays queryable.

```sql
CREATE OR REPLACE FUNCTION nebula.fn_blueprint_set_status(
    p_plan_number text, p_status text, p_reason text DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v_row nebula.blueprints_history; v_new_id uuid := gen_random_uuid();
BEGIN
    SELECT * INTO v_row FROM nebula.blueprints_history
     WHERE plan_number = p_plan_number
       AND now() >= recorded_on_dt AND now() < recorded_until_dt
     ORDER BY recorded_on_dt DESC LIMIT 1;

    IF NOT FOUND THEN RAISE EXCEPTION 'no open blueprint for plan %', p_plan_number; END IF;
    IF v_row.blueprint_status = p_status THEN RETURN v_row.id; END IF;   -- idempotent

    UPDATE nebula.blueprints_history
       SET recorded_until_dt = now(), valid_until = now()
     WHERE id = v_row.id;

    INSERT INTO nebula.blueprints_history
        (id, plan_number, title, payload, blueprint_status, created_at, updated_at,
         valid_from, valid_until, recorded_on_dt, recorded_until_dt, asset_id)
    SELECT v_new_id, v_row.plan_number, v_row.title,
           v_row.payload || jsonb_build_object('status_reason', p_reason),
           p_status, v_row.created_at, now(), now(), 'infinity', now(), 'infinity', v_row.asset_id;

    RETURN v_new_id;
END $$;
```

### 5.3 Drive it from the stream, not from hope

Two options; they are not exclusive.

| Option | Mechanism | Trade-off |
|---|---|---|
| **A. Reconciliation job (start here)** | `bin/reconcile-blueprint-status.py --dry-run\|--apply` compares `v_plan_status_live` to `cached_status` and calls `fn_blueprint_set_status` per drifted plan; timer-driven, idempotent | No trigger on an event path; a drift window of one timer period; easy to audit and to stop |
| **B. Event-driven writer** | Trigger on the receipt insert path calling the same function | Zero drift window, but `receipts_unified` is a view (needs `INSTEAD OF` on the write side, fragile) — belongs in the receipt-issuing code path in a transaction, not as a view trigger |
| C. Re-enable the INSERT-only mirror | V171 as-is | **Rejected.** It cannot see `UPDATE`s, which is the only event that matters, and it feeds a table that V176 freezes |

Recommendation: ship 5.1 + 5.2 + option A now (small, reversible, immediately removes phantom
backlog), then fold option B into the next conduit change that touches receipt issuance.

## 6. Do not do this

- Do not `UPDATE nebula.blueprints_history SET blueprint_status = …` in place as a routine repair. It
  destroys the bitemporal record of the prior state; the 2026-11-21 correction was a one-off
  reconciliation with an explicit evidence citation, not a pattern to copy.
- Do not derive status from `kernel.transition_event`. That is the ticket lifecycle; a plan can have
  `review_pass` receipts and no kernel ticket events at all (`8261673` is exactly that case).
- Do not treat `nebula.implementation_plans` as a table to be corrected. It is a view over
  `blueprints_history`; correcting it *is* correcting the canonical table.
