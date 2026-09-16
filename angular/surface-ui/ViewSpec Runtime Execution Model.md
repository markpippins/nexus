ViewSpec Runtime Execution Model  
The concise takeaway: The ViewSpec Runtime is the UI VM of Nexus — a deterministic execution engine that takes a ViewSpec AST (projected from SOL/SOLScript) and turns it into a live, context‑aware, invariant‑governed interactive surface.  
It is not a renderer. It is a semantic runtime whose job is to execute meaning.

Below is the full, architect‑grade definition — structured, governed, and aligned with the SOLScript Context Model and the UI Invariant Contract.

🧠 What the ViewSpec Runtime is
A ViewSpec Runtime is a semantic UI virtual machine.
It executes:

surfaces

widgets

bindings

actions

layout graphs

context frames

invariant checks

operator overlays

manual mode overlays

It is the runtime counterpart to:

SOLScript (semantic VM)

SOL → ViewSpec Projection Model (projection engine)

ViewSpec Runtime is the behavior layer.

1. Runtime Responsibilities
The runtime has five core responsibilities.

1. Surface Execution
Load a ViewSpec surface and instantiate:

layout regions

widget instances

bindings

actions

overlays

2. Context Propagation
Attach and propagate:

UI context

operator persona context

evaluation context

mutation context

projection context

Every interaction carries a ContextFrame.

3. Invariant Enforcement
Delegate all validation to SOLScript:

attribute edits

relationship wiring

state transitions

drag/drop operations

binding changes

Runtime never validates anything itself.

4. Event Routing
Route widget events to:

SOLScript mutation functions

ViewSpec actions

operator overlays

manual mode overlays

5. Dynamic Regeneration
Regenerate surfaces when:

context changes

propositions update

invariants fail

relationships change

state transitions occur

This is how the UI becomes semantic.

2. Runtime Architecture
The runtime is composed of six subsystems.

2.1 Surface Manager
Responsible for:

loading surfaces

instantiating widgets

managing layout

attaching context

rendering initial state

Surfaces are the top‑level execution units.

2.2 Widget Engine
Responsible for:

widget lifecycle

widget props

widget state

widget events

widget bindings

Widgets are semantic projections of SOL attributes.

2.3 Binding Engine
Responsible for:

resolving bindings

fetching entity values

applying mutations

updating widgets

maintaining consistency

Bindings are semantic links to SOLScript entities.

2.4 Action Engine
Responsible for:

executing ViewSpec actions

triggering state transitions

invoking SOLScript invariants

updating surfaces

narrating via operator persona

Actions are semantic transitions.

2.5 Event Bus
Responsible for:

routing widget events

dispatching mutation frames

triggering evaluation frames

notifying overlays

updating runtime state

The event bus is the runtime’s nervous system.

2.6 Overlay Engine
Responsible for:

operator persona overlays

manual mode overlays

invariant overlays

semantic indicators

contextual hints

Overlays are semantic explanations.

3. Runtime Execution Pipeline
The runtime executes ViewSpec in a deterministic pipeline.

Step 1 — Load Surface
SurfaceManager loads:

layout graph

widget definitions

bindings

actions

overlays

ContextFrame is attached.

Step 2 — Instantiate Widgets
WidgetEngine creates:

widget instances

initial props

initial state

event handlers

Bindings are resolved.

Step 3 — Bind Data
BindingEngine fetches:

entity values

relationship values

proposition indicators

semantic truth badges

Widgets are hydrated.

Step 4 — Attach Overlays
OverlayEngine attaches:

operator persona overlays

manual mode overlays

invariant overlays

semantic indicators

UI becomes meaning‑aware.

Step 5 — Handle Interaction
User interacts → Widget emits event → EventBus routes to:

SOLScript mutation

SOLScript evaluation

ViewSpec action

overlay update

surface regeneration

This is the core loop.

Step 6 — Regenerate Surface
If context changes:

surface is regenerated

widgets rebind

overlays update

propositions re-evaluate

invariants re-check

This is how the UI stays semantically fresh.

4. Runtime Data Structures
The runtime uses four core structures.

4.1 RuntimeSurface
Contains:

surface metadata

layout graph

widget instances

bindings

actions

overlays

context frame

4.2 RuntimeWidget
Contains:

widget type

props

state

bindings

event handlers

overlays

4.3 RuntimeBinding
Contains:

entity reference

attribute reference

relationship reference

binding expression

context frame

4.4 RuntimeAction
Contains:

action type

state transition

mutation target

invariant checks

operator narration

5. Runtime Context Model
The runtime uses the same context model as SOLScript:

Entity Context

Temporal Context

Provenance Context

Evaluation Context

Relationship Context

UI Context

Every interaction carries a ContextFrame.

6. Runtime Invariant Enforcement
Runtime delegates all invariants to SOLScript:

validate_entity

validate_attribute

validate_relationship

validate_transition

evaluate_proposition

Runtime never validates anything itself.

7. Runtime → SOLScript Contract
Runtime sends:

MutationRequest

TransitionRequest

BindingRequest

RelationshipRequest

SOLScript returns:

InvariantResult

Proposition updates

Semantic indicators

Suggested next steps

Runtime updates UI accordingly.

8. Runtime → Operator Persona Contract
Runtime sends:

rule failures

invariant results

proposition updates

context frames

Operator Persona returns:

explanations

hints

next steps

semantic narration

9. Runtime → Manual Mode Contract
Runtime sends:

rule graphs

evidence chains

compiled expressions

context frames

Manual Mode returns:

inspectable overlays

semantic explanations

10. Summary Table
Component	Role	Description
Surface Manager	load	surfaces + layout
Widget Engine	instantiate	widgets + props + state
Binding Engine	hydrate	entity → widget
Action Engine	execute	transitions + invariants
Event Bus	route	events → SOLScript
Overlay Engine	explain	persona + manual mode
