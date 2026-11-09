# solscript_framing_parity — Ruling 32 §4 cross-runtime framing parity guard

A **shadow-only, version-bound** proof that the E8.4 framing gate agrees across
the three independent implementations that enforce it, over one shared matrix.

| Runtime | Implementation | Gate shape |
|---|---|---|
| Python | `python/SOLScript/solscript/interpreter.py` (`ResolutionInterpreter.evaluate_proposition`) | type-level required dimensions (`semantic_type_required_dimensions`), post-#916 |
| SQL | `resolution.evaluate_proposition(uuid, text, jsonb)` created by `schemas/migrations/resolution/resolution_migration_v32.sql` | framed-only (instance frame count) |
| TypeScript | `typescript/solscript/src/interpreter.ts` (`ResolutionInterpreter.evaluateProposition`) | instance frame count |

Nothing here changes admission, production behaviour, or the database. Python
and TypeScript run entirely in memory; SQL runs against a throwaway database
that the guard creates and drops. **No migration is applied to any existing
database and the live `nexus` database is never touched.**

## Run

```bash
cd nexus
PGPASSWORD=pgpass PYTHONPATH=python python3 -m solscript_framing_parity
```

`PGPASSWORD` (or `~/.pgpass`) is only needed for the SQL leg; `PGHOST`,
`PGPORT` and `PGUSER` default to the repo's local development endpoint
(`localhost:5432`, `pguser`). `node` and `tsc` must be on `PATH`.

Useful flags: `--json` (machine-readable report), `--update-pins` (refresh the
version pins after re-verifying a runtime change), `--keep-sql-database` /
`--keep-ts-temp` (debugging only, breaks hermeticity).

Exit codes: `0` contract holds · `1` parity failure or a runtime could not run ·
`2` version drift (pinned interpreter/migration file changed).

Tests:

```bash
PGPASSWORD=pgpass PYTHONPATH=python python3 -m pytest python/solscript_framing_parity -q
```

The end-to-end tests skip themselves when `node`, `tsc` or PostgreSQL is
unavailable; the contract/pin/Python-runtime tests always run.

## The shared matrix

One fixture (a `channel` governed-reference dimension plus a `tenant`
dimension, one entity, one proposition) is rebuilt identically in every
runtime. Each case feeds the same instance frame values and caller context.

| Case | Python | TypeScript | SQL | Verdict |
|---|---|---|---|---|
| `zero_required_unframed` | Asserted/pass/not_scoped | Asserted/pass/not_scoped | Asserted/pass/not_scoped | AGREE |
| `supplementary_absent_context` | Asserted/pass/not_scoped | null/fail/context_required | null/fail/context_required | DIVERGE F3 |
| `supplementary_matching_context` | Asserted/pass/not_scoped | Asserted/pass/scoped | Asserted/pass/scoped | DIVERGE F3 |
| `supplementary_contradicting_context` | null/fail/context_mismatch | null/fail/context_mismatch | null/fail/context_mismatch | AGREE |
| `incomplete_instance_no_context` | null/fail/unframed_required | null/fail/context_required | null/fail/context_required | DIVERGE F1 |
| `incomplete_instance_partial_context` | null/fail/unframed_required | Asserted/pass/scoped | Asserted/pass/scoped | DIVERGE F1 |
| `complete_instance_no_context` | null/fail/context_required | null/fail/context_required | null/fail/context_required | AGREE |
| `complete_instance_matching_context` | Asserted/pass/scoped | Asserted/pass/scoped | Asserted/pass/scoped | AGREE |
| `complete_instance_mismatching_context` | null/fail/context_mismatch | null/fail/context_mismatch | null/fail/context_mismatch | AGREE |
| `unframed_unknown_context_key` | error:unknown_frame_dimension | Asserted/pass/not_scoped | Asserted/pass/not_scoped | DIVERGE F2 |
| `framed_unknown_context_key` | error:unknown_frame_dimension | error:unknown_frame_dimension | error:unknown_frame_dimension | AGREE |

The expectations above are **pinned** in `matrix.py`. A guard run fails when a
runtime drifts from its pinned row, when a pinned agreement case starts
diverging, or when a pinned divergence case silently starts agreeing. Known
divergences are asserted, never averaged away.

## Residual divergences (explicit follow-ups)

These are recorded, asserted and left alone — this unit ships **no behaviour
change**. Each has a status of `open` in `matrix.FOLLOW_UPS`.

- **F1 — `unframed_required` is a Python-only status.** Python evaluates
  *type-level* required dimensions before any context check; SQL v32 and the
  TypeScript interpreter have no type-level requirement input and report
  `context_required` (or evaluate normally when the caller supplies context for
  every committed dimension). Follow-up: decide whether SQL/TS gain type-level
  requirements or Python folds `unframed_required` into `context_required`.
- **F2 — unknown context keys on unframed propositions.** Python validates
  context keys unconditionally; SQL/TS short-circuit at “no frame values” and
  ignore an unknown key. Follow-up: align on one caller contract.
- **F3 — SQL D2 framed-union-required open question.** SQL/TS require the whole
  union of declared instance frame dimensions, so supplementary values refuse
  without context and relabel `scoped` with matching context. Python treats
  supplementary values as optional and keeps `not_scoped`. Follow-up: resolve
  D2 (framed-union vs type-required) and migrate the losing runtimes.

## Version binding

`version_pins.json` pins SHA-256 hashes of:

- the Python interpreter and model sources,
- the TypeScript `interpreter`, `models`, `expression-compiler` and `events`
  sources,
- migration `v32` (which creates the gate) and `v33`–`v37` (verified not to
  redefine it),

plus the SHA-256 of the extracted `evaluate_proposition` function body. Change
any of those and the guard refuses to run (`exit 2`) with the pinned and
observed hashes, because the previous proof no longer covers the new
revision. Re-verify, then refresh deliberately with `--update-pins`.

The TypeScript side is compiled from `src/` at run time into a temp directory;
the committed `dist/` is never used, so a stale build cannot mask a source
change.

## What this guard does *not* do

- It does not test live admission or the worker pipeline.
- It does not apply any migration, including v32, to an existing database.
- It does not touch `resolution.*` in the `nexus` database.
- It does not decide the follow-ups above.

## File map

| Path | Role |
|---|---|
| `matrix.py` | shared matrix, fixture, pinned parity contract, follow-ups |
| `canonical.py` | canonical outcome shape and error classification |
| `python_runtime.py` | in-memory Python leg |
| `ts_runtime.py` | compiles `typescript/solscript/src` in a temp dir; runs the driver |
| `sql_runtime.py` | extracts the v32 gate verbatim; throwaway database lifecycle |
| `version_pins.py` / `version_pins.json` | version binding and drift detection |
| `guard.py` / `__main__.py` | orchestration, comparison, report, exit codes |
| `test_framing_parity_guard.py` | contract, pin, Python-runtime and end-to-end tests |
| `typescript/solscript/test/framing-parity-driver.mjs` | TS adapter for the shared matrix |
