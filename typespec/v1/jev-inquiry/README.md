# jev-inquiry — TypeSafe System One (jev) Bounded Judgment Contract

## Overview

This contract defines the integration of **TypeSafe System One (jev)** — a calibrated-judgment API — into the Nexus SOLScript evaluation chain via the WR `inquiry?` block.

**Model:** `jev-latest` (released 2026-09-15)
**Components:** Python SDK, `system-one-adapter-python`, vLLM serving fork
**Architect Assessment:** `1564cb0a` (TypeSafe System One in Nexus — SOLScript seam confirmed)

## The Seam

The integration seam already exists as a stub in `python/SOLScript/solscript/inference_engine.py`:

```python
class InferenceEngine:
    external_knowledge_base: KnowledgeBase | None  # delegation point

class KnowledgeBase:
    def query(self, key: str, context: Dict[str, Any]) -> Optional[Any]
```

The Context Model already governs the escalation with fields that map one-to-one:
- `evaluation_mode: deterministic | hybrid | inference`
- `confidence_threshold`
- `reasoner_chain`
- Provenance Context: `source_system`, `verifier_method`, `policy_version_hash`

## TypeSafe Primitives → Nexus Consumers

| TypeSafe Primitive | Nexus Consumer Today |
|-------------------|---------------------|
| **Noul** (P(yes) ∈ [0,1]) | Proposition truth gaps in the evaluation chain |
| **Choice** (per-label probs + confidence) | Drift-type vs transcript-type classification; reviewer pre-triage |
| **Score** (rubric → weighted EV) | Readiness thresholds (0.85/0.75/0.70), rubric-graded evaluations |

## Strategic Findings (from Architect Assessment)

1. **The adapter, not the SDK, is the strategic piece** — `system-one-adapter-python` implements the same `system_one` API backed by any LLM endpoint. Integrate today via ollama on helium; swap in jev (hosted) or self-hosted vLLM later with **zero call-site churn**.

2. **The WR `inquiry?` block is the natural contract home** — a bounded TypeSafe evaluation IS an inquiry with exactly the four fields ratified in `1ba40aa0` (`evaluator_ref`, `expected_outcome_type`, `evidence_requirements`, `read_set_scope`). Evaluations inherit witnessed-run/attempt semantics for free.

3. **Doctrine fit is unusually clean** — TypeSafe's "typed output guarantees the interface, not truth" mirrors `a4232e3d` (Vision is cache, not authority). Their caching doctrine matches our M1 cache/staleness work: judgment cache keyed by `(state_hash, question, model_version, policy_version)`.

4. **Helium topology tie-in** — Candidate triage at scale ties up helium with ollama. Bounded typed questions at marginal cost (jev hosted, or self-hosted via vLLM fork on helium) is the cheapest lever to cut that load.

## Guardrails (Contractual, Not Convention)

| Guardrail | Enforcement |
|-----------|-------------|
| **No admission authority** | PEB pre-screen may prioritize; never decides admission |
| **No reviewer settlement** | Choice's "flag uncertain" maps to witnessed-run adjudication queue |
| **No direct Resolution/PEB mutation** | Adapter is read-only; writes go through canonical pathways |
| **Calibration before adoption** | Pilot comparing jev vs ollama on real SOLScript questions required |

## Contract Structure

| File | Purpose |
|------|---------|
| `models.tsp` | Core models: JevPrimitive, JevOutcome, JevInquiry, JevInquiryResult, JevGuardrails, SolScriptJevSeam |
| `operations.tsp` | Route-free (model-only) |
| `main.tsp` | Entry point |

## Usage

```typescript
import "@nexus/jev-inquiry/python";
```

The contract is consumed by:
1. **SOLScript `KnowledgeBase` adapter** — implements `query(target_key, context)` returning `JevOutcome`
2. **Moleculer REST facade** (future) — layers HTTP routes over these models
3. **Witnessed-run pipeline** — inquiries become `JevInquiryResult` with attempt metadata
4. **Judgment cache** — keyed by `JudgmentCacheKey` (state_hash, question, model_version, policy_version, adapter_backend)

## Pilot Path (Jev 2–6 To Do)

1. **Jev 2** — Adapter spike against ollama on helium
2. **Jev 3** — Deterministic SOLScript judgment benchmark corpus
3. **Jev 4** — Provenance and judgment-cache design
4. **Jev 5** — Map outputs into evaluator/Expression boundaries
5. **Jev 6** — Differential pilot and adoption gate

## References

- Architect assessment: `1564cb0a`
- WR inquiry? ratification: `1ba40aa0`
- M1 slice-3 parity ruling: `62be6bb2` (differential-parity method)
- Vision is cache, not authority: `a4232e3d`
- Context Model (§1.4): evaluation_mode, confidence_threshold, reasoner_chain
- Provenance Context: source_system, verifier_method, policy_version_hash