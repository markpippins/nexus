/** Shared .env loader for the MCP server — used by index.ts.
 *
 * Loads KEY=VALUE pairs from a ``.env`` file (inline, no dependency on
 * ``dotenv``).  Does **not** override variables already in process.env.
 */

import fs from "fs";
import path from "path";

export function loadEnv(envPath?: string): Record<string, string> {
  const filePath = envPath || path.resolve(__dirname, "..", ".env");
  const parsed: Record<string, string> = {};

  try {
    const content = fs.readFileSync(filePath, "utf-8");
    for (const raw of content.split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq === -1) continue;
      const key = line.slice(0, eq).trim();
      let val = line.slice(eq + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      if (key && !(key in process.env)) {
        process.env[key] = val;
        parsed[key] = val;
      }
    }
  } catch {
    // .env file missing or unreadable — use defaults
  }

  return parsed;
}

// ── Load .env at module evaluation time ─────────────────────────────
loadEnv();
