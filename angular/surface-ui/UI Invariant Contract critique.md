Yes. I see several real conflicts, plus some terminology and boundary mismatches. The §10 documents are directionally compatible with SOL/SOLScript, but the UI Invariant Contract currently describes a more capable and more authoritative SOLScript API than the implementation actually provides.

Executive conclusion

The central compatible idea is:
> UI interaction → explicit semantic context → deterministic evaluation → structured result → UI rendering.


That aligns with SOLScript’s frame-scoped proposition evaluation and Resolution’s proposition/rule model.

However, the UI contract currently conflates three separate things:
1. UI runtime context — what surface/widget/control the operator is interacting with.
2. Semantic evaluation context — the governed dimensions required to evaluate a proposition.
3. Admission authority — whether a mutation or transition is actually authorized.

Those must remain distinct. The UI may provide context and request evaluation, but it must not be treated as the authority that changes Resolution propositions, grants admission, or directly applies governed mutations.

────────────────────────────────────────────────────────────────────────────────

1. Major conflict: the UI contract uses a different disposition model

The UI contract specifies:

// text
True, False, Disputed, Stale, WrongContext

and separately uses:

// text
admitted: boolean

Current SOLScript uses:

// python
Asserted
Disputed
Rejected
Pending
Proposed
Stale
Retracted

For context failures it returns a separate status:

// text
context_required
context_mismatch
scoped
not_scoped

This creates several ambiguities:
-  True  /  False  are not SOLScript dispositions.
-  WrongContext  is currently a context status, not a disposition.
-  Stale  exists in the SOLScript enum but is not currently produced by  evaluate_proposition .
-  admitted  implies authority, while SOLScript’s proposition result is only an assessment.
-  Rejected  and  Disputed  are proposition-level outcomes, not necessarily mutation-admission outcomes.

Recommended correction

The UI result should distinguish:

// typescript
type EvaluationDisposition =
  | "asserted"
  | "rejected"
  | "disputed"
  | "pending"
  | "proposed"
  | "stale"
  | "retracted";
 
type EvaluationStatus =
  | "not_scoped"
  | "scoped"
  | "context_required"
  | "context_mismatch"
  | "unknown";
 
type AdmissionResult =
  | "granted"
  | "refused"
  | "routed";

Then:

// typescript
interface InvariantResult {
  disposition: EvaluationDisposition;
  context_status: EvaluationStatus;
  admission?: AdmissionResult;
  authority_receipt_id?: string;
}

The important rule is:
>  Asserted  does not mean  admitted , and  Rejected  does not necessarily mean  PEB refused authority .


Only PEB’s authority receipt should determine admission.

────────────────────────────────────────────────────────────────────────────────

2. Major conflict: “UI applies the mutation” bypasses governance

The UI contract says:
> If  admitted = true , UI applies the mutation.


That is unsafe under the current architecture.

The current §10 runtime implementation has  ActionInterpreter  handlers that directly mutate  ContractStateStore . For example:
-  select 
-  filter 
-  sort 
-  inspect 
-  drilldown 
-  acknowledge 
-  dismiss 
-  compare 

These are ordinary UI-local state changes, but the UI Invariant Contract also describes semantic mutations such as:
- editing entity attributes
- relationship changes
- state transitions
- workflow transitions

Those are materially different from local presentation state.

Required distinction

UI-local mutations

Examples:
- focus a widget
- select a row
- open an inspector
- change a local filter
- navigate a surface
- toggle help mode

These may remain in the UI runtime and  ContractStateStore .

Governed domain mutations

Examples:
- change an entity attribute
- change a relationship
- transition a domain state
- consume a lease
- advance a WorkRequest
- alter a proposition or doctrine-related state

These must follow:

// text
UI action
→ mutation request
→ semantic frame construction
→ SOL/Resolution evaluation
→ PEB admission where authority is required
→ sanctioned transition
→ domain-state mutation
→ evidence/receipt
→ UI state refresh

The UI should not apply a governed mutation merely because SOLScript returned  Asserted . It should apply it only after the authoritative admission result is returned.

A better UI contract phrase would be:
> The UI may optimistically stage a proposed mutation, but it must not commit a governed domain mutation until the authoritative admission path returns a grant.


────────────────────────────────────────────────────────────────────────────────

3. Major conflict: SOLScript is described as the “source of truth”

The UI contract says:
> The UI must treat this as the source of truth.


and:
> SOLScript is the semantic truth source.


