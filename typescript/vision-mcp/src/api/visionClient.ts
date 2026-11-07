/**
 * HTTP client for the LOSM work-request API.
 * All vision-mcp tools proxy through this client to losm-host (:8006), which
 * absorbed the vision-srv /api surface per decision 5d8e10fd with the wire
 * contract preserved — the shapes below are unchanged.
 *
 * Configuration (all optional, environment-driven):
 *   - VISION_SRV_URL                     — full base URL of losm-host.
 *                                          Default: http://localhost:8006.
 *                                          (vision-srv on :8003 is retired;
 *                                          env var name kept for compatibility.)
 *                                          Both http:// and https:// are
 *                                          supported; the client dispatches
 *                                          to the matching Node transport.
 *   - VISION_SRV_TLS_REJECT_UNAUTHORIZED — "true" (default) or "false".
 *                                          Set to "false" only for dev with
 *                                          self-signed certificates.
 *
 * Override examples:
 *   VISION_SRV_URL=http://localhost:8006 \
 *     npx tsx src/index.ts
 *   VISION_SRV_URL=https://vision.internal:8443 \
 *     VISION_SRV_TLS_REJECT_UNAUTHORIZED=false \
 *     npx tsx src/index.ts
 *
 * If VISION_SRV_URL is malformed, the process exits at module load with a
 * clear, actionable error — there is no point starting an MCP server that
 * cannot reach its upstream.
 */
import * as http from "http";
import * as https from "https";

function parseBaseUrl(raw: string): URL {
  try {
    return new URL(raw);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(
      `vision-mcp: invalid VISION_SRV_URL=${JSON.stringify(raw)} — ${msg}. ` +
        `Expected form: http://host:port or https://host:port`,
    );
    process.exit(1);
  }
}

const BASE_URL = parseBaseUrl(
  process.env.VISION_SRV_URL ?? "http://localhost:8006",
);
const USE_HTTPS = BASE_URL.protocol === "https:";
const REJECT_UNAUTHORIZED =
  (process.env.VISION_SRV_TLS_REJECT_UNAUTHORIZED ?? "true").toLowerCase() !==
  "false";

function httpRequest(method: string, path: string, body?: any): Promise<any> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const headers: Record<string, string> = {};
    if (bodyStr) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(bodyStr).toString();
    }

    const defaultPort = USE_HTTPS ? 443 : 80;
    const options: http.RequestOptions = {
      hostname: BASE_URL.hostname,
      port: BASE_URL.port || String(defaultPort),
      path,
      method,
      headers,
    };
    if (USE_HTTPS) {
      (options as https.RequestOptions).rejectUnauthorized = REJECT_UNAUTHORIZED;
    }

    const transport = USE_HTTPS ? https : http;
    const req = transport.request(options, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed);
        } catch {
          resolve(data);
        }
      });
    });
    req.on("error", (err: Error) => {
      reject(new Error(`Cannot reach losm-host: ${err.message}`));
    });
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

export const VisionClient = {
  /** GET /health */
  health: () => httpRequest("GET", "/health"),

  // ── Work Requests ──────────────────────────────────────────
  /** GET /api/work-requests */
  listWorkRequests: () => httpRequest("GET", "/api/work-requests"),
  /** GET /api/work-requests/:id */
  getWorkRequest: (id: string) => httpRequest("GET", `/api/work-requests/${id}`),
  /** POST /api/work-requests */
  createWorkRequest: (body: {
    wrId?: string; intent: string; constraints?: any; priority?: number;
    context?: any; status?: string;
  }) => httpRequest("POST", "/api/work-requests", body),
  /** PATCH /api/work-requests/:id */
  updateWorkRequest: (id: string, body: {
    intent?: string; constraints?: any; priority?: number;
    context?: any; status?: string;
  }) => httpRequest("PATCH", `/api/work-requests/${id}`, body),
  /** DELETE /api/work-requests/:id */
  deleteWorkRequest: (id: string) => httpRequest("DELETE", `/api/work-requests/${id}`),

  // ── Branches ───────────────────────────────────────────────
  /** GET /api/branches */
  listBranches: () => httpRequest("GET", "/api/branches"),
  /** POST /api/branches */
  createBranch: (body: {
    branchId: string; wrId: string; parentBranchId?: string;
    forkPoint?: string; label?: string; status?: string;
  }) => httpRequest("POST", "/api/branches", body),

  // ── Artifacts ──────────────────────────────────────────────
  /** GET /api/artifacts */
  listArtifacts: () => httpRequest("GET", "/api/artifacts"),
  /** POST /api/artifacts */
  createArtifact: (body: {
    artifactId?: string; type: string; content: any; confidence?: number;
    provenance?: any; wrId?: string; parentArtifactId?: string; templateMetadata?: any;
  }) => httpRequest("POST", "/api/artifacts", body),
};
