# Staged patch — V221-safe requirement delete guard

**Status: prepared, NOT applied.** `sql/V222__port_requirements_to_resolution.sql`
is unmodified. The patch is applied only after operator approval.

## What it changes

One statement in `resolution.requirement_delete_blockers(uuid, uuid[])`. The
`work_requests` blocker branch currently reads the legacy shortcut:

```sql
FROM resolution.work_request w
WHERE w.source_requirement_id = p_id
```

The patch replaces it with a branch that:

1. reads `resolution.request_requirement_link` where `relation = 'source'`
   (the normalized reference V220/V221 establish), and
2. still sees the legacy shortcut while it exists, through
   `to_jsonb(w) ->> 'source_requirement_id'` — no static column reference, so
   the function is valid on both sides of V221.

It also corrects the two comments that describe the blocker set.

## Why

V221 drops `resolution.work_request.source_requirement_id`
(`sql/V221__ac11_drop_source_requirement_shortcut.sql:104`) and asserts it is
gone (`:247`), while V222's guard reads it at `:712`–`:716`. Applied in the
plan-of-record order, `CREATE FUNCTION` still succeeds (plpgsql resolves column
references at first execution), so V222's postconditions pass and the failure
appears on the first requirement delete or cascade: the pre-check raises instead
of returning blockers, so a delete that should be a 409 becomes a 500.

The V222 test fixture recreates the pre-V221 column on purpose
(`bin/tests/test_v222_requirements_port.py:221`–`:226`), so the existing suite
cannot catch it. The order test added alongside this patch does.

## Apply (after approval)

```bash
git apply sql/patches/V222-v221-safe-delete-guard.patch
```

Then remove the `@pytest.mark.xfail(...)` decorator above
`test_delete_guard_is_v221_safe` in `bin/tests/test_v222_requirements_port.py`.
It is strict, so leaving it in place makes the now-passing test fail as XPASS —
that failure is the reminder, not a regression.

## Verify

```bash
# Before applying: the order test fails against the committed migration
python -m pytest bin/tests/test_v222_requirements_port.py -k v221_safe --runxfail
# -> FAILED on `WHERE w.source_requirement_id = p_id` (42703)

# The staged patch, verified without touching V222
diff -u sql/V222__port_requirements_to_resolution.sql /tmp/v222_patched.sql >/dev/null
V222_MIGRATION_PATH=/tmp/v222_patched.sql \
  python -m pytest bin/tests/test_v222_requirements_port.py -k v221_safe --runxfail
# -> 1 passed
```

After applying, run the whole `bin/tests/test_v222_requirements_port.py` module
and `bin/tests/test_requirement_delete_guard_parity.py`, then the runbook §7
delete/cascade probe on the target.

## Evidence (2026-11-21, throwaway databases only)

- Committed V222: `-k v221_safe` → `1 xfailed`; `--runxfail` → `1 failed`, with
  the guard's `WHERE w.source_requirement_id = p_id` statement raising.
- Patched copy (`V222_MIGRATION_PATH=/tmp/v222_patched.sql`, `--runxfail`) →
  `1 passed`.
- Full module on committed V222: no regressions (reported in the commit).
- No live or shared database was contacted; each test creates and drops its own
  database.
