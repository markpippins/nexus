/**
 * Lightweight argument validation for MCP tool handlers.
 * Replaces Zod dependency for environments without npm.
 * When zod is available, switch to Zod schemas for richer error messages.
 */

export type ValidationRule = {
  field: string;
  type: "string" | "number" | "boolean" | "array" | "object";
  required?: boolean;
  description?: string;
};

export interface ValidationError {
  field: string;
  message: string;
}

export function validate(
  args: Record<string, any>,
  rules: ValidationRule[],
): ValidationError[] {
  const errors: ValidationError[] = [];

  for (const rule of rules) {
    const value = args[rule.field];

    if (rule.required && (value === undefined || value === null)) {
      errors.push({
        field: rule.field,
        message: `"${rule.field}" is required`,
      });
      continue;
    }

    if (value === undefined || value === null) continue;

    if (rule.type === "array") {
      if (!Array.isArray(value)) {
        errors.push({
          field: rule.field,
          message: `"${rule.field}" must be an array`,
        });
      }
    } else if (typeof value !== rule.type) {
      errors.push({
        field: rule.field,
        message: `"${rule.field}" must be of type ${rule.type}, got ${typeof value}`,
      });
    }
  }

  return errors;
}
