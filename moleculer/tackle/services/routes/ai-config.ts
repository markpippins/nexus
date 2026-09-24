import { Router } from "express";
import {
  getAIConfigSnapshot,
  getAIProviders,
  getAIProvider,
  upsertAIProvider,
  deleteAIProvider,
  getAIHarnesses,
  getAIHarness,
  upsertAIHarness,
  deleteAIHarness,
  getAIModels,
  getAIModel,
  upsertAIModel,
  deleteAIModel,
  getAIRoleConfigs,
  getAIRoleConfig,
  upsertAIRoleConfig,
  upsertConfigBundles,
  upsertConfigBundle,
  deleteConfigBundle,
  getAllConfigBundles,
  getConfigBundles,
  getConfigBundle,
  deleteAIRoleConfig,
  seedDefaultAIConfig,
  importAIConfig,
  validateAIConfig,
  getResolvedRoleConfig,
  startSession,
  updateSessionPid,
  getSession,
  endSession,
  setModelVerified,
  rearmBundlesForModel,
  deactivateBundlesForUnverifiedModels,
} from "../db";
import fs from "fs";
import path from "path";
import { spawn } from "child_process";

// ── Config bundle invocation_mode validation ─────────────────────
// The DB CHECK on tackle.config_bundle.invocation_mode only accepts the
// invocation-channel vocabulary CLI | HTTP | SDK | MCP | INTERACTIVE
// (consumed by tackle-mcp and harness-srv; INTERACTIVE is the Freebuff-
// hosted channel). Older UIs sent a legacy dispatch vocabulary
// (stream/direct/batch/fallback/sync/async) that the DB rejects with a
// raw constraint error — validate here instead so every client gets a
// friendly 400 with the allowed values.
const INVOCATION_MODES = ["CLI", "HTTP", "SDK", "MCP", "INTERACTIVE"] as const;
const LEGACY_INVOCATION_MODES = new Set(["stream", "direct", "batch", "fallback", "sync", "async"]);

function invocationModeError(mode: unknown): string | null {
  if (mode === undefined || mode === null) return null; // server default (CLI) applies
  if (typeof mode !== "string" || !(INVOCATION_MODES as readonly string[]).includes(mode)) {
    const legacyHint =
      typeof mode === "string" && LEGACY_INVOCATION_MODES.has(mode.toLowerCase())
        ? ` '${mode}' is the old dispatch vocabulary — pick an invocation channel instead.`
        : "";
    return `Invalid invocation_mode '${String(mode)}'. Allowed values: ${INVOCATION_MODES.join(", ")}.${legacyHint}`;
  }
  return null;
}

function bundlesInvocationModeError(bundles: unknown): string | null {
  if (!Array.isArray(bundles)) return null;
  for (const b of bundles) {
    const err = invocationModeError((b as any)?.invocation_mode);
    if (err) return err;
  }
  return null;
}

export const aiConfigRouter = Router();

// ── Full snapshot ──────────────────────────────────────────────

