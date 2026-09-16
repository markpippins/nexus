Canonical Runtime Conformance Checklist
1. Mission & Boundary
Mission: Execute ViewSpec programs—never decide what the UI is, only how it behaves.

Boundary:

Must not modify DesignIR or ViewSpec.

Must not select widgets or synthesize layout.

Must not generate adapter pipelines.

2. ContractStateStore
Single source of truth for UI state per widget/contract.

Must:

Store contract-shaped state only.

Expose reactive subscriptions.

Apply all mutations via actions.

Must not:

Allow direct widget prop mutation.

Store arbitrary ad hoc state outside contracts.

3. EventBus & ActionInterpreter
EventBus:

Routes events from widgets to ActionInterpreter.

ActionInterpreter:

Executes ViewSpecAction → ContractStateStore mutations.

Must:

Support actions like navigateSurface, focusWidget, updateGlobalContext, setSurfaceState.

Be the only path for state changes.

Must not:

Let widgets bypass ActionInterpreter.

Embed business logic in widgets.

4. Adapter Execution
Must:

Execute adapter pipelines against live payloads.

Validate outputs against capability contracts.

Handle REST/SSE/WebSocket/agent outputs.

Must not:

Infer schemas beyond declared pipelines.

Modify adapter definitions at runtime.

5. Layout & Rendering
Must:

Interpret structural layout (region, priority, density).

Render widgets into regions.

Handle responsive behavior and geometry.

Must not:

Feed layout decisions back into compiler.

Change ViewSpec layout structure.

6. Hot Reload & Patching
Must:

Apply patches (LAYOUT, WIDGETS, ADAPTERS, EVENTS, FIXTURES) without remounting unnecessarily.

Preserve ContractStateStore across patches.

Must not:

Tear down entire surfaces on minor changes.

Regenerate IDs during hot reload.