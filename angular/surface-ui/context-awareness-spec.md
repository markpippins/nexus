# Context Awareness, Operator Persona & Manual Mode Runtime Specification

## Executive Architectural Summary
The **InteractionContextStore**, **Operator Persona**, and **Manual Mode** constitute the runtime-only contextual intelligence layer of the Nexus Vision UI Virtual Machine.

### Boundary Invariants
1. **Runtime Isolation**: Context awareness, operator narration, and manual mode exist strictly at runtime. They are **never** part of `DesignIR`, `ViewSpec`, capability contracts, or compiler outputs.
2. **Immutability of Compiler Artifacts**: The compiler remains a pure, deterministic function ($\text{DesignIR} \rightarrow \text{ViewSpec}$). The Operator and Manual Mode consume compiler metadata as a "map of the world" but cannot mutate or re-synthesize AST nodes.
3. **Decoupled Application vs. Operator State**:
   - `ContractStateStore`: Single source of truth for **application domain state** per widget/contract.
   - `InteractionContextStore`: Single source of truth for **ephemeral operator context** (what the user is looking at, selecting, focusing, or doing).
4. **Action Interpretation Enforcement**: Any state-changing action triggered by user interaction, workflow navigation, or the Operator persona passes through the `ActionInterpreter` and updates `InteractionContextStore` or `ContractStateStore` accordingly.

---

```
┌────────────────────────────────────────────────────────────────────────┐
│                          ViewSpec Program AST                          │  (Immutable, Pure)
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                       Nexus Virtual Machine (Runtime)                  │
│                                                                        │
│  ┌──────────────────────────────┐    ┌──────────────────────────────┐  │
│  │     ContractStateStore       │    │   InteractionContextStore    │  │
│  │     (Application Data)       │    │     (Operator Context)       │  │
│  └──────────────┬───────────────┘    └──────────────┬───────────────┘  │
│                 │                                   │                  │
│                 │      ┌──────────────────────┐     │                  │
│                 ├─────►│   Operator Persona   │◄────┤                  │
│                 │      │   & Manual Mode      │     │                  │
│                 │      └──────────┬───────────┘     │                  │
│                 │                 │                 │                  │
│  ┌──────────────▼──────────────┐  │  ┌──────────────▼───────────────┐  │
│  │      ActionInterpreter      │◄─┴──┤        Reactive EventBus     │  │
│  └──────────────┬──────────────┘     └──────────────┬───────────────┘  │
│                 │                                   │                  │
│  ┌──────────────▼──────────────┐     ┌──────────────▼───────────────┐  │
│  │   Adapter Execution Engine  │     │      Widget Renderer         │  │
│  │   (Contract Projections)    │     │      & Overlays Layer        │  │
│  └─────────────────────────────┘     └──────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 1. InteractionContextStore — Canonical Data Model & API

### Canonical Shape
```typescript
export interface InteractionContext {
  activeSurfaceId: string;
  activeWorkflowId?: string;
  activeWorkflowStepId?: string;

  activeWidgetId?: string;
  activeRoleId?: string;

  focusedControlId?: string;
  lastClickedWidgetId?: string;
  lastClickedRoleId?: string;

  selectedEntity?: {
    widgetId: string;
    entityId: string;
    rowIndex?: number;
  };

  lastEvent?: {
    widgetId: string;
    eventType: string;
    payload?: unknown;
  };

  helpMode?: {
    active: boolean;
    level: "operator" | "manual" | "video" | "drawer";
    targetWidgetId?: string;
  };
}
```

### Core API Surface (`InteractionContextStoreAPI`)
```typescript
export interface InteractionContextStoreAPI extends InteractionContextEventHooks {
  // Snapshot & Subscription
  get(): InteractionContext;
  subscribe(listener: (ctx: InteractionContext) => void): () => void;
  update(partial: Partial<InteractionContext>): void;
  reset(): void;
}
```

### Contextual Event Hooks Contract (`InteractionContextEventHooks`)
These hooks define how runtime events update operator context:

```typescript
export interface InteractionContextEventHooks {
  // Widget-level events
  onWidgetClick(widgetId: string, roleId?: string): void;
  onWidgetFocus(controlId: string): void;
  onWidgetBlur(controlId: string): void;
  onRowSelect(widgetId: string, entityId: string, rowIndex?: number): void;

