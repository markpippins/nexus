/**
 * Route-contract drift guard for the conduit-mcp HTTP client.
 *
 * History (PR #448, 2026-09-22): the client drifted from the deployed
 * server's route contract — it called bare `/api/{plan_id}/latest-type`
 * while the deployed Python conduit serves `/api/receipts/...` (same for
 * sessions/breaker). Because the HTTP helpers swallow 404s as null, the
 * drift surfaced not as an error but as `"current state is none"` from
 * validateReceipt — silently freezing the entire builder backlog.
 *
 * This module re-verifies the contract at conduit-mcp boot against the
 * server's live openapi.json, so the same class of drift fails loudly at
 * startup instead of silently at first receipt.
 *
 * Failure semantics:
 *   - server UNREACHABLE  → warn loudly, continue (tools will throw real
 *     connection errors per call; that failure mode was never silent).
 *   - server reachable + route missing → DRIFT: exit(1) in strict mode
 *     (default; systemd restart loop = loud), log in warn mode.
 *   - CONDUIT_ROUTE_GUARD=off skips the check entirely.
 */

export interface RequiredRoute {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  /** Path template with {param} wildcards, as the server's openapi declares it. */
  path: string;
  why: string;
}

export const REQUIRED_ROUTES: RequiredRoute[] = [
  // receipts (validateReceipt / issue_receipt / get_plan_receipts)
  { method: "GET", path: "/api/receipts/{plan_id}/latest-type", why: "validateReceipt state resolution — the PR #448 freeze" },
  { method: "GET", path: "/api/receipts/{plan_id}/raw", why: "receipt chain (position-aware fan-out)" },
  { method: "GET", path: "/api/receipts/{plan_id}", why: "get_plan_receipts tool" },
  { method: "POST", path: "/api/receipts/", why: "issue_receipt persistence" },
  { method: "DELETE", path: "/api/receipts/{plan_id}", why: "unblock/revise receipt cleanup" },
  // sessions
  { method: "GET", path: "/api/sessions/", why: "getAllSessions" },
  { method: "GET", path: "/api/sessions/{session_id}", why: "getSession" },
  { method: "GET", path: "/api/sessions/running", why: "getRunningSessions" },
  { method: "GET", path: "/api/sessions/stale", why: "getStaleSessions" },
  { method: "PATCH", path: "/api/sessions/{session_id}/cost", why: "updateSessionCost" },
  { method: "POST", path: "/api/sessions/{session_id}/heartbeat", why: "updateSessionHeartbeat" },
  { method: "POST", path: "/api/sessions/{session_id}/kill", why: "killSession" },
  // circuit breaker
  { method: "GET", path: "/api/breaker/", why: "isConduitPaused" },
  { method: "POST", path: "/api/breaker/trip", why: "tripBreaker" },
  { method: "POST", path: "/api/breaker/reset", why: "clearBreaker" },
  { method: "POST", path: "/api/breaker/pause", why: "setConduitPaused(true)" },
  { method: "POST", path: "/api/breaker/resume", why: "setConduitPaused(false)" },
  { method: "GET", path: "/api/breaker/failure-recovery", why: "getFailureRecoveryConfig" },
  { method: "POST", path: "/api/breaker/failure-recovery", why: "saveFailureRecoveryConfig" },
  { method: "GET", path: "/healthz", why: "checkHealth" },
];

/** Match a required template ("{param}" = any single segment) against a served openapi path. */
export function matchesTemplate(template: string, served: string): boolean {
  const t = template.split("/").filter((s) => s !== "");
  const s = served.split("/").filter((s) => s !== "");
  if (t.length !== s.length) return false;
  return t.every((seg, i) => seg.startsWith("{") === s[i].startsWith("{") && (seg.startsWith("{") || seg === s[i]));
}

export function routeIsServed(
  openapiPaths: Record<string, Record<string, unknown>>,
  route: RequiredRoute,
): boolean {
  for (const [servedPath, methods] of Object.entries(openapiPaths)) {
    if (!matchesTemplate(route.path, servedPath)) continue;
    if (methods && typeof methods === "object" && route.method.toLowerCase() in methods) return true;
  }
  return false;
}

export interface ContractResult {
  serverReachable: boolean;
  ok: boolean;
  missing: { method: string; path: string; why: string }[];
  checked: number;
  baseUrl: string;
  error?: string;
}

export async function checkRouteContract(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ContractResult> {
  const result: ContractResult = { serverReachable: false, ok: false, missing: [], checked: 0, baseUrl };
  try {
    const res = await fetchImpl(`${baseUrl.replace(/\/$/, "")}/openapi.json`, {
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      result.error = `openapi.json → ${res.status}`;
      return result;
    }
    const spec = (await res.json()) as { paths?: Record<string, Record<string, unknown>> };
    result.serverReachable = true;
    const paths = spec.paths ?? {};
    result.checked = REQUIRED_ROUTES.length;
    result.missing = REQUIRED_ROUTES.filter((r) => !routeIsServed(paths, r)).map(
      ({ method, path, why }) => ({ method, path, why }),
    );
    result.ok = result.missing.length === 0;
    return result;
  } catch (e: any) {
    result.error = e?.message || String(e);
    return result;
  }
}

export async function enforceRouteContractAtBoot(
  baseUrl: string,
  log = console,
): Promise<"ok" | "unreachable" | "drift" | "off"> {
  const mode = (process.env.CONDUIT_ROUTE_GUARD || "strict").toLowerCase();
  if (mode === "off") return "off";
  const r = await checkRouteContract(baseUrl);
  if (!r.serverReachable) {
    log.warn(`[route-guard] ${baseUrl} unreachable (${r.error}) — contract NOT verified; API-backed tools will fail loudly per call`);
    return "unreachable";
  }
  if (r.ok) {
    log.log(`[route-guard] contract OK: ${r.checked} required routes served by ${baseUrl}`);
    return "ok";
  }
  const lines = r.missing.map((m) => `  MISSING ${m.method} ${m.path}  (${m.why})`).join("\n");
  const banner = `[route-guard] ROUTE-CONTRACT DRIFT against ${baseUrl} — ${r.missing.length}/${r.checked} required routes absent from the served openapi.json. API-backed tools would fail SILENTLY (404 → null). PR #448 lesson, 2026-09-22.\n${lines}`;
  if (mode === "warn") {
    log.warn(banner);
    return "drift";
  }
  log.error(banner);
  throw new Error(`route-contract drift: ${r.missing.length}/${r.checked} required routes missing from ${baseUrl}`);
}
