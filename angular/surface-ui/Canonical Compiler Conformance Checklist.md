Canonical Compiler Conformance Checklist
1. Compiler Purity & Determinism
The compiler must:

Be a pure function: DesignIR → ViewSpec

Produce byte-for-byte identical output for identical input

Contain no randomness (Math.random, timestamps, UUIDs)

Contain no external state or global mutable counters

Contain no side effects (I/O, network, DOM, timers)

Treat ViewSpec as immutable

Treat DesignIR as immutable

Use deterministic hashing only for identity, not logic

Never depend on runtime values or payload schemas

2. Strict Compiler / Runtime Boundary
The compiler must not:

Fetch data

Generate mock data

Execute adapters

Render widgets

Compute CSS, flexbox, or layout geometry

Emit runtime-specific action payloads

Emit runtime state mutations

Modify ContractStateStore

Interact with EventBus

Interact with DOM or canvas

Produce runtime navigation commands

Produce runtime filter/sort predicates

The compiler must:

Emit abstract actions

Emit abstract event routes

Emit abstract workflow transitions

Emit structural layout metadata only

3. Multi-Phase Compiler Pipeline
The compiler must implement all seven phases:

validateDesignIR

resolveCapabilities

selectWidgets

synthesizeLayout

bindAdapters

synthesizeEventRouting

synthesizeFixtures (structural only, no mock payloads)

Each phase must:

Be pure

Be deterministic

Accept only its inputs

Produce only its outputs

Never leak runtime concerns

4. Widget Selector Rules
Widget selection must:

Filter by capabilityId

Filter by density compatibility

Score by hierarchy priority

Score by salience

Score by region compatibility

Score by context bias

Apply variant overrides deterministically

Break ties deterministically (e.g., lexicographic ID)

Produce stable widget IDs (widget-${role})

Widget selection must not:

Infer payload schemas

Use runtime data

Use random selection

Use visual heuristics

Use layout geometry

5. Layout Synthesis Rules
Layout synthesis must:

Assign region

Assign priority

Assign density

Sort widgets deterministically

Produce structural layout nodes only

Layout synthesis must not:

Emit flexbox values

Emit CSS

Emit pixel geometry

Emit runtime layout hints (flex, order)

Compute responsive behavior

6. Adapter Binding Rules
Adapter binding must:

Emit adapter stubs

Use <unknown> placeholders

Follow capability-driven heuristics

Follow role-driven heuristics

Follow interaction-driven heuristics

Follow density-driven heuristics

Follow context-driven heuristics

Adapter binding must not:

Generate full pipelines

Infer payload schemas

Generate mock data

Bind widgets directly to REST

Emit runtime adapter execution logic

7. Event Routing Rules
Event routing must:

Map verbs → abstract ViewSpecAction identifiers

Produce structural event routes

Never embed runtime action payloads

Never embed runtime state mutations

Never embed navigation commands

Never embed filter/sort predicates

Event routing must not:

Emit { type: "navigateSurface" }

Emit { type: "focusWidget" }

Emit { type: "updateGlobalContext" }

Emit { type: "setSurfaceState" }

These are runtime actions, not compiler constructs.

8. Workflow Lowering Rules
Workflow lowering must:

Validate surface and role references

Emit abstract workflow steps

Emit abstract workflow transitions

Emit abstract workflow actions

Workflow lowering must not:

Emit runtime navigation commands

Emit runtime state mutations

Emit inspector open/close commands

Emit global context updates

Emit surface state updates

9. Multi-Surface Compilation Rules
Multi-surface compilation must:

Produce a MultiSurfaceViewSpec

Compile each surface independently

Merge global context correctly

Preserve surface identity

Emit workflow routing table

Set activeSurfaceId deterministically

Multi-surface compilation must not:

Return only the first surface

Collapse surfaces into one

Merge roles across surfaces

Merge interactions across surfaces

10. Incremental Compiler Rules
Incremental compilation must:

Maintain phase-level caches

Maintain dependency graph

Invalidate only affected nodes

Regenerate only affected nodes

Emit granular patches

Preserve widget IDs

Preserve ContractStateStore identity

Support hot reload without remounting

Incremental compilation must not:

Recompile entire surfaces

Emit full patches for minor changes

Use hash-only diffing

Drop runtime state

Remount widgets unnecessarily

11. ViewSpec Program AST Rules
ViewSpec must:

Be immutable

Be serializable

Be diffable

Be patchable

Contain only structural metadata

Contain no runtime logic

Contain no payload schemas

Contain no mock data

12. Deterministic Identity Rules
All IDs must:

Be stable

Be deterministic

Be derived from role/surface names

Never use randomness

Never use timestamps

Examples:

widget-${sanitizeId(roleName)}

node-${sanitizeId(roleName)}

viewspec-${sanitizeId(surfaceId)}