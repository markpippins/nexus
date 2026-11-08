/**
 * Validation for tackle's AI configuration graph.
 *
 * Pure: no DB access. `validateAIConfig()` in db.ts feeds it a snapshot, which
 * also makes every rule here directly testable.
 *
 * Severity contract (consumed by GET /config/ai/validate):
 *
 *   error   — a role that can receive work now cannot resolve a runnable
 *             configuration. Errors alone set `valid: false`.
 *   warning — suspicious but runnable (e.g. a provider row is missing while
 *             the resolver still has an endpoint).
 *   info    — historical residue: findings on INACTIVE bundles. Those rows are
 *             retained for audit and are never resolved, so they must not
 *             block the system. They used to be `error`, which meant two
 *             retired Gemini references made the whole config report
 *             valid=false and blocked harness preflight (2026-11-07).
 *
 * INTERACTIVE bundles are hosted by Freebuff and have no local CLI binary by
 * design (`harn-freebuff.invocation_semantics.binary = null`), so the binary
 * requirement applies only to CLI bundles.
 */
import type {
  AIConfigSnapshot,
  ConfigBundleRow,
  ConfigValidationWarning,
} from "./db.js";

function parseJsonSafe(text: string, fallback: any): any {
  try {
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

/** Only CLI bundles spawn a local process; hosted channels do not. */
export function bundleNeedsBinary(
  mode: string | null | undefined,
): boolean {
  return (mode ?? "CLI") === "CLI";
}

export function validateAIConfigSnapshot(
  cfg: AIConfigSnapshot,
): ConfigValidationWarning[] {
  const warnings: ConfigValidationWarning[] = [];

  const harnessMap = new Map(cfg.harnesses.map((h) => [h.id, h]));
  const modelMap = new Map(cfg.models.map((m) => [m.id, m]));
  const providerMap = new Map(cfg.providers.map((p) => [p.id, p]));
  const bundleMap = new Map<string, ConfigBundleRow[]>();
  for (const b of cfg.bundles) {
    const list = bundleMap.get(b.role) || [];
    list.push(b);
    bundleMap.set(b.role, list);
  }

  for (const rc of cfg.roles) {
    const bundles = (bundleMap.get(rc.role) || [])
      .slice()
      .sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0));

    // The resolver's primary is the lowest-priority ACTIVE bundle for the
    // role. Match the snapshot's primary model first so this stays honest if
    // the role view and the bundle table ever disagree.
    const primaryBundle =
      bundles.find((b) => b.is_active === 1 && b.model_id === rc.model_id) ||
      bundles.find((b) => b.is_active === 1);
    const primaryMode = primaryBundle?.invocation_mode ?? "CLI";

    if (!modelMap.has(rc.model_id)) {
      warnings.push({
        role: rc.role,
        field: "model_id",
        message: `Primary model '${rc.model_id}' not found in models table.`,
        severity: "error",
      });
    } else {
      const model = modelMap.get(rc.model_id)!;
      const harness = harnessMap.get(model.harness_id);
      if (!harness) {
        warnings.push({
          role: rc.role,
          field: "harness_id",
          message: `Primary model '${rc.model_id}' references harness '${model.harness_id}' which does not exist.`,
          severity: "error",
        });
      } else if (
        bundleNeedsBinary(primaryMode) &&
        !parseJsonSafe(harness.invocation_semantics, {})?.binary
      ) {
        warnings.push({
          role: rc.role,
          field: "invocation_semantics.binary",
          message: `Harness '${harness.name}' for primary model '${model.name}' has no 'binary' in invocation_semantics.`,
          severity: "error",
        });
      }
      if (model.provider_id && !providerMap.has(model.provider_id)) {
        warnings.push({
          role: rc.role,
          field: "provider_id",
          message: `Primary model '${rc.model_id}' references provider '${model.provider_id}' which does not exist.`,
          severity: "warning",
        });
      }
    }

    for (const b of bundles) {
      const active = b.is_active === 1;
      // Inactive bundles are audit history, never resolved. Their findings
      // stay visible but must not decide whether the system is usable.
      const severity = (s: "error" | "warning"): "error" | "warning" | "info" =>
        active ? s : "info";

      if (!modelMap.has(b.model_id)) {
        warnings.push({
          role: rc.role,
          field: "model_id",
          message: active
            ? `Bundle model '${b.model_id}' not found in models table.`
            : `Bundle model '${b.model_id}' not found in models table (inactive historical bundle — retired reference, not resolvable).`,
          severity: severity("error"),
        });
        continue;
      }

      const model = modelMap.get(b.model_id)!;
      const harnessId = b.harness_id || model.harness_id;
      const harness = harnessMap.get(harnessId);
      if (!harness) {
        warnings.push({
          role: rc.role,
          field: "harness_id",
          message: `Bundle model '${b.model_id}' references harness '${harnessId}' which does not exist.`,
          severity: severity("error"),
        });
      } else if (
        bundleNeedsBinary(b.invocation_mode) &&
        !parseJsonSafe(harness.invocation_semantics, {})?.binary
      ) {
        warnings.push({
          role: rc.role,
          field: "invocation_semantics.binary",
          message: `Harness '${harness.name}' for bundle model '${model.name}' has no 'binary' in invocation_semantics.`,
          severity: severity("error"),
        });
      }

      const providerId = b.provider_id || model.provider_id;
      if (providerId && !providerMap.has(providerId)) {
        warnings.push({
          role: rc.role,
          field: "provider_id",
          message: `Bundle model '${b.model_id}' references provider '${providerId}' which does not exist.`,
          severity: severity("warning"),
        });
      }
    }

    if (bundles.length === 0) {
      warnings.push({
        role: rc.role,
        field: "bundles",
        message: `Role '${rc.role}' has no config bundles configured. If the primary model fails, execution will halt.`,
        severity: "warning",
      });
    }
  }

  return warnings;
}

/** The findings that make a configuration unusable (`valid: false`). */
export function configValidationErrors(
  warnings: ConfigValidationWarning[],
): ConfigValidationWarning[] {
  return warnings.filter((w) => w.severity === "error");
}
