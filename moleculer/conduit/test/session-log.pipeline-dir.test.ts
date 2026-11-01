/**
 * PIPELINE_DIR resolution for the conduit twin's session-log route.
 *
 * Locks the defect reported in the path-coupling audit (agent record f9a6e738,
 * Class B): the default walked six levels up from `services/routes` and then
 * named the repo `nexus/`. Six is right for `dist/services/routes`, one too
 * many for the source layout that `moleculer-runner services/**\/*.service.ts`
 * actually loads — so the default missed in the main checkout, and from a
 * worktree it resolved to the *main* checkout's audit data instead of its own.
 *
 * The assertions are existential where they can be: each case checks that the
 * resolved directory is the one that owns the file, not just that the string
 * has the right shape.
 */
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { findRepoRoot, resolvePipelineDir } from "../services/routes/session-log";

const tempDirs: string[] = [];

function tempDir(prefix: string): string {
  const d = mkdtempSync(path.join(tmpdir(), prefix));
  tempDirs.push(d);
  return d;
}

/** A checkout-shaped tree: `.git` (as a linked worktree has it — a file),
 *  `audit/CONDUIT_DATA`, and the service's own directory chain. */
function makeWorktreeCheckout(parent: string, name: string): string {
  const root = path.join(parent, "some-worktree-root", name);
  mkdirSync(path.join(root, "audit", "CONDUIT_DATA"), { recursive: true });
  writeFileSync(
    path.join(root, ".git"),
    "gitdir: /home/codex/dev/nexus/.git/worktrees/example\n",
  );
  mkdirSync(path.join(root, "moleculer", "conduit", "services", "routes"), {
    recursive: true,
  });
  return root;
}

describe("session-log PIPELINE_DIR", () => {
  test("resolves the data dir of the checkout that owns the file", () => {
    const parent = tempDir("mc-parent-");
    const root = makeWorktreeCheckout(parent, "some-topic");
    const startDir = path.join(root, "moleculer", "conduit", "services", "routes");

    // The pre-fix expression, for the record: from a worktree it lands on the
    // main checkout's data (or, in this sandbox, nowhere).
    const legacy = path.resolve(
      startDir,
      "../../../../../../nexus/audit/CONDUIT_DATA",
    );
    expect(legacy.startsWith(root)).toBe(false);

    expect(resolvePipelineDir(startDir)).toBe(
      path.join(root, "audit", "CONDUIT_DATA"),
    );
    expect(existsSync(resolvePipelineDir(startDir))).toBe(true);
  });

  test("works for this checkout, and that dir is the one this file sits in", () => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const startDir = path.join(__dirname, "..", "services", "routes");

    const resolved = resolvePipelineDir(startDir);
    expect(findRepoRoot(startDir)).toBe(repoRoot);
    expect(resolved).toBe(path.join(repoRoot, "audit", "CONDUIT_DATA"));
    // Ownership, not existence: `audit/CONDUIT_DATA` is not tracked, so a fresh
    // worktree legitimately has no sessions dir under it. The property that
    // broke was "which checkout does this resolve inside", so assert that.
    expect(resolved.startsWith(repoRoot + path.sep)).toBe(true);
  });

  test("a clone's `.git` directory is a checkout root too", () => {
    const parent = tempDir("mc-clone-");
    const root = path.join(parent, "renamed-checkout");
    mkdirSync(path.join(root, ".git"), { recursive: true });
    mkdirSync(path.join(root, "audit", "CONDUIT_DATA"), { recursive: true });
    const startDir = path.join(root, "moleculer", "conduit", "services", "routes");
    mkdirSync(startDir, { recursive: true });

    expect(resolvePipelineDir(startDir)).toBe(
      path.join(root, "audit", "CONDUIT_DATA"),
    );
    expect(resolvePipelineDir(startDir)).not.toContain("nexus");
  });

  test("keeps the legacy expression when no ancestor is a checkout", () => {
    const root = tempDir("mc-bare-");
    const startDir = path.join(root, "no", "git", "here");
    mkdirSync(startDir, { recursive: true });

    expect(findRepoRoot(startDir)).toBeUndefined();
    expect(resolvePipelineDir(startDir)).toBe(
      path.resolve(startDir, "../../../../../../nexus/audit/CONDUIT_DATA"),
    );
  });
});
