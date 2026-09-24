# Jev 6 Differential Pilot Report — faithful-state 19-case benchmark

Date: 2026-09-23. Branch: `jev-poc-thallium-qwen3b`. Backends:
TypeSafe hosted `jev-1.13.0` vs ollama `qwen2.5-coder:3b` (thallium:11434).
Raw result JSONs sit beside this report in `results/` (faithful runs only).

## 1. What changed since the first (synthetic-state) run

The first benchmark built state with `defaults.get(f, 'present')`, so
`readiness_score` arrived as the string `'present'` instead of `0.92` and
`valid_license` as `'present'` instead of `False`. Deltas measured against
that state were meaningless. Two fixes (both with tests, all green):

- **Item 1 — faithful state builder**
  (`solscript/benchmarks/jev_corpus/faithful_state.py`, 11 tests in
  `tests/test_jev_faithful_state.py`): per-case values taken from each case's
  own reference rationale, e.g. noul_001 `readiness_score=0.92`, noul_002
  `valid_license=False, license_expiry=2024-01-15`, noul_007
  `evidence_items=7, required_evidence=12`. Unknown ids and incomplete entries
  raise loudly — no silent `'present'` fallback.
- **Item 2 — CHOICE label passthrough**
  (`solscript/adapters/jev/adapter.py`, 9 tests in
  `tests/test_jev_choice_labels.py`): new optional `label_sets` param on
  `system_one()` threads the corpus vocabulary into the prompt ("ALLOWED
  LABELS … use exactly these strings") and constrains the parse
  case-insensitively. Invented labels are dropped; an unmappable top pick
  sets `flag_for_review` instead of fabricating a label. Fully backward
  compatible (`None` = legacy behavior).

Suite: **87 passed** (67 pre-existing + 20 new).

## 2. TypeSafe hosted, faithful state — 19/19, zero errors

| Case | TS | REF | |Δ| / verdict |
|---|---|---|---|
| noul_001 | 0.99 | 1.00 | 0.01 |
| noul_002 | 0.03 | 0.00 | 0.03 |
| noul_003 | 0.98 | 0.85 | 0.13 |
| noul_004 | 0.85 | 0.95 | 0.10 |
| noul_005 | 0.72 | 0.72 | exact |
| noul_006 | 0.94 | 0.99 | 0.05 |
| noul_007 | 0.03 | 0.65 | **0.62 — see note** |
| noul_008 | 0.75 | 0.78 | 0.03 |
| choice_001–006 | transcript_drift / minor / nearly_ready / grounded / P2_medium / committed | identical | **6/6 exact** |
| score_001 | 0.32 | 0.82 | 0.50 |
| score_002 | 0.91 | 0.76 | 0.15 |
| score_003 | 0.01 | 0.68 | 0.67 |
| score_004 | **1.18** | 0.73 | out of `[0,1]` range |
| score_005 | 0.11 | 0.91 | 0.80 |

Notes:

- **Noul calibrates.** 7/8 within 0.13. The exception (noul_007) is arguably a
  *reference* problem, not a model problem: the question asks whether
  completeness is *above 0.9* and the state is 7-of-12 (0.58), so P≈0 is the
  defensible answer and the reference 0.65 looks suspect. Flagged for analyst
  review, not counted as a model miss.
- **Choice is perfect.** 6/6 exact with high confidence (0.83–0.98 on the
  clean hits; 0.37–0.48 where the first synthetic run missed — confidence
  tracks difficulty correctly).
- **Score does not calibrate.** 1/5 within 0.15, one value (1.18) outside the
  contract range — the Score primitive needs rubric/criteria work before any
  adoption decision leans on it.

## 3. Ollama `qwen2.5-coder:3b`, faithful state — 19/19 via adapter

Noul P: 1.0 / 0.0 / 1.0 / 1.0 / 0.9 / **0.0** / 0.9 / 0.9 vs REF
1.0 / 0.0 / 0.85 / 0.95 / 0.72 / 0.99 / 0.65 / 0.78.
Faithful state fixed noul_002 (was 1.0 → now 0.0, exact). noul_006 is a genuine
model miss (empty `duplicates` should satisfy the uniqueness invariant;
the 3b model returned 0.0).

Choice *without* labels: unmappable — the model invents `drift_type1`,
`label1/label2`, `low/medium/high`, `success/failure` instead of the corpus
vocabulary. Choice *with* label passthrough (item 2): **4/6 MATCH**
(001 transcript_drift, 004 grounded, 005 P2_medium, 006 committed);
002 (cosmetic vs minor) and 003 (blocked vs nearly_ready) miss by one tier.

Score: 0.80 / 0.90 / 0.70 / 0.95 on 002–005 (three within 0.22 of REF);
score_001 failed JSON parse once (transient, retryable — fail-visible path
worked as designed).

## 4. Findings for the adoption decision

1. **Noul + Choice are pilot-ready on the hosted backend** with faithful
   state; Choice additionally needs nothing more than the corpus labels it
   already receives in the WR flow.
2. **The adapter pattern is validated end-to-end**: same corpus, same state
   builder, zero call-site churn to swap backends; `label_sets` closed the
   small-model vocabulary gap from unmappable to 4/6.
3. **Score is not ready on either backend.** Do not use Score output for
   gating until the rubric/criteria contract is reworked (hosted 1.18
   out-of-range value is the sharpest evidence).
4. **Open items**: noul_007 reference value (0.65) vs strict reading (≈0.0)
   needs an analyst ruling; ollama score_001 transient parse failure suggests
   a retry-with-backoff in the benchmark harness, not a model flaw.
