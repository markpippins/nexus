# Lilac cutover rehearsal (plan 8261672, W1-L6)

This is the cutover boundary for Lilac Wave 1: producers and consumers stop
reading the legacy receipt surfaces and read the canonical `resolution`
surfaces instead. This document records the rehearsal procedure and the
boundary rules. It is **staged-inert**: nothing here has been applied to a live
database, and nothing here repoints a producer.

## Boundary rule

| Step | Owner | Gate |
|------|-------|------|
| Measure dual-read parity | builder | `bin/lilac-parity-check.py --check` exits 0 |
| Prove zero direct legacy writes | builder | write-audit delta `clean: true` |
| Prove replay equivalence | builder | `replay_equivalence(...)["ok"] is True` |
| Rehearse rollback on a throwaway DB | builder | `bin/tests/test_lilac_parity_gate.py` AC4 green |
| Drop legacy tables | **operator** | separate approval citing plan 8261672 |

The dropped step is not automatable from this change set: the tool has no drop
mode, and `--print-sql legacy-drop` prints the gated step's expected content to
stderr and exits 2 without emitting DDL.

## What the rehearsal does

The rehearsal treats one legacy surface (`vision.receipts`) as the boundary a
consumer sees, because the rollback question is about the *read path*, not about
which tables exist.

1. **Seed a throwaway database** with the minimum cutover needs: a legacy
   `vision.receipts` table holding two rows, and `resolution.receipt` holding
   one canonical twin.
2. **Cut over** (`--print-sql cutover`): rename the legacy table to
   `vision.receipts_pre_cutover` and create `vision.receipts` as a view over
   `resolution.receipt`. The legacy table is retained, never dropped.
3. **Observe**: the same name now answers with canonical data (one row), and the
   retained copy still holds both legacy rows.
4. **Roll back** (`--print-sql rollback`): drop the view (only if it exists) and
   rename the retained table back. The rollback is idempotent — a second run is
   a no-op, which is what makes it usable as an incident step rather than a
   one-shot.
5. **Verify**: the pre-cutover read path answers again with the original two
   rows, and neither the view nor the retained copy is left behind.

Recording obligations for a real rehearsal run: the transcript must include the
`measured`/`matched`/`mismatched`/`unreadable` counts, the writer list from the
write-audit delta (or `clean: true`), and the post-rollback verification lines.
A number without its rollback transcript is not evidence of a reversible
cutover.

## Why parity fails closed

`parity_report` returns separate `matched`, `mismatched` and `unreadable`
counts and a `fail_closed` flag. A ratio is never emitted as the result: 999
matching rows and 1 unexplained `missing_twin` is a failure, not 99.9% parity.
`unmapped_type` is the only mismatch class treated as explained — it is a
legacy-only receipt type the ratified contract never assigned a canonical kind
to, so it has nothing to disagree with.

An unreadable row (no id, a malformed twin, or a read that raised) is counted in
`unreadable` and fails the gate. It is never skipped, because a row that cannot
be read cannot be shown to agree.

## Why the write audit is a delta, not a flag

`pg_stat_user_tables` counters are cumulative. Silence can only be proven by
differencing two snapshots taken around the observation window — a single
snapshot says how much has happened since the counters were last reset, which
is not the window. The delta names every legacy table that took an
insert/update/delete, and it reports a missing table or a counter that went
backwards as **unreadable evidence** rather than as zero writes: an absent
counter cannot prove absence of writes.

## Scope exclusions

This plan does not absorb a WorkRequest representation, does not add a request
kind axis, and does not activate PEB decisions authority. A guard scans this
change set's code for those surfaces and fails if any appear, so the exclusion
is enforced by the build rather than by review attention.
