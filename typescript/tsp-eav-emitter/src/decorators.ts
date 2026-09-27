/**
 * Decorator plumbing for tsp-eav-emitter.
 *
 * Three decorators, one storage-class doctrine question made concrete:
 *
 *   @instanceStorage("shrapnel" | "mongodb" | "jsonb_table")  — on a MODEL.
 *     Records where instances of this shape live. This is the concrete
 *     TypeSpec surface for the Discussions storage-class thread: the value
 *     is compiled into a registration artifact, NOT into the type contract
 *     (no stereotype_field row, no fingerprint involvement) — so storage
 *     class can evolve without forcing a new stereotype revision. The
 *     "metadata outside the contract" option, as a working demonstration.
 *
 *   @extendsRationale("…")  — on a MODEL that extends another.
 *     The dedicated home for the C2 extends rationale (doc-as-rationale
 *     remains the fallback when this decorator is absent).
 *
 *   @calculated  — on a PROPERTY.
 *     Marks the field is_calculated = true (shrapnel.field column; value
 *     derivation semantics live outside the catalog contract).
 *
 * State is stored via library-scoped program state maps (the canonical
 * decorator-state mechanism for this @typespec/compiler generation).
 */
import type { DecoratorContext, Model, ModelProperty, Program, StringLiteral, Type } from "@typespec/compiler";
import { createTypeSpecLibrary, paramMessage } from "@typespec/compiler";

/**
 * String literal arguments arrive as StringLiteral TYPES (kind: "String",
 * value: string), not raw strings, in this compiler generation.
 */
function lit(value: unknown): string {
  if (typeof value === "string") return value;
  const t = value as Type;
  if (t && t.kind === "String") return (t as StringLiteral).value;
  return "";
}

export const STORAGE_CLASSES = ["shrapnel", "mongodb", "jsonb_table"] as const;
export type StorageClass = (typeof STORAGE_CLASSES)[number];

const libDef = {
  name: "@nexus/tsp-eav-emitter",
  diagnostics: {
    "invalid-storage-class": {
      severity: "error",
      messages: {
        default: paramMessage`Unknown instance storage class '${"value"}'. Allowed: shrapnel, mongodb, jsonb_table.`,
      },
    },
    "rationale-empty": {
      severity: "error",
      messages: {
        default: "extendsRationale must be a non-empty string.",
      },
    },
  },
} as const;

export const lib = createTypeSpecLibrary(libDef);

/** model -> storage class */
const storageKey = lib.createStateSymbol("instanceStorage");
export function getInstanceStorage(program: Program, model: Model): StorageClass | undefined {
  return program.stateMap(storageKey).get(model) as StorageClass | undefined;
}

/** model -> rationale string */
const rationaleKey = lib.createStateSymbol("extendsRationale");
export function getExtendsRationale(program: Program, model: Model): string | undefined {
  return program.stateMap(rationaleKey).get(model) as string | undefined;
}

/** property -> true */
const calculatedKey = lib.createStateSymbol("calculated");
export function isCalculatedField(program: Program, prop: ModelProperty): boolean {
  return program.stateMap(calculatedKey).get(prop) === true;
}

export namespace ShrapnelCatalog {
  /**
   * @instanceStorage("mongodb") — where instances of this stereotype live.
   * NOT part of the frozen contract (see module docstring).
   */
  export function $instanceStorage(
    context: DecoratorContext,
    target: Model,
    storage: string | Type
  ): void {
    const value = lit(storage);
    if (!STORAGE_CLASSES.includes(value as StorageClass)) {
      lib.reportDiagnostic(context.program, {
        code: "invalid-storage-class",
        format: { value: value || String(storage) },
        target: context.decoratorTarget,
      });
      return;
    }
    context.program.stateMap(storageKey).set(target, value);
  }

  /**
   * @extendsRationale("…") — C2 justification for extends, author-owned.
   */
  export function $extendsRationale(
    context: DecoratorContext,
    target: Model,
    rationale: string | Type
  ): void {
    const value = lit(rationale).trim();
    if (!value) {
      lib.reportDiagnostic(context.program, {
        code: "rationale-empty",
        target: context.decoratorTarget,
      });
      return;
    }
    context.program.stateMap(rationaleKey).set(target, value);
  }

  /**
   * @calculated — field is computed, not directly written.
   */
  export function $calculated(context: DecoratorContext, target: ModelProperty): void {
    context.program.stateMap(calculatedKey).set(target, true);
  }
}