aiConfigRouter.get("/", async (_req, res) => {
  try {
    res.json(await getAIConfigSnapshot());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Validate ───────────────────────────────────────────────────

aiConfigRouter.get("/validate", async (_req, res) => {
  try {
    const warnings = await validateAIConfig();
    res.json({ valid: warnings.length === 0, warnings });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Seed defaults ──────────────────────────────────────────────

aiConfigRouter.post("/seed-defaults", async (req, res) => {
  try {
    const { force } = req.body || {};
    const result = await seedDefaultAIConfig(!!force);
    res.json(result);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Import full snapshot ───────────────────────────────────────

aiConfigRouter.post("/import", async (req, res) => {
  try {
    const { providers, harnesses, models, roles, bundles } = req.body || {};
    if (!providers && !harnesses && !models && !roles && !bundles) {
      res.status(400).json({ error: "No import data provided" });
      return;
    }
    const modeErr = bundlesInvocationModeError(bundles);
    if (modeErr) {
      res.status(400).json({ error: modeErr, allowed: INVOCATION_MODES });
      return;
    }
    const result = await importAIConfig({
      providers: providers || [],
      harnesses: harnesses || [],
      models: models || [],
      roles: roles || [],
      bundles: bundles || [],
    });
    res.json({ imported: true, ...result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Providers ──────────────────────────────────────────────────

aiConfigRouter.get("/providers", async (_req, res) => {
  try {
    res.json(await getAIProviders());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/provider/:id", async (req, res) => {
  try {
    const provider = await getAIProvider(req.params.id);
    if (!provider) {
      res.status(404).json({ error: "Provider not found" });
      return;
    }
    res.json(provider);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/provider", async (req, res) => {
  try {
    const { id, name, type, endpoint_url, api_key, config_json } = req.body || {};
    if (!id || !name || !type) {
      res.status(400).json({ error: "id, name, and type are required" });
      return;
    }
    await upsertAIProvider({ id, name, type, endpoint_url, api_key, config_json });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.delete("/provider/:id", async (req, res) => {
  try {
    const deleted = await deleteAIProvider(req.params.id);
    res.json({ deleted, id: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Harnesses ──────────────────────────────────────────────────

aiConfigRouter.get("/harnesses", async (_req, res) => {
  try {
    res.json(await getAIHarnesses());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/harness/:id", async (req, res) => {
  try {
    const harness = await getAIHarness(req.params.id);
    if (!harness) {
      res.status(404).json({ error: "Harness not found" });
      return;
    }
    res.json(harness);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/harness", async (req, res) => {
  try {
    const { id, name, invocation_semantics } = req.body || {};
    if (!id || !name) {
      res.status(400).json({ error: "id and name are required" });
      return;
    }
    await upsertAIHarness({ id, name, invocation_semantics });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.delete("/harness/:id", async (req, res) => {
  try {
    const deleted = await deleteAIHarness(req.params.id);
    res.json({ deleted, id: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Models ─────────────────────────────────────────────────────

aiConfigRouter.get("/models", async (_req, res) => {
  try {
    res.json(await getAIModels());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/model/:id", async (req, res) => {
  try {
    const model = await getAIModel(req.params.id);
    if (!model) {
      res.status(404).json({ error: "Model not found" });
      return;
    }
    res.json(model);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/model", async (req, res) => {
  try {
    const { id, name, harness_id, provider_id, model_identifier } = req.body || {};
    if (!id || !name || !harness_id || !model_identifier) {
      res.status(400).json({ error: "id, name, harness_id, and model_identifier are required" });
      return;
    }
    await upsertAIModel({ id, name, harness_id, provider_id, model_identifier });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.delete("/model/:id", async (req, res) => {
  try {
    const deleted = await deleteAIModel(req.params.id);
    res.json({ deleted, id: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Role Configs ───────────────────────────────────────────────

aiConfigRouter.get("/roles", async (_req, res) => {
  try {
    res.json(await getAIRoleConfigs());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/role/:role", async (req, res) => {
  try {
    const role = await getAIRoleConfig(req.params.role);
    if (!role) {
      res.status(404).json({ error: "Role config not found" });
      return;
    }
    res.json(role);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/role", async (req, res) => {
  try {
    console.log('[tackle-srv] POST /config/ai/role body:', JSON.stringify(req.body, null, 2));
    const { id, role, provider_id, harness_id, model_id, extra_params, bundles } = req.body || {};
    console.log('[tackle-srv] Destructured bundles:', bundles);
    if (!id || !role || !provider_id || !harness_id || !model_id) {
      res.status(400).json({ error: "id, role, provider_id, harness_id, and model_id are required" });
      return;
    }

    if (Array.isArray(bundles) && bundles.length > 0) {
      const modeErr = bundlesInvocationModeError(bundles);
      if (modeErr) {
        res.status(400).json({ error: modeErr, allowed: INVOCATION_MODES });
        return;
      }
      console.log('[tackle-srv] Saving bundles for role:', role, 'count:', bundles.length);
      await upsertConfigBundles(role, bundles);
    } else {
      await upsertAIRoleConfig({ id, role, provider_id, harness_id, model_id, extra_params });
    }

    res.json({ saved: true, id, role });
  } catch (e: any) {
    console.error('[tackle-srv] Error saving role:', e.message);
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.delete("/role/:role", async (req, res) => {
  try {
    const deleted = await deleteAIRoleConfig(req.params.role);
    res.json({ deleted, role: req.params.role });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Config Bundles ─────────────────────────────────────────────

aiConfigRouter.get("/bundles", async (_req, res) => {
  try {
    res.json(await getAllConfigBundles());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/bundles/:role", async (req, res) => {
  try {
    res.json(await getConfigBundles(req.params.role));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/bundle/:id", async (req, res) => {
  try {
    const bundle = await getConfigBundle(req.params.id);
    if (!bundle) {
      res.status(404).json({ error: "Bundle not found" });
      return;
    }
    res.json(bundle);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/bundle", async (req, res) => {
  try {
    const { id, name, role, model_id, provider_id, harness_id, priority,
            invocation_mode, command, endpoint_url, timeout_ms,
            valid_from, valid_to, is_active, metadata } = req.body || {};
    if (!name || !role || !model_id) {
      res.status(400).json({ error: "name, role, and model_id are required" });
      return;
    }
    const modeErr = invocationModeError(invocation_mode);
    if (modeErr) {
      res.status(400).json({ error: modeErr, allowed: INVOCATION_MODES });
      return;
    }
    // Normalize the UI boolean to an integer (is_active is INTEGER in PG).
    // The verified-model gate inside upsertConfigBundle forces this to 0 when
    // the bundle's model is unverified, so passing 1 here is safe — the DB
    // layer is the source of truth.
    const isActive = is_active === true || is_active === 1 || is_active === "1" ? 1 : 0;
    // Auto-generate id for new bundles (mock-mode parity: the UI does not
    // send an id when creating). Editing bundles carries an id → upsert.
    const bundleId = id || `bundle-${Date.now().toString(36)}`;
    await upsertConfigBundle({
      id: bundleId, name, role, model_id,
      provider_id, harness_id, priority,
      invocation_mode, command, endpoint_url, timeout_ms,
      valid_from, valid_to, is_active: isActive, metadata,
    });
    res.json({ saved: true, id: bundleId });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.delete("/bundle/:id", async (req, res) => {
  try {
    const deleted = await deleteConfigBundle(req.params.id);
    res.json({ deleted, id: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.post("/bundles/:role", async (req, res) => {
  try {
    const { bundles } = req.body || {};
    if (!Array.isArray(bundles) || bundles.length === 0) {
      res.status(400).json({ error: "bundles array is required" });
      return;
    }
    const modeErr = bundlesInvocationModeError(bundles);
    if (modeErr) {
      res.status(400).json({ error: modeErr, allowed: INVOCATION_MODES });
      return;
    }
    await upsertConfigBundles(req.params.role, bundles);
    res.json({ saved: true, role: req.params.role, count: bundles.length });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Resolved Config ────────────────────────────────────────────

aiConfigRouter.get("/resolve/:role", async (req, res) => {
  try {
    const config = await getResolvedRoleConfig(req.params.role);
    if (!config) {
      res.status(404).json({ error: `No config found for role '${req.params.role}'` });
      return;
    }
    res.json(config);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Test Invoke ────────────────────────────────────────────────

aiConfigRouter.post("/test", async (req, res) => {
  try {
    const { model_id, test_prompt } = req.body || {};
    if (!model_id || !test_prompt) {
      res.status(400).json({ error: "model_id and test_prompt are required" });
      return;
    }

    const models = await getAIModels();
    const model = models.find((m: any) => m.id === model_id);
    if (!model) {
      res.status(404).json({ error: `Model ${model_id} not found` });
      return;
    }

    // Verified-model gate: refuse test invocations for models that have not
    // been verified. Unverified models previously spawned opencode with an
    // unresolvable model id — the run silently failed with "model not found"
    // in the log and the UI hung polling an empty output.
    if (!model.verified) {
      res.status(400).json({
        error: `Model ${model_id} is not verified — test invocation refused. Verify the model through a successful harness run before testing.`,
      });
      return;
    }

    const harnesses = await getAIHarnesses();
    const harness = harnesses.find((h: any) => h.id === model.harness_id);
    if (!harness) {
      res.status(404).json({ error: `Harness ${model.harness_id} not found` });
      return;
    }

    // Provider-qualify the opencode --model flag (bare ids fail to resolve).
    const providers = await getAIProviders();
    const provider = providers.find((p: any) => p.id === model.provider_id);
    const runModelId = openCodeModelArg(model, provider);

    let harnessType = "opencode";
    try {
      const sem = JSON.parse(harness.invocation_semantics || "{}");
      const binary = (sem.binary || "opencode").toLowerCase();
      if (binary.includes("codex")) harnessType = "codex";
      else if (binary.includes("ollama")) harnessType = "ollama";
      else harnessType = "opencode";
    } catch { /* use default */ }

    const now = new Date().toISOString();
    const sessionId = `test-${model_id}-${Date.now()}`;
    await startSession({
      id: sessionId,
      agent_role: "test",
      start_iso: now,
      model: model.model_identifier,
    });

    const projectRoot = process.env.PIPELINE_ROOT || "/home/codex/dev";
    // .conduit-data was deleted 2026-08-09 and mirrored to audit/CONDUIT_DATA
    const sessionsDir = path.join(projectRoot, "nexus", "logs");
    fs.mkdirSync(sessionsDir, { recursive: true });
    const sessionLogPath = path.join(sessionsDir, `${sessionId}.log`);
    const logFd = fs.openSync(sessionLogPath, "a");

    const proc = spawn(harnessType, [
      "run", "--model", runModelId,
      "--dir", projectRoot,
      "--print-logs", "--log-level", "ERROR",
      test_prompt,
    ], {
      detached: true,
      stdio: ["ignore", logFd, logFd],
    });
    fs.closeSync(logFd);
    proc.unref();

    if (proc.pid && proc.pid > 0) {
      await updateSessionPid(sessionId, proc.pid);
    }

    const timestamp = new Date().toISOString();
    console.log(`[${timestamp}] TEST INVOKE model=${model_id} session=${sessionId} pid=${proc.pid} log=${sessionLogPath}`);

    res.json({
      started: true,
      sessionId,
      model_id,
      model_name: model.name,
      model_identifier: model.model_identifier,
      harness: harnessType,
      logPath: `/log/${sessionId}`,
      timestamp,
    });
  } catch (e: any) {
    console.error(`[${new Date().toISOString()}] TEST INVOKE error:`, e.message);
    res.status(500).json({ error: e.message });
  }
});

// ── Verify Model ────────────────────────────────────────────────────
// Runs a real inference through the model's harness and, on a clean exit
// (plus a log scan for the classic unresolvable-model markers), flips the
// model to verified=true and re-arms every config bundle referencing it.
// Fire-and-forget like /test — the client polls GET /verify/:sessionId.

// opencode resolves models as `<providerKey>/<modelId>` (e.g.
// ollama/qwen2.5-coder, nvidia/z-ai/glm-5.2, openrouter/~anthropic/...).
// Passing the bare identifier makes opencode treat the whole string as the
// provider name → ProviderModelNotFoundError "<id>/." — the classic
// "model not found" failure.
//
// The provider key is the models.dev provider id (nvidia, openrouter,
// mistral, opencode-go, ...) — which is NOT expressible in tackle's
// providers.type (CHECK-constrained to openai/anthropic/google/ollama/
// opencode/codex/...). bin/sync_modelsdev_models.py stores the key in
// config_json.opencodeProvider; the type is the fallback for the built-ins
// whose type already matches their models.dev key.
const OPENCODE_PROVIDER_TYPES = ["ollama", "openai", "anthropic", "google", "codex", "opencode"];

function opencodeProviderKey(provider: any): string | null {
  if (!provider) return null;
  try {
    const cfg = JSON.parse(provider.config_json || "{}");
    if (cfg.opencodeProvider) return String(cfg.opencodeProvider);
  } catch {
    /* malformed config_json — fall through to type */
  }
  const t = provider.type ? String(provider.type).toLowerCase() : "";
  return OPENCODE_PROVIDER_TYPES.includes(t) ? t : null;
}

// The canonical reference is ALWAYS `providerKey/modelId` — prefix
// unconditionally. models.dev ids may themselves contain a provider/org
// path (nvidia ids like `z-ai/glm-5.2` or `nvidia/nemotron-3-ultra-
// 550b-a55b`, openrouter ids like `~anthropic/...` or the literal
// `openrouter/free` router) — opencode splits at the first slash and passes
// the remainder to the provider API verbatim, so the identifier must never
// be rewritten or stripped.
function openCodeModelArg(model: any, provider: any): string {
  const id = model.model_identifier;
  const key = opencodeProviderKey(provider);
  if (!key) return id;
  return `${key}/${id}`;
}

// Bounds a verify run: if the harness wedges, SIGKILL the child so the
// session always settles and the model is marked unverified. 20 minutes
// gives slow local (CPU ollama) models room to finish — the classic wedge
// (a rate-limited title-generation small model blocking finalization) is
// addressed by pinning small_model to a local model in opencode.json.
const VERIFY_WATCHDOG_MS = 20 * 60 * 1000;

const VERIFY_DEFAULT_PROMPT =
  "Reply with the single word OK and nothing else. Do not add any explanation.";
// OpenCode can exit 0 while still reporting an unresolvable model — treat
// these markers in the log tail as failure regardless of exit code.
const VERIFY_FAIL_MARKERS =
  /ProviderModelNotFoundError|Model not found|model not found|No such model|unauthorized/i;

aiConfigRouter.post("/verify", async (req, res) => {
  try {
    const { model_id, test_prompt } = req.body || {};
    if (!model_id) {
      res.status(400).json({ error: "model_id is required" });
      return;
    }

    const models = await getAIModels();
    const model = models.find((m: any) => m.id === model_id);
    if (!model) {
      res.status(404).json({ error: `Model ${model_id} not found` });
      return;
    }

    const harnesses = await getAIHarnesses();
    const harness = harnesses.find((h: any) => h.id === model.harness_id);
    if (!harness) {
      res.status(404).json({ error: `Harness ${model.harness_id} not found` });
      return;
    }

    // Resolve the provider type so the opencode --model flag can be
    // provider-qualified (fixes the bare-id ProviderModelNotFoundError).
    const providers = await getAIProviders();
    const provider = providers.find((p: any) => p.id === model.provider_id);
    const runModelId = openCodeModelArg(model, provider);

    let harnessType = "opencode";
    try {
      const sem = JSON.parse(harness.invocation_semantics || "{}");
      const binary = (sem.binary || "opencode").toLowerCase();
      if (binary.includes("codex")) harnessType = "codex";
      else if (binary.includes("ollama")) harnessType = "ollama";
      else harnessType = "opencode";
    } catch { /* use default */ }

    const prompt = test_prompt || VERIFY_DEFAULT_PROMPT;
    const now = new Date().toISOString();
    const sessionId = `verify-${model_id}-${Date.now()}`;
    await startSession({
      id: sessionId,
      agent_role: "test",
      start_iso: now,
      model: model.model_identifier,
    });

    const projectRoot = process.env.PIPELINE_ROOT || "/home/codex/dev";
    const sessionsDir = path.join(projectRoot, "nexus", "logs");
    fs.mkdirSync(sessionsDir, { recursive: true });
    const sessionLogPath = path.join(sessionsDir, `${sessionId}.log`);
    const logFd = fs.openSync(sessionLogPath, "a");

    const proc = spawn(harnessType, [
      "run", "--model", runModelId,
      "--dir", projectRoot,
      "--print-logs", "--log-level", "ERROR",
      prompt,
    ], {
      detached: true,
      stdio: ["ignore", logFd, logFd],
    });
    fs.closeSync(logFd);

    // Deliberately NOT unref'd: hold the child handle so the completion
    // watcher below can flip verified/re-arm bundles when the run finishes.
    let settled = false;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const onDone = async (exitCode: number | null) => {
      if (settled) return;
      settled = true;
      if (watchdog) clearTimeout(watchdog);
      const doneIso = new Date().toISOString();
      let success = exitCode === 0;
      if (success) {
        // Belt-and-braces: opencode can exit 0 while printing the error.
        try {
          const tail = fs.readFileSync(sessionLogPath, "utf8").slice(-4000);
          if (VERIFY_FAIL_MARKERS.test(tail)) success = false;
        } catch {
          /* keep exit-code verdict */
        }
      }
      try {
        if (success) {
          await setModelVerified(model_id, true);
          const rearmed = await rearmBundlesForModel(model_id);
          console.log(`[verify] ${model_id} VERIFIED — ${rearmed} bundle(s) re-armed`);
        } else {
          // Failed reverify (or first-time verify): the model is no longer
          // certifiably working — mark it unverified so its bundles are forced
          // inactive (verified-model gate) and the UI surfaces it for role
          // updates. This is the "beta model changed names / no longer works"
          // case: a previously-verified model that now fails gets de-registered.
          await setModelVerified(model_id, false);
          console.log(`[verify] ${model_id} marked UNVERIFIED (reverify failed)`);
        }
        await endSession(sessionId, exitCode ?? -1, doneIso);
      } catch (e: any) {
        console.error(`[verify] ${sessionId} post-exit update failed:`, e.message);
      }
      console.log(`[verify] model=${model_id} session=${sessionId} exit=${exitCode} → ${success ? "VERIFIED" : "FAILED"}`);
    };
    proc.on("exit", (code) => onDone(code));
    proc.on("error", (err) => {
      console.error(`[verify] ${sessionId} spawn error:`, err.message);
      onDone(null);
    });

    // Watchdog: never let a hung harness hold the session open forever
    // (e.g. a rate-limited small model blocks opencode's finalization).
    // Cleared inside onDone when the run settles.
    watchdog = setTimeout(() => {
      if (settled) return;
      console.error(`[verify] ${sessionId} watchdog fired after ${VERIFY_WATCHDOG_MS / 60000}min — killing hung harness`);
      try {
        proc.kill("SIGKILL");
      } catch {
        /* child already gone — exit handler settles */
      }
    }, VERIFY_WATCHDOG_MS);

    if (proc.pid && proc.pid > 0) {
      await updateSessionPid(sessionId, proc.pid);
    }

    const timestamp = new Date().toISOString();
    console.log(`[${timestamp}] VERIFY model=${model_id} session=${sessionId} pid=${proc.pid} log=${sessionLogPath}`);

    res.json({
      started: true,
      verified: false,
      sessionId,
      model_id,
      model_name: model.name,
      model_identifier: model.model_identifier,
      harness: harnessType,
      logPath: `/log/${sessionId}`,
      message: "Verification run started — the model flips to verified on a clean exit.",
      timestamp,
    });
  } catch (e: any) {
    console.error(`[${new Date().toISOString()}] VERIFY error:`, e.message);
    res.status(500).json({ error: e.message });
  }
});

aiConfigRouter.get("/verify/:sessionId", async (req, res) => {
  try {
    const session = await getSession(req.params.sessionId);
    if (!session) {
      res.status(404).json({ error: "Verify session not found" });
      return;
    }
    let running = session.is_running === 1;
    // Stale-session recovery: if the service died mid-verify the in-process
    // watcher is gone and the session would report running forever. Treat
    // sessions older than the watchdog window (+2 min grace) as orphaned and
    // settle them as failed on first poll.
    let staleSettled = false;
    if (running && session.start_iso) {
      const ageMs = Date.now() - new Date(session.start_iso).getTime();
      if (ageMs > VERIFY_WATCHDOG_MS + 2 * 60 * 1000) {
        const nowIso = new Date().toISOString();
        await endSession(session.id, -1, nowIso);
        session.exit_code = -1;
        session.end_iso = nowIso;
        running = false;
        staleSettled = true;
      }
    }
    res.json({
      sessionId: session.id,
      running,
      exit_code: session.exit_code,
      end_iso: session.end_iso,
      model_identifier: session.model,
      verified: running ? null : session.exit_code === 0,
      stale_settled: staleSettled,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Purge unverified models from the inference chain ────────────────
// Force-deactivates every config bundle (across all roles) whose model is
// unverified or missing, so no role's resolver queue can select a bundle whose
// model has not been certified. Returns per-role affected bundle counts.
aiConfigRouter.post("/verify/purge-unverified", async (_req, res) => {
  try {
    const result = await deactivateBundlesForUnverifiedModels();
    const total = result.reduce((sum, r) => sum + r.bundleIds.length, 0);
    res.json({ purged: total, roles: result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
