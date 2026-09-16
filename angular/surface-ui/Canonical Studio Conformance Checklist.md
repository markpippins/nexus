Canonical Studio Conformance Checklist
1. Mission & Boundary
Mission: Author, inspect, and edit DesignIR and ViewSpec—never execute them.

Must:

Treat compiler and runtime as external engines.

Must not:

Execute adapters or runtime actions.

Maintain live state beyond previews.

2. DesignIR Authoring
Must:

Provide structured editors for roles, hierarchy, interactions, surfaces, workflows.

Validate against compiler’s validateDesignIR.

Must not:

Inject runtime-specific fields into DesignIR.

3. ViewSpec Inspection
Must:

Show compiled ViewSpec as an immutable AST.

Support diffing and patch visualization.

Must not:

Allow direct mutation of ViewSpec nodes.

4. Adapter Pipeline Builder
Must:

Take compiler-emitted stubs with <unknown> placeholders.

Let users map fields against sample payloads.

Persist adapter definitions separately from compiler.

Must not:

Auto-generate full pipelines without user confirmation.

Infer schemas beyond user-provided mappings.

5. Incremental Editing
Must:

Trigger incremental compilation on keystrokes.

Display patches and affected nodes.

Must not:

silently recompile entire surfaces without showing impact.

6. Workflow & Multi-Surface Editing
Must:

Provide visual tools for defining workflows and cross-surface navigation.

Validate references to surfaces and roles.

Must not:

hard-code navigation logic outside DesignIR.