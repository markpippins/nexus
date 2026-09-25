import { Errors } from "moleculer";
import {
  getRedis,
  META_UPDATED_KEY,
  IDX_KEY,
  PROC_KEY,
  TASK_IDX_KEY,
} from "./store";
import { syncAll } from "./sync";

/**
 * Action handlers for the prompt-sync twin — verbatim incumbent semantics,
 * extracted from index.ts so the hermetic suite can drive them directly
 * (mocked ./store) without a broker, exactly like the sibling ports.
 *
 * Envelope parity (incumbent index.ts catch blocks):
 *   - non-health catchers emit 500 {error: err.message}
 *   - prompt miss throws 404 "Prompt not found" (exact string)
 *   - health failure throws 503 tagged PS_HEALTH (gateway emits the
 *     incumbent's structured 503 body {status:"error", message})
 *   - missing role index → 200 [] (NOT 404 — incumbent quirk, deliberate)
 */

function errorEnvelope(err: any): Errors.MoleculerError {
  return new Errors.MoleculerError(err?.message ?? String(err), 500, "PS_ERROR");
}

export async function healthHandler(): Promise<Record<string, unknown>> {
  try {
    const redis = getRedis();
    const lastUpdated = await redis.get(META_UPDATED_KEY);
    return {
      status: "ok",
      lastUpdated: lastUpdated || null,
      uptime: process.uptime(),
      // Report the namespace so monitoring can distinguish this from the
      // procedure registry's mem:meta:last_updated.
      namespace: "prompt:",
    };
  } catch (err: any) {
    // Incumbent: res.status(503).json({ status: "error", message: err.message })
    throw new Errors.MoleculerError(err?.message ?? String(err), 503, "PS_HEALTH");
  }
}

export async function promptsHandler(ctx: any): Promise<unknown> {
  try {
    const redis = getRedis();
    const data = await redis.get(IDX_KEY(ctx.params.role));
    if (!data) {
      return [];
    }
    return JSON.parse(data);
  } catch (err: any) {
    throw errorEnvelope(err);
  }
}

export async function promptHandler(ctx: any): Promise<unknown> {
  try {
    const redis = getRedis();
    const data = await redis.get(PROC_KEY(ctx.params.role, ctx.params.slug));
    if (!data) {
      // Incumbent: res.status(404).json({ error: "Prompt not found" })
      throw new Errors.MoleculerError("Prompt not found", 404, "PS_NOT_FOUND");
    }
    return JSON.parse(data);
  } catch (err: any) {
    if (err?.code === 404 || err?.type === "PS_NOT_FOUND") throw err;
    throw errorEnvelope(err);
  }
}

export async function tasksHandler(ctx: any): Promise<unknown> {
  try {
    const redis = getRedis();
    const data = await redis.get(TASK_IDX_KEY(ctx.params.role));
    if (!data) {
      return [];
    }
    return JSON.parse(data);
  } catch (err: any) {
    throw errorEnvelope(err);
  }
}

export async function refreshHandler(): Promise<{
  prompts: number;
  rolePromptIndices: number;
  tasks: number;
  roleTaskIndices: number;
  timestamp: string;
}> {
  try {
    return await syncAll();
  } catch (err: any) {
    throw errorEnvelope(err);
  }
}