That is too broad.

Current SOLScript is an in-memory evaluator for the Resolution model. It:
- loads concepts, entities, rules, and propositions
- evaluates expressions and propositions
- checks transition guards
- tracks frame dimensions
- performs deterministic reasoning

But it is not currently the canonical owner of:
- persisted entity state
- PEB doctrine
- admission authority
- transaction receipts
- workflow lifecycle
- evidence persistence
- sanctioned transitions

The existing SOLScript README explicitly describes it as schema-correlated and capable of operating entirely in memory. That means its result is an evaluation result, not canonical system state.

Correct authority split

┌───────────────────────────────────────┬───────────────────────────────────────────────┐
│ Concern                               │ Authority                                     │
├───────────────────────────────────────┼───────────────────────────────────────────────┤
│ Semantic grammar, propositions, rules │ Resolution                                    │
│ Evaluation execution                  │ SOLScript / Resolution evaluator              │
│ Doctrine and enforcement posture      │ PEB                                           │
│ Authority/admission receipt           │ PEB                                           │
│ Workflow definition and node position │ Wind                                          │
│ WorkRequest/plan lifecycle            │ Conduit/Nebula according to current ownership │
│ Evidence/provenance records           │ semantics/Nebula                              │
│ UI presentation state                 │ UI runtime                                    │
│ UI interaction context                │ InteractionContextStore                       │
└───────────────────────────────────────┴───────────────────────────────────────────────┘

The UI contract should say:
> The UI treats the returned evaluation as the authoritative assessment for that request, but treats persisted domain state and PEB admission receipts as authoritative for commit and authority.


────────────────────────────────────────────────────────────────────────────────

4. Major conflict: every UI interaction is not necessarily a semantic mutation

The contract opens with:
> Everything the UI does is expressed as a semantic mutation.


That is too strong and conflicts with the §10 context-awareness specification, which explicitly separates:
-  ContractStateStore  — application state
-  InteractionContextStore  — ephemeral operator context

Many UI actions are not semantic domain mutations:
- focusing a control
- selecting a row
- opening help
- changing the active surface
- highlighting a widget
- opening Manual Mode
- changing local sort/filter state

These are runtime interaction events, not propositions about the domain.

Recommended classification

// text
Interaction event
  ├─ presentation/context event
  │    └─ InteractionContextStore
  ├─ local contract-state mutation
  │    └─ ContractStateStore / ActionInterpreter
  └─ governed domain mutation
       └─ SOL/PEB admission path

Only the third category must become a Mutation Frame in the governance sense.

The UI can still attach context to all interactions, but attaching context does not make every interaction a semantic mutation.

────────────────────────────────────────────────────────────────────────────────

5. Major conflict: SOLScript is assigned responsibilities it does not currently have

The UI contract lists these as exact SOLScript calls:
-  validate_entity 
-  validate_attribute 
-  validate_relationship 
-  validate_transition 
-  evaluate_proposition 
-  generate_dynamic_screen 

Current SOLScript visibly implements:
-  evaluate_proposition 
-  check_rule 
-  check_transition_guard 
-  transition_entity 
- expression compilation
- query operations
- reasoning services

It does not currently expose the specified UI-facing functions as a stable API, and it does not currently generate ViewSpec screens.

The §10 compiler documents say:
> DesignIR → ViewSpec


and explicitly prohibit the compiler from generating runtime screens or depending on runtime values.

The UI contract says:
>  generate_dynamic_screen  generates ViewSpec from SOLScript entities.


That conflicts directly with the compiler boundary.

Recommended correction

Dynamic screen generation should be divided:

// text
DesignIR
→ Compiler
→ immutable ViewSpec
→ UI Runtime rendering

SOLScript may provide semantic metadata, validation, or a projection request, but it should not directly synthesize ViewSpec from live entities unless that is explicitly designed as a separate governed projection service.

The current contract should replace  generate_dynamic_screen  with something like:

// text
request_semantic_projection

whose output is data or semantic metadata consumed by the compiler/runtime—not an ungoverned ViewSpec rewrite.

────────────────────────────────────────────────────────────────────────────────

6. Major conflict: “SOLScript applies the mutation”

The Context Model says:
> Mutation Frame → Evaluation Frame
> SOLScript applies the mutation → evaluates invariants


That is inconsistent with the §10 runtime boundary and with safe governance.

