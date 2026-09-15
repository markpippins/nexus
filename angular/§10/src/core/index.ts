/**
 * §10 core — framework-free TypeScript surface (F-0 packaging, wave-6).
 *
 * This barrel is the single import entry point for the §10 canonical core
 * (compiler, runtime, adapter, types, widget catalog). It is intentionally
 * framework-free: no React/Angular/TanStack dependencies — pure TS types,
 * deterministic pure functions, and runtime/interpreter classes. Hosts (e.g.
 * the React view-architect) import from here WITHOUT forking the package.
 *
 * Import as:
 *   import { compileDesignIR, ViewSpecRuntime, CANONICAL_WIDGET_CATALOG } from "<core>";
 *
 * NOTE (F-1): the canonical projection/admission surface is `typescript/§10 core`
 * (ts-core); do NOT import compile types from it. This `angular/§10/src/core`
 * is the framework-free source of truth for compiler/runtime behavior.
 */

// ── Types ───────────────────────────────────────────────────────────────
export * from "./types/designIR";
export * from "./types/capabilities";
export * from "./types/viewSpec";

// ── Compiler ────────────────────────────────────────────────────────────
export * from "./compiler/compiler";
export * from "./compiler/incrementalCompiler";
export * from "./compiler/regionResolver";
export * from "./compiler/widgetSelector";
export * from "./compiler/adapterHeuristics";

// ── Runtime ─────────────────────────────────────────────────────────────
export * from "./runtime/runtime";
export * from "./runtime/types";
export * from "./runtime/contractState";
export * from "./runtime/eventBus";
export * from "./runtime/actionInterpreter";
export * from "./runtime/widgetRegistry";
export * from "./runtime/interactionContext";
export * from "./runtime/operatorPersona";
export * from "./runtime/documentationRegistry";
export * from "./runtime/mockData";

// ── Adapter ─────────────────────────────────────────────────────────────
export * from "./adapter/runtime";
export * from "./adapter/types";

// ── Widget catalog ──────────────────────────────────────────────────────
export * from "./widget/catalog";