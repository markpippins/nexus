/**
 * PIPELINE_DIR resolution for the session-log route (src/lib/repo-root.ts).
 *
 * Locks the defect reported in the path-coupling audit (agent record f9a6e738,
 * Class A site 5): the default walked five levels up from `src/routes` and then
 * named the repo `nexus/`, so from an R8.0 worktree it resolved to
 * `nexus-worktrees/nexus/audit/CONDUIT_DATA` — nonexistent — and the SSE route
 * streamed nothing while reporting no error.
 *
 * The sibling copy of this module lives in `adonisjs/nexus-control-edge/app/lib/`
 * and is byte-identical. That package's unit suite cannot currently run (`node ace
 * test` fails at boot: undeclared `@poppinss/ts-exec`, then `start/env.ts`
 * validation), so the byte-identity case below is what carries it - not a suite
 * there. Do not read this as coverage existing on both sides.
 *
 * Pure and DB-free.
 */
import {
  mkdirSync,
  mkdtempSync,
  writeFileSync,
  existsSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";

import { findRepoRoot, resolvePipelineDir } from "./lib/repo-root.js";

/** ESM: no __dirname in this package (`"type": "module"`). */
const HERE = path.dirname(fileURLToPath(import.meta.url));

const tempDirs: string[] = [];

function tempDir(prefix: string): string {
  const d = mkdtempSync(path.join(tmpdir(), prefix));
  tempDirs.push(d);
  return d;
}

afterAll(() => {
  for (const d of tempDirs) rmSync(d, { recursive: true, force: true });
});

describe("resolvePipelineDir", () => {
  it("resolves the checkout that owns the route, not a sibling named nexus", () => {
    const parent = tempDir("cs-parent-");
    const root = path.join(parent, "nexus-worktrees", "some-topic");
    // A linked worktree: `.git` is a file, and it has an untracked data mirror.
    mkdirSync(path.join(root, "audit", "CONDUIT_DATA"), { recursive: true });
    writeFileSync(path.join(root, ".git"), "gitdir: /x/.git/worktrees/y\n");
    const startDir = path.join(root, "typescript", "conduit-srv", "src", "routes");
    mkdirSync(startDir, { recursive: true });

    // The pre-fix expression, for the record.
    const legacy = path.resolve(startDir, "../../../../../nexus/audit/CONDUIT_DATA");
    expect(existsSync(legacy)).toBe(false);

    expect(resolvePipelineDir(startDir)).toBe(
      path.join(root, "audit", "CONDUIT_DATA"),
    );
    expect(existsSync(resolvePipelineDir(startDir))).toBe(true);
  });

  it("resolves this checkout, and that dir is the one this package sits in", () => {
    const repoRoot = path.resolve(HERE, "..", "..", "..");
    const startDir = path.join(HERE, "routes");

    expect(findRepoRoot(startDir)).toBe(repoRoot);
    const resolved = resolvePipelineDir(startDir);
    expect(resolved).toBe(path.join(repoRoot, "audit", "CONDUIT_DATA"));
    // Ownership, not existence: `audit/CONDUIT_DATA` is untracked, so a fresh
    // worktree legitimately has none. The property that broke is which checkout
    // the path resolves inside.
    expect(resolved.startsWith(repoRoot + path.sep)).toBe(true);
  });

  it("a clone's `.git` directory is a checkout root too", () => {
    const parent = tempDir("cs-clone-");
    const root = path.join(parent, "renamed-checkout");
    mkdirSync(path.join(root, ".git"), { recursive: true });
    mkdirSync(path.join(root, "audit", "CONDUIT_DATA"), { recursive: true });
    const startDir = path.join(root, "typescript", "conduit-srv", "src", "routes");
    mkdirSync(startDir, { recursive: true });

    expect(resolvePipelineDir(startDir)).toBe(
      path.join(root, "audit", "CONDUIT_DATA"),
    );
    expect(resolvePipelineDir(startDir)).not.toContain("nexus");
  });

  it("stays byte-identical to the adonis edge's copy", () => {
    // The adonis edge (`node ace test`) cannot be run in this environment — its
    // installed tree is missing @poppinss/ts-exec and no CI workflow runs that
    // suite — so its copy is not covered by a runnable test of its own. Byte
    // identity is what makes that acceptable: the behaviour proven above is
    // literally the same code, and any drift between the two twins fails here.
    const own = path.resolve(HERE, "lib", "repo-root.ts");
    const sibling = path.resolve(
      HERE,
      "..",
      "..",
      "..",
      "adonisjs",
      "nexus-control-edge",
      "app",
      "lib",
      "repo-root.ts",
    );
    expect(existsSync(sibling)).toBe(true);
    expect(readFileSync(sibling, "utf8")).toBe(readFileSync(own, "utf8"));
  });

  it("keeps the pre-fix expression when no ancestor is a checkout", () => {
    const root = tempDir("cs-bare-");
    const startDir = path.join(root, "built", "routes");
    mkdirSync(startDir, { recursive: true });

    expect(findRepoRoot(startDir)).toBeUndefined();
    expect(resolvePipelineDir(startDir)).toBe(
      path.resolve(startDir, "../../../../../nexus/audit/CONDUIT_DATA"),
    );
  });
});