A mutation frame should describe a proposed mutation, not mutate the entity before evaluation. Otherwise:
- rejected mutations may transiently alter state
- evaluation can accidentally inspect post-mutation state
- rollback semantics become implicit
- the original state is not clearly bound into the evaluation fingerprint

Better model

// text
current state snapshot
+ proposed mutation
+ semantic context
→ evaluation frame
→ evaluation result
→ admission decision
→ commit or refusal

SOLScript can evaluate a proposed post-state in a sandbox or transaction context, but it should not commit the mutation as part of evaluation.

The existing  TransactionContext  provides a useful foundation, but its current behavior is in-memory rollback/commit and does not itself establish PEB authority or durable evidence.

────────────────────────────────────────────────────────────────────────────────

7. Major conflict: proposition updates during evaluation

The Context Model says:
> Proposition disposition is updated inside the same frame.


This is a serious authority and persistence issue.

Current SOLScript’s  evaluate_proposition  returns a result but does not itself update the proposition disposition.  on_change  may update proposition state and timestamps, but that is a separate event-driven path.

The UI contract also includes:

// text
proposition_updates

in the evaluation result.

That risks making an evaluation call mutate Resolution state, possibly from the UI path.

Recommended rule

Evaluation should return proposed or observed proposition results:

// text
assertion_results
disposition
diagnostics

Persistence of a changed proposition disposition should be a separate, governed operation with:
- source evaluation identity
- input snapshot
- evidence references
- actor
- contract version
- authority/admission outcome where applicable

The UI should never be able to send arbitrary  proposition_updates  as part of a mutation request.

────────────────────────────────────────────────────────────────────────────────

8. Major conflict: context inheritance and override semantics are underspecified

The Context Model says:
> Child evaluations inherit parent frames unless overridden.


But current SOLScript frame discipline is much stricter:
- framed propositions require context
- unknown context keys raise an error
- every declared framed dimension must be covered
- values must match exactly
- mismatches return  context_mismatch 

There is no general frame inheritance implementation, and “override” is not defined.

More importantly, unrestricted UI override would conflict with:
> Higher layers (UI) cannot override lower layers (provenance).


Required clarification

Context should be split into:

// text
authoritative frame dimensions
  - resolved by Resolution/PEB/Wind/provenance
  - UI may supply a request, never override silently
 
descriptive UI context
  - surface/widget/control/operator mode
  - useful for explanation and presentation
  - not itself authority-bearing
 
derived evaluation context
  - generated by the evaluator from authoritative inputs

A UI-provided  context_frame  should therefore be treated as a candidate/request context and validated against the authoritative frame, not merged blindly.

────────────────────────────────────────────────────────────────────────────────

9. Major conflict:  context_frame  is underspecified and over-permissive

The UI MutationRequest has:

// text
context_frame: optional UI context

But the Context Model says:
> No evaluation happens without a frame.


Those statements conflict:
- Is the frame optional?
- Is only UI context optional?
- Can SOLScript construct the missing semantic frame?
- What happens if UI supplies only  surface_id  and  widget_id  but the proposition requires  channel ,  lease , or temporal dimensions?

Current SOLScript refuses a framed proposition without required context, which is correct.

Recommended contract

Make the request explicit:

// typescript
semantic_context: {
  supplied: Record<string, unknown>;
  source: "ui" | "workflow" | "runtime" | "resolved";
}

Then require the evaluator to return:

// typescript
context_status:
  | "scoped"
  | "context_required"
  | "context_mismatch"
  | "unknown";

Do not let the UI pretend that its interaction context is the complete semantic frame.

────────────────────────────────────────────────────────────────────────────────

10. Major conflict:  compiled_sql  exposes the wrong evaluation abstraction

The UI contract includes:

// text
compiled_sql

as an output.

Current SOLScript compiles expression trees into Python callables. Resolution also has SQL compilation functions, but SQL is an implementation/projection mechanism, not necessarily the portable semantic explanation.

Returning compiled SQL to the UI creates several problems:
- leaks database implementation details
- does not represent Python/JVM/other evaluator behavior
- may expose sensitive schema or query details
- encourages treating SQL as the semantic rule itself
- does not work for all expressions or reasoning paths

Recommended replacement

Expose a stable, evaluator-neutral explanation:

// typescript
evaluation_trace?: {
  rule_id: string;
  proposition_id?: string;
  result: "passed" | "failed" | "unknown";
  reason_code: string;
  operands?: unknown[];
}

Optionally include a separately authorized diagnostic representation:

