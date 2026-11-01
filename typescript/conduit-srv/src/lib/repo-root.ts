/**
 * Locate the checkout that owns a file, instead of counting `../` and assuming
 * the repo directory is named `nexus`.
 *
 * This module exists because three session-log twins carried the same default —
 * `path.resolve(__dirname, "../../../../../nexus/audit/CONDUIT_DATA")` — which
 * is correct only when the checkout is literally named `nexus`. From an R8.0
 * worktree (`nexus-worktrees/<topic>`) it resolves to
 * `nexus-worktrees/nexus/audit/CONDUIT_DATA`, which does not exist, so the SSE
 * route streams nothing and reports no error.
 *
 * The file is duplicated verbatim in `adonisjs/nexus-control-edge/app/lib/`
 * (the two packages share no build). Keep them identical: each differing line
 * is a future drift, which is the failure mode this change exists to remove.
 *
 * Callers pass their own `__dirname`, so nothing here assumes a directory depth.
 */
import fs from "node:fs";
import path from "node:path";

/**
 * Nearest ancestor of `startDir` that is a checkout root — `.git` is a
 * directory in a clone and a *file* in a linked worktree; `existsSync` covers
 * both. Returns `undefined` when no ancestor qualifies (a packaged or
 * dist-only deployment).
 */
export function findRepoRoot(startDir: string): string | undefined {
  let dir = path.resolve(startDir);
  for (;;) {
    if (fs.existsSync(path.join(dir, ".git"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/**
 * Default `PIPELINE_DIR`: `audit/CONDUIT_DATA` of the checkout that owns
 * `startDir`. `PIPELINE_DIR` env override stays the caller's decision.
 *
 * The fallback is the pre-fix expression, kept verbatim so a deployment with no
 * checkout ancestor (a built image copied elsewhere) fails exactly where it used
 * to rather than silently resolving somewhere new.
 */
export function resolvePipelineDir(startDir: string): string {
  const root = findRepoRoot(startDir);
  if (root) return path.join(root, "audit", "CONDUIT_DATA");
  return path.resolve(startDir, "../../../../../nexus/audit/CONDUIT_DATA");
}
