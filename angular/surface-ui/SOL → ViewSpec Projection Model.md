SOL → ViewSpec Projection Model  
The concise takeaway: ViewSpec is not a UI language. It is a projection of SOL’s semantic world into a deterministic, renderable surface.  
The projection model defines exactly how concepts, entities, attributes, relationships, rules, propositions, and context frames in SOL/SOLScript become screens, widgets, bindings, affordances, and interaction flows in ViewSpec Runtime.

This is the part your architect persona has been trying to finish tonight — and the part GAIS has been inventing UI for without waiting.
Below is the governed, canonical definition that finally unifies them.

🧠 SOL → ViewSpec Projection Model
The projection model answers one question:

Given a SOLScript semantic world, what UI should exist?

ViewSpec is the deterministic answer.

SOLScript provides meaning.
ViewSpec provides representation.
Runtime provides behavior.
UI VM provides interaction.
Operator Persona provides narration.

The projection model is the bridge.

1. Projection Inputs (from SOL/SOLScript)
The projection model consumes semantic structures, not UI definitions.

1.1 Concepts
Become screens or surface types.

1.2 Attributes
Become fields, widgets, controls, filters, columns.

1.3 Relationships
Become links, edges, nested surfaces, tabs, drill‑downs.

1.4 State Machines
Become affordances, buttons, transition actions, status badges.

1.5 Rules / Invariants
Become validation states, warnings, blocking conditions, explanations.

1.6 Propositions
Become semantic truth indicators, badges, semantic health panels.

1.7 Context Frames
Become dynamic UI context, surface modes, operator overlays.

1.8 Entities
Become records, cards, rows, detail views, editable forms.

Everything in ViewSpec is derived from these.

2. Projection Outputs (ViewSpec AST)
The projection model produces a ViewSpec AST, which contains:

Surface

Widget

Binding

Action

Layout

Context

Interaction

OperatorOverlay

ManualModeOverlay

This AST is deterministic and fully governed.

3. Projection Pipeline
The projection pipeline is a five‑stage transformation.

Stage 1 — Concept → Surface
Every SOL concept becomes a ViewSpec surface.

Example:
Concept: WorkRequest  
→ Surface: WorkRequestSurface

Surface metadata includes:

label

icon

default layout

default context

default actions

default filters

This is the “screen type.”

Stage 2 — Attributes → Widgets
Every attribute becomes a widget.

Attribute: status  
→ Widget: StatusDropdown

Attribute: created_at  
→ Widget: TimestampDisplay

Attribute: description  
→ Widget: TextArea

Widget metadata includes:

type (text, enum, number, date, FK, relationship)

validation (from rules)

binding (from entity)

affordances (from state machine)

operator overlays (from context)

Stage 3 — Relationships → Navigation
Every relationship becomes a navigation affordance.

Relationship: WorkRequest → Capability  
→ Tab: “Capabilities”
→ Drill‑down: CapabilitySurface
→ Edge: RelationshipGraphWidget

Relationship metadata includes:

cardinality

direction

path

guard rules

relationship type

Stage 4 — Rules → UI Invariants
Every rule becomes a UI invariant.

Rule: “Status must not be null”
→ Widget-level validation
→ Blocking condition on Save
→ Warning banner if violated
→ Operator Persona explanation

Rule: “Capability must belong to same subsystem”
→ Relationship guard
→ Drag/drop blocker
→ Inline error message
→ Manual Mode explanation

Stage 5 — Propositions → Semantic Indicators
Every proposition becomes a semantic indicator.

Proposition: “WorkRequest is valid”
→ Badge: “Valid”
→ Color: green
→ Tooltip: disposition + reason
→ Manual Mode: evidence chain
→ Operator Persona: narration

Proposition: “Capability mapping is disputed”
→ Banner: “Disputed”
→ Color: yellow
→ Reconciliation flow
→ Evidence viewer

4. Projection Rules (governed by architect persona)
These rules define how projection must behave.

Rule 1 — Projection is deterministic
Same SOL world → same ViewSpec AST.

Rule 2 — Projection is context-aware
Context frames modify surfaces.

Rule 3 — Projection is invariant-driven
Rules shape UI affordances.

Rule 4 — Projection is relationship-driven
Edges shape navigation.

Rule 5 — Projection is state-machine-driven
Transitions shape actions.

Rule 6 — Projection is semantic
Propositions shape indicators.

Rule 7 — Projection is explainable
Operator Persona + Manual Mode must be able to narrate every projection.

5. Projection Frame
Projection happens inside a Projection Frame:

Field	Description
entity_id	Entity being rendered
concept_id	Concept type
context_frame	UI + evaluation context
projection_target	ViewSpec
relationship_scope	Which edges to include
temporal_scope	Valid_at, staleness
operator_mode	persona mode
manual_mode	explanation mode


This ensures projection is governed.

6. Projection Example (canonical)
Given:

Concept: WorkRequest

Attributes: status, description, created_at

Relationship: WorkRequest → Capability

Rule: status must not be null

Proposition: WorkRequest is valid

Projection yields:

Surface: WorkRequestSurface
Header: WorkRequest ID

Badge: Valid

Widgets: StatusDropdown, DescriptionTextArea, CreatedAtTimestamp

Tabs: Capabilities

Actions: Approve, Complete

Validation: StatusDropdown required

OperatorOverlay: “Status must not be null”

ManualModeOverlay: rule graph + evidence chain

This is deterministic.

7. Projection Integration with UI VM
ViewSpec Runtime must:

render surfaces

attach context

attach operator overlays

attach manual mode overlays

enforce invariants via SOLScript

update surfaces dynamically

regenerate screens when context changes

This is how the UI becomes semantic.

8. Projection Integration with GAIS
GAIS invents UI.
SOLScript governs UI.
ViewSpec renders UI.

GAIS proposes.
SOLScript constrains.
ViewSpec executes.

This is the correct hierarchy.

9. Summary Table
SOL Element	ViewSpec Projection
Concept	Surface
Attribute	Widget
Relationship	Navigation
Rule	Invariant
Proposition	Indicator
State Machine	Actions
Context Frame	Dynamic UI context
Entity	Record / Detail view