// text
compiled_artifact:
  language: "sql" | "python" | "sol-ir"
  digest: string
  locator: string

The UI should not need raw compiled SQL to explain a decision.

────────────────────────────────────────────────────────────────────────────────

11. Major conflict:  required_evidence  and  required_verification  imply SOLScript can authorize evidence requirements

The UI contract includes:

// text
required_evidence
required_verification

These are reasonable output concepts, but current SOLScript mostly evaluates available facts and returns rule outcomes. It does not currently define a formal evidence-request or verification-plan contract.

There is also a boundary issue:
- SOLScript can identify missing inputs or failed evidence predicates.
- PEB/semantics must define whether evidence is authoritative, independently verified, fresh, or admissible.
- The UI must not decide that satisfying a suggested evidence item grants authority.

Recommended distinction

// text
missing_inputs:
  facts required for evaluation
 
evidence_requirements:
  references/predicates that must be satisfied
 
verification_requirements:
  verification operations or attestations needed
 
authority_result:
  only PEB can grant/refuse/reroute

These should not be collapsed into a generic “required evidence” field.

────────────────────────────────────────────────────────────────────────────────

12. Major conflict: LLM suggestions versus semantic outputs

The UI contract allows:

// text
suggested_next_steps

and the Context Model gives the Operator Persona a role in interpreting consequences.

That is compatible only if suggestions remain non-authoritative. The contract does say LLM suggestions are optional, which is good.

But the phrase:
> everything the UI displays is a semantic evaluation result


is too broad. Operator narration and suggestions are derived UI guidance, not necessarily semantic truth.

Correct hierarchy

// text
authoritative evaluation
→ deterministic diagnostics
→ evidence/provenance explanation
→ optional operator narration
→ optional LLM suggestion

The UI must label those layers separately. A suggestion must never be rendered in the same semantic category as an assertion result or PEB authority receipt.

────────────────────────────────────────────────────────────────────────────────

13. Major conflict: timestamps and determinism

The §10 code contains runtime timestamps and even time-dependent mock generation:
-  Date.now()  in  ActionInterpreter 
-  Date.now()  in generated mock timestamps
-  datetime.now()  in SOLScript transaction/change paths
-  datetime.now()  when proposition state changes

The governance envelope rules require replay to use captured timestamps and prohibit substituting wall-clock time during replay.

This is not necessarily a conflict for ephemeral UI state, but it becomes a conflict if those runtime events are included in a governed evaluation frame or fingerprint.

Required boundary

- UI-local timestamps may exist for display and ephemeral state.
- Governed frames must use captured, explicit timestamps.
- SOLScript evaluation APIs used by governance must accept an evaluation clock/input snapshot.
- Replay must never call  datetime.now()  to reconstruct the original decision.

The UI contract should explicitly state whether  context_frame  is fingerprinted. If it is, it must use canonicalized captured values, not live UI event times.

────────────────────────────────────────────────────────────────────────────────

14. Major conflict: the UI runtime’s  ActionInterpreter  currently bypasses semantic validation

The UI contract says:
> Rule 1 — UI never validates anything. All validation is delegated to SOLScript.


But the actual  DefaultActionInterpreter  directly applies state changes for many action types without any SOLScript call.

For local UI actions, that is acceptable. For domain mutations, it is not.

The missing distinction is an action classification or capability metadata:

// typescript
type ActionGovernance =
  | "local"
  | "semantic-evaluate"
  | "authority-admit";

Then:

// text
local
  → ActionInterpreter → UI stores
 
semantic-evaluate
  → MutationRequest → SOLScript → result
 
authority-admit
  → MutationRequest → SOLScript → PEB admission → commit

Without this classification, the invariant contract is aspirational and the runtime can silently bypass it.

────────────────────────────────────────────────────────────────────────────────

15. Conflict in dynamic screen generation

The UI contract treats SOLScript as:
> the dynamic screen generator


But the compiler checklist says:
- compiler generates ViewSpec
- runtime interprets ViewSpec
- compiler must not depend on runtime values
- ViewSpec contains structural metadata
- runtime must not alter ViewSpec structure

The Context Model also says Projection Frames can emit:

// text
JSON-LD/TypeSpec/IR/CUE/TLA+/ViewSpec

That is too broad unless “emit” means produce a separate projection artifact rather than mutate the active UI program.

Recommended split

// text
SOL/Resolution:
  evaluate semantic facts and determine available capabilities/meaning
 
