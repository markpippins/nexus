🧠 UI Invariant Contract
The UI Invariant Contract defines how the UI VM interacts with SOLScript to enforce invariants, evaluate meaning, and generate dynamic screens.

It is the bridge between:

ViewSpec Runtime

SOLScript Interpreter

Resolution Schema

Reasoning Layer

Operator Persona

Manual Mode

Everything the UI does is expressed as a semantic mutation, and everything the UI displays is a semantic evaluation result.

1. Contract Overview
The UI Invariant Contract has three responsibilities:

1. Mutation → Frame Construction
UI emits a mutation → SOLScript wraps it in a Mutation Frame.

2. Invariant Evaluation
SOLScript evaluates invariants inside an Evaluation Frame.

3. UI Rendering of Semantic Results
UI receives structured results and updates:

widget state

surface state

affordances

error/warning banners

operator persona narration

dynamic screen generation

2. Contract Inputs (UI → SOLScript)
The UI sends semantic mutations, not raw values.

Every mutation has the same shape:

MutationRequest
Field	Description
entity_id	Target entity
concept_name	Concept type
attribute_name	Attribute being changed
new_value	Proposed value
interaction_type	edit, bind, drag, drop, transition
surface_id	ViewSpec surface
widget_id	Widget initiating mutation
operator_mode	persona mode
context_frame	optional UI context


This is wrapped into a Mutation Frame.

3. Contract Outputs (SOLScript → UI)
SOLScript returns a structured result that the UI must render.

InvariantResult
Field	Description
admitted	Boolean: mutation allowed?
disposition	semantic disposition (True, False, Disputed, Stale, WrongContext)
rule_results	list of rule evaluations
compiled_sql	compiled invariant expression
reason	human-readable explanation
required_evidence	evidence needed to admit mutation
required_verification	verification needed
suggested_next_steps	deterministic or LLM suggestions
proposition_updates	updated propositions
context_frame	evaluation frame used


The UI must treat this as the source of truth.

4. Contract Behaviors (UI Responsibilities)
The UI must respond to invariant results in governed ways.

4.1 Admitted = true
UI applies the mutation and updates:

widget state

surface state

entity representation

operator persona narration

4.2 Admitted = false
UI must:

reject the mutation

highlight the violating widget

show the rule name + reason

offer suggested next steps

optionally trigger Manual Mode

4.3 Disposition = Disputed
UI must:

show a “semantic dispute” banner

offer reconciliation flow

show disagreement details

allow user to inspect evidence

4.4 Disposition = Stale
UI must:

show staleness indicators

offer refresh or re-evaluation

show staleness window

4.5 Disposition = WrongContext
UI must:

show context mismatch

offer context switch

highlight conflicting frames

5. Contract Functions
These are the exact calls the UI VM makes into SOLScript.

5.1 validate_entity
Validates all invariants for an entity.

5.2 validate_attribute
Validates a single attribute mutation.

5.3 validate_relationship
Validates drag/drop wiring in the workbench.

5.4 validate_transition
Validates state machine transitions.

5.5 evaluate_proposition
Evaluates semantic truth.

5.6 generate_dynamic_screen
Generates ViewSpec from SOLScript entities.

6. Contract Rules
These are the governing laws of the UI invariant system.

Rule 1 — UI never validates anything
All validation is delegated to SOLScript.

Rule 2 — UI must render semantic truth
UI state reflects SOLScript disposition.

Rule 3 — UI must respect rule severity
Hard rules block.
Soft rules warn.

Rule 4 — UI must surface provenance
Evidence and verification must be visible.

Rule 5 — UI must propagate context
Every interaction carries a context frame.

Rule 6 — UI must be deterministic-first
LLM suggestions are optional, never authoritative.

7. Contract Integration with ViewSpec Runtime
ViewSpec Runtime must:

attach surface context

attach widget context

attach operator persona context

attach interaction context

pass all of it into SOLScript frames

This makes the UI meaning-aware.

8. Contract Integration with Operator Persona
Operator Persona uses invariant results to narrate:

why a mutation was rejected

what rule was violated

what evidence is missing

what next steps are recommended

This is how the UI becomes explainable.

9. Contract Integration with Manual Mode
Manual Mode uses invariant results to:

show rule graphs

show compiled expressions

show evidence chains

show proposition history

show context frames

This is how the UI becomes inspectable.

10. Summary Table
Layer	Role	Contract
UI	emits mutations	MutationRequest
SOLScript	evaluates invariants	InvariantResult
ViewSpec Runtime	carries context	ContextFrame
Operator Persona	narrates meaning	reason + next steps
Manual Mode	explains meaning	rule graphs + evidence