  // EventBus → InteractionContextStore
  onEventDispatch(widgetId: string, eventType: string, payload?: unknown): void;

  // Navigation & workflow transitions
  onSurfaceNavigate(surfaceId: string): void;
  onWorkflowStepEnter(workflowId: string, stepId: string, focusRoleId?: string): void;

  // Help / Manual Mode
  onHelpMode(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void;
  onHelpModeExit(): void;
}
```

---

## 2. Operator Persona Integration Model

### Mission
The **Operator** is a persistent runtime presence acting as an intelligent UI co-pilot. It interprets live runtime context, narrates what the operator is viewing, explains action consequences, assists with workflow execution, and provides contextual help.

### Three Primary Inputs
1. **`InteractionContextStore` (The Operator's Eyes)**: Tracks active surface, active workflow step, selected entities, focused controls, and help mode state.
2. **`ContractStateStore` (The Operator's Memory)**: Observes verified data contracts (e.g. MetricSeries values, EntityCollection items, ConsensusMatrix votes).
3. **`ViewSpec` Metadata (The Operator's Map of the World)**: Reads structural role assignments, layout regions, capabilities, interaction graphs, and workflow routing tables.

### Operator Persona Runtime API (`OperatorPersonaAPI`)
```typescript
export interface OperatorPersonaAPI {
  // Context Inspection
  getContext(): InteractionContext;
  getState(widgetId: string): unknown;
  getViewSpec(): MultiSurfaceViewSpec;

  // Narration & Co-Pilot Voice
  narrate(message: string): void;

  // Workflow Control
  startWorkflow(workflowId: string): void;
  nextStep(): void;
  previousStep(): void;
  goToStep(stepId: string): void;

  // Help & Manual Mode
  openHelp(level: "operator" | "manual" | "video" | "drawer", targetWidgetId?: string): void;
  closeHelp(): void;

  // Visual Surface Annotation & Highlights
  highlightWidget(widgetId: string): void;
  highlightRole(roleId: string): void;
  clearHighlights(): void;
}
```

---

## 3. Nexus Manual Mode Architecture

### Overview
**Manual Mode** turns Nexus into an interactive, self-documenting system that teaches the user in place without modifying compiler structures.

### Layers of Assistance
- **Operator Guidance**: Inline, conversational micro-guidance inside the Operator message panel.
- **Help Drawer**: Slide-out structured manual pages showing capability contracts, schema expectations, role descriptions, and sample operations.
- **Video Overlay**: Modal/embedded video tutorials demonstrating specific widget or workflow tasks.
- **Inline Highlights & Tooltips**: Surface-level element callouts drawing attention to targeted widgets or action controls.

### Documentation Registry Mapping
A runtime registry maps semantic identifiers to documentation assets:
```typescript
export interface DocumentationEntry {
  title: string;
  summary: string;
  detailsMarkdown?: string;
  videoUrl?: string;
  keyActions?: Array<{ label: string; description: string }>;
}

export type DocumentationRegistry = Record<
  string, // Matches capabilityId | widgetType | surfaceId | workflowId
  DocumentationEntry
>;
```

---

## 4. Architectural Separation & Invariant Matrix

| Concern | DesignIR Compiler | ContractStateStore | InteractionContextStore | Operator Persona | Manual Mode |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Execution Tier** | Build-time / Static AST | Runtime VM | Runtime VM | Runtime VM | Runtime UI Overlay |
| **Purity** | 100% Pure, Deterministic | State Store | Ephemeral Store | Context Reader / Guide | UI Layer |
| **Data Scope** | Structural Schemas | Application Data | Operator Focus/Events | Guidance & Narration | Docs & Videos |
| **Lifecycle** | Immutable AST Output | Surface Session | Interaction Session | Continuous Presence | On-Demand / Modal |
| **Mutates ViewSpec?** | Synthesizes Initial | **No** | **No** | **No** | **No** |
| **Mutates ContractState?**| **No** | Via ActionInterpreter | **No** | Via ActionInterpreter | **No** |