Compiler:
  DesignIR → ViewSpec
 
Runtime:
  ViewSpec + evaluated data → rendered screen
 
Projection service:
  explicit semantic model → JSON-LD/TypeSpec/etc.

SOLScript may recommend or expose semantic capability metadata, but screen structure should remain governed by the compiler/ViewSpec pipeline.

────────────────────────────────────────────────────────────────────────────────

16. Partial conflict:  InteractionContextStore  is mutable, while semantic frames are immutable

The Context Model correctly says semantic frames are immutable and append-only. But the runtime store is explicitly mutable:

// typescript
update(partial)
reset()

This is not inherently wrong because the store contains ephemeral operator context. The conflict arises when the UI contract passes the live mutable store state directly as an evaluation frame.

Required rule

At evaluation time:

// text
mutable InteractionContextStore
→ immutable captured UI context snapshot
→ validated semantic frame
→ SOLScript evaluation

The evaluator must receive a snapshot, not a live mutable object. The resulting frame must carry an identity or digest if it participates in governance.

────────────────────────────────────────────────────────────────────────────────

17. What is compatible

Several important parts are sound and should be retained.

Compatible principle 1: explicit context

The UI contract’s requirement that every governed interaction carries context aligns with SOLScript’s framed proposition behavior.

Compatible principle 2: deterministic-first evaluation

This aligns with the current HybridReasoner and deterministic pattern library.

Compatible principle 3: provenance and evidence visibility

This aligns with the broader governance-envelope model, provided evidence references remain owned by semantics and authority remains owned by PEB.

Compatible principle 4: hard versus soft rules

SOLScript already has  Severity.HARD  and  Severity.SOFT . The UI can render these differently, but severity should not itself be interpreted as an admission decision.

Compatible principle 5: Manual Mode inspection

Showing rule graphs, evidence chains, proposition history, and context frames is compatible, as long as Manual Mode is read-only and does not mutate governance state.

Compatible principle 6: runtime/compiler separation

The context-awareness document’s prohibition on mutating DesignIR/ViewSpec is consistent with the intended architecture. The UI Invariant Contract should be revised to preserve that same discipline.

────────────────────────────────────────────────────────────────────────────────

18. Most important overlooked seam

The biggest missing seam is:
> UI interaction context → governed semantic frame → admission envelope


The current documents discuss all three but do not define the conversion contract.

Specifically missing are:
1. Which UI fields become semantic frame dimensions?
2. Which remain descriptive UI metadata?
3. Who resolves workflow identity and node identity?
4. How does a widget action identify the governed entity?
5. How is the proposed post-state represented?
6. Which actions require only local state mutation?
7. Which actions require SOL evaluation?
8. Which actions require PEB authority?
9. How are evaluation and admission fingerprints generated?
10. How does the UI receive and verify the resulting receipt?

This is the seam that should be specified before wiring the UI to SOLScript.

────────────────────────────────────────────────────────────────────────────────

Recommended corrected flow

// text
1. UI event occurs
2. ActionInterpreter classifies action:
   local | semantic-evaluate | authority-admit
3. InteractionContextStore is snapshotted
4. UI builds a proposed mutation
5. Runtime resolves:
   subject identity
   workflow/node
   contract/projection identity
   input snapshot
6. SOL/Resolution evaluates the proposed state
7. SOL returns an assessment:
   disposition
   context status
   assertion results
   diagnostics
   evidence requirements
8. If authority is required:
   PEB evaluates/adopts the assessment
9. PEB returns:
   granted | refused | routed
   receipt and sanctioned transition
10. Only then is governed state committed
11. Evidence, envelope, and receipt are persisted
12. UI renders the assessment and authority layers separately

Bottom line

The UI Invariant Contract is conceptually aligned with SOL/SOLScript, but it currently overstates SOLScript’s role and collapses assessment, mutation, UI state, and authority into one contract.

The highest-priority corrections are:
1. Replace the UI disposition vocabulary with SOLScript-compatible assessment statuses plus a separate authority result.
2. Remove “SOLScript applies the mutation”; make mutation evaluation proposal-based.
3. Make PEB admission, not  admitted: true , the authority boundary.
4. Separate UI interaction context from semantic evaluation context.
5. Remove direct SOLScript ownership of ViewSpec generation.
6. Remove proposition mutation from evaluation results.
7. Add action governance classification to the UI runtime.
8. Define the missing UI-context-to-governance-envelope adapter contract.

No source files were changed.
