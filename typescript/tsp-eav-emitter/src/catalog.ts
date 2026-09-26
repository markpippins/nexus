/**
 * In-memory catalog: the record graph the walker accumulates and the SQL
 * renderer serializes. IDs stay symbolic (names + declaration order) — the
 * database assigns real ids at apply time.
 */
import type { DiagnosticTarget } from "@typespec/compiler";

export interface StereotypeRow {
  name: string;
  description?: string;
}

export interface FieldRow {
  property_name: string;
  label?: string;
  name: string;
  field_type_code: number;
  is_calculated: boolean;
  field_index: number;
}

export interface RevisionRow {
  stereotypeName: string;
  parentStereotypeName?: string;
  rationale?: string;
  /** Ordered contract: declaration order of the model's own properties. */
  fields: { property_name: string; required: boolean }[];
}

export interface WalkerDiagnostic {
  code: string;
  message: string;
  target: DiagnosticTarget;
}

export interface Catalog {
  stereotypes: Map<string, StereotypeRow>;
  /** Keyed by property_name (the DB's own unique key, uq_field_property_name). */
  fields: Map<string, FieldRow>;
  revisions: RevisionRow[];
  diagnostics: WalkerDiagnostic[];
}

export function createCatalog(): Catalog {
  return {
    stereotypes: new Map(),
    fields: new Map(),
    revisions: [],
    diagnostics: [],
  };
}

/** Diag codes for tests/docs. */
export const DIAG = {
  ANON_MODEL: "shrapnel-anonymous-model",
  DUP_FIELD_TYPE_CONFLICT: "shrapnel-field-type-conflict",
  EXTENDS_MISSING_DOC: "shrapnel-extends-requires-rationale",
  EXTENDS_UNKNOWN_PARENT: "shrapnel-extends-unknown-parent",
  UNMAPPABLE_TYPE: "shrapnel-unmappable-type",
} as const;
