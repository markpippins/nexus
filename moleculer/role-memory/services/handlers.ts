import { Errors } from "moleculer";
import {
  getRedis,
  countKeys,
  META_UPDATED_KEY,
  PROC_KEY,
  IDX_KEY,
} from "./store";
import { syncAll } from "./sync";

/**
 * Action handlers for the role-memory twin — verbatim incumbent semantics,
 * extracted from the service so the hermetic suite can drive them directly
 * (mocked ./store) without a broker, exactly like the sibling ports.
 *
 * Envelope parity (incumbent index.ts catch blocks):
 *   - non-health catchers emit 500 {error: err.message}
 *   - slug miss throws 404 "Procedure not found"
 *   - health failure throws 503 tagged RM_HEALTH (gateway emits the
 *     incumbent's structured 503 body)
 */

const STALE_THRESHOLD_MS = 60 * 60 * 1000; // 1h — same as incumbent

function errorEnvelope(err: any): Errors.MoleculerError {
  return new Errors.MoleculerError(err?.message ?? String(err), 500, "RM_ERROR");
}

export async function healthHandler(): Promise<Record<string, unknown>> {
  try {
    const redis = getRedis();
    await redis.ping(); // Redis connectivity probe
    const lastUpdated = await redis.get(META_UPDATED_KEY);
    const [procedureCount, roleIndexCount] = await Promise.all([
      countKeys(`mem:proc:*`),
      countKeys(`mem:idx:*`),
    ]);

    const lastUpdatedMs = lastUpdated ? Date.parse(lastUpdated) : 0;
    const stale =
      !lastUpdated ||
      procedureCount === 0 ||
      roleIndexCount === 0 ||
      Date.now() - lastUpdatedMs > STALE_THRESHOLD_MS;

    return {
      status: stale ? "degraded" : "ok",
      redis: "connected",
      lastUpdated: lastUpdated || null,
      procedureCount,
      roleIndexCount,
      stale,
      staleThresholdMs: STALE_THRESHOLD_MS,
      uptime: process.uptime(),
    };
  } catch (err: any) {
    throw new Errors.MoleculerError(err?.message ?? String(err), 503, "RM_HEALTH");
  }
}

export async function proceduresHandler(ctx: any): Promise<unknown> {
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

export async function procedureHandler(ctx: any): Promise<unknown> {
  try {
    const redis = getRedis();
    const data = await redis.get(PROC_KEY(ctx.params.slug));
    if (!data) {
      throw new Errors.MoleculerError("Procedure not found", 404, "RM_NOT_FOUND");
    }
    return JSON.parse(data);
  } catch (err: any) {
    if (err?.code === 404 || err?.type === "RM_NOT_FOUND") throw err;
    throw errorEnvelope(err);
  }
}

export async function refreshHandler(): Promise<{
  procedures: number;
  roleIndices: number;
  timestamp: string;
}> {
  try {
    return await syncAll();
  } catch (err: any) {
    throw errorEnvelope(err);
  }
}
