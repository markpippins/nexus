**SOLScript Context Model**  
The concise takeaway: **SOLScript needs a governed, multi‑layer context system that lets every evaluation, rule, proposition, mutation, and UI interaction occur inside an explicit “semantic frame.”**  
This frame determines *what is true, for whom, in what scope, under what evidence, and for what purpose*.  
Everything else is detail.

Below is the full, architect‑grade definition — structured, governed, and ready for integration with the UI VM, ViewSpec Runtime, Planner, PEB, and the Resolution schema.

---

# 🧠 SOLScript Context Model  
The context model defines **the semantic scope** in which SOLScript evaluates:

- entities  
- attributes  
- relationships  
- expressions  
- rules  
- propositions  
- transitions  
- reasoning patterns  
- evidence  
- UI interactions  

It is the **semantic boundary** that determines meaning.

---

# 1. Context Primitives  
These are the atomic units of context. Every evaluation frame is composed of them.

### 1.1 **Entity Context**  
Defines *which entity* is being evaluated.

- `entity_id`  
- `concept_id`  
- `external_id`  
- `attributes` (EAV)  
- `relationships`  
- `state`  

This is the “subject” of evaluation.

### 1.2 **Temporal Context**  
Defines *when* the evaluation occurs.

- `valid_at`  
- `observed_at`  
- `recorded_at`  
- `staleness_window`  
- `temporal_consistency` pattern  

This governs staleness, ordering, and temporal invariants.

### 1.3 **Provenance Context**  
Defines *where the evidence came from*.

- `source_system`  
- `evidence_kind`  
- `verifier_method`  
- `independence`  
- `policy_version_hash`  

This governs admission, verification, and trust.

### 1.4 **Evaluation Context**  
Defines *why* the evaluation is happening.

- `trigger_reason` (manual, UI, reconciliation, staleness, admission)  
- `evaluation_mode` (deterministic, hybrid, inference)  
- `confidence_threshold`  
- `reasoner_chain`  

This governs rule execution and reasoning escalation.

### 1.5 **Relationship Context**  
Defines *which edges* matter.

- `root_entity_id`  
- `relationship_id`  
- `direction`  
- `path`  
- `cardinality`  

This governs relational invariants and guards.

### 1.6 **UI Context**  
Defines *how the UI is interacting with the semantic world*.

- `surface_id`  
- `widget_id`  
- `interaction_type` (edit, bind, drag, drop, transition)  
- `view_context` (ViewSpec surface)  
- `operator_persona_mode`  

This governs dynamic screen generation and invariant enforcement.

---

# 2. Context Frames  
A **context frame** is a structured bundle of primitives.

Every evaluation in SOLScript happens inside a frame.

### 2.1 **Evaluation Frame**  
Used for rule checks, proposition evaluation, and reasoning.

Contains:

- Entity Context  
- Temporal Context  
- Provenance Context  
- Evaluation Context  

### 2.2 **Mutation Frame**  
Used when the UI or runtime changes an entity.

Contains:

- Entity Context  
- UI Context  
- Relationship Context  
- Provenance Context  

### 2.3 **Transition Frame**  
Used for state machine transitions.

Contains:

- Entity Context  
- Temporal Context  
- Evaluation Context  
- Relationship Context  

### 2.4 **Reasoning Frame**  
Used by the HybridReasoner.

Contains:

- Evaluation Context  
- Provenance Context  
- Deterministic pattern results  
- LLM fallback context  

### 2.5 **Projection Frame**  
Used when emitting JSON‑LD, TypeSpec, IR, CUE, TLA+, ViewSpec.

Contains:

- Entity Context  
- Relationship Context  
- Temporal Context  
- Projection target metadata  

---

# 3. Context Flow  
Context flows through SOLScript in a governed pipeline.

### 3.1 **UI → Mutation Frame**  
User edits something → UI emits a mutation → SOLScript wraps it in a Mutation Frame.

### 3.2 **Mutation Frame → Evaluation Frame**  
SOLScript applies the mutation → evaluates invariants → produces rule results.

### 3.3 **Evaluation Frame → Reasoning Frame**  
If deterministic evaluation is insufficient → escalate to HybridReasoner.

### 3.4 **Evaluation Frame → Proposition Update**  
Proposition disposition is updated inside the same frame.

### 3.5 **Evaluation Frame → Projection Frame**  
If the UI needs a new screen → SOLScript generates ViewSpec from the frame.

---

# 4. Context Rules  
These are the governing laws of context.

### Rule 1 — **Context is explicit**  
No evaluation happens without a frame.

### Rule 2 — **Context is immutable**  
Frames are append‑only; they never mutate in place.

### Rule 3 — **Context is layered**  
Higher layers (UI) cannot override lower layers (provenance).

### Rule 4 — **Context is inherited**  
Child evaluations inherit parent frames unless overridden.

### Rule 5 — **Context is auditable**  
Every frame is recorded as evidence.

---

# 5. Context in the UI VM  
This is where the context model becomes critical.

The UI VM must treat SOLScript as:

- the invariant engine  
- the constraint solver  
- the state machine interpreter  
- the relationship guard  
- the semantic truth source  
- the dynamic screen generator  

Every UI interaction becomes a **Mutation Frame**.  
Every UI validation becomes an **Evaluation Frame**.  
Every dynamic screen becomes a **Projection Frame**.

This is how the UI becomes meaning‑aware.

---

# 6. Context in ViewSpec Runtime  
ViewSpec Runtime must pass:

- surface context  
- widget context  
- interaction context  
- operator persona context  

…into SOLScript frames.

This is how the UI VM becomes semantically governed.

---

# 7. Context in Planner, PEB, Runtime  
Planner uses Evaluation Frames.  
PEB uses Provenance Frames.  
Runtime uses Transition Frames.

This unifies the entire system.

---

# 8. Summary Table  
A quick reference for your architect persona:

| Frame | Purpose | Contains |
|-------|---------|----------|
| **Evaluation Frame** | Rule/proposition evaluation | Entity, Temporal, Provenance, Evaluation |
| **Mutation Frame** | UI/runtime mutations | Entity, UI, Relationship, Provenance |
| **Transition Frame** | State transitions | Entity, Temporal, Evaluation, Relationship |
| **Reasoning Frame** | Hybrid reasoning | Evaluation, Provenance, Deterministic results |
| **Projection Frame** | JSON‑LD/TypeSpec/IR/CUE/TLA+/ViewSpec | Entity, Relationship, Temporal, Projection metadata |

---

# Next Step for the Architect Persona  
The natural follow‑up is:

**UI Invariant Contract**

This is the bridge between ViewSpec Runtime and SOLScript — the thing your architect persona needs next so it stops hammering its rate limit.

Whenever you're ready, we’ll define it.
