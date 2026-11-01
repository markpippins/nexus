/**
 * PYTHONPATH resolution for the CIR-SDM CLI bridge (cirsdm.ts).
 *
 * Locks the defect reported in the path-coupling audit (agent record
 * f9a6e738): the default walked four levels up from `src/` and then named the
 * repo `nexus/`, so from a linked worktree (`nexus-worktrees/<topic>`) it
 * resolved to `nexus-worktrees/nexus/python` — a path that does not exist.
 * Every CIR-SDM call then died with ModuleNotFoundError and gateWrTransition
 * failed CLOSED, holding every WR transition while reporting "enforcement gate
 * unreachable".
 *
 * The assertions are existential, not string-shaped: each case asserts the
 * resolved directory actually contains the module the subprocess imports. The
 * string form is an implementation detail; "the path exists and owns
 * nexus_core" is the property that broke.
 *
 * Pure and DB-free.
 */
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { findRepoRoot, resolvePythonPath } from "./cirsdm";

const CLI_REL = path.join("python", "nexus_core", "wrp", "enforce_cli.py");

/** Build a checkout-shaped tree (python/nexus_core/wrp/enforce_cli.py plus a
 *  conduit-mcp src dir `depth` levels down) and return that src dir. */
function makeCheckout(root: string, depth = 4): string {
  const cli = path.join(root, CLI_REL);
  mkdirSync(path.dirname(cli), { recursive: true });
  writeFileSync(cli, "# stub enforcement CLI\n");
  const srcDir = path.join(
    root,
    ...Array.from({ length: depth - 1 }, (_, i) => `level${i + 1}`),
  );
  mkdirSync(srcDir, { recursive: true });
  return srcDir;
}

const tempDirs: string[] = [];
function tempDir(prefix: string): string {
  const d = mkdtempSync(path.join(tmpdir(), prefix));
  tempDirs.push(d);
  return d;
}

afterEach(() => {
  // temp dirs are cheap and isolated; nothing to unwind but the cache is
  // module-scoped, so do not assert on it across cases.
});

describe("CIR-SDM PYTHONPATH — worktree layout", () => {
  it("resolves the checkout that owns the source, not a sibling named nexus", () => {
    // <parent>/nexus-worktrees/<topic>/typescript/conduit-mcp/src — the exact
    // shape R8.0 worktrees produce, where <parent> contains no `nexus/` dir.
    const parent = tempDir("wr-parent-");
    const root = path.join(parent, "nexus-worktrees", "some-topic");
    const cli = path.join(root, CLI_REL);
    mkdirSync(path.dirname(cli), { recursive: true });
    writeFileSync(cli, "# stub\n");
    const srcDir = path.join(root, "typescript", "conduit-mcp", "src");
    mkdirSync(srcDir, { recursive: true });

    // The pre-fix expression, for the record: this is what used to be used.
    const legacy = path.resolve(srcDir, "../../../../nexus/python");
    expect(existsSync(legacy)).toBe(false);

    const resolved = resolvePythonPath(srcDir);
    expect(resolved).toBe(path.join(root, "python"));
    expect(existsSync(path.join(resolved, "nexus_core", "wrp", "enforce_cli.py"))).toBe(
      true,
    );
  });

  it("finds the root from a src dir many levels down (depth-independent)", () => {
    const root = path.join(tempDir("deep-"), "checkout");
    const srcDir = makeCheckout(root, 7);
    expect(resolvePythonPath(srcDir)).toBe(path.join(root, "python"));
  });

  it("does not depend on the checkout being named 'nexus'", () => {
    const root = path.join(tempDir("renamed-"), "my-fork-of-the-thing");
    const srcDir = makeCheckout(root);
    const resolved = resolvePythonPath(srcDir);
    expect(resolved).toBe(path.join(root, "python"));
    expect(resolved).not.toContain("nexus");
  });
});

describe("CIR-SDM PYTHONPATH — this checkout", () => {
  it("resolves to this repo's python/ and the CLI is importable from there", () => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const resolved = resolvePythonPath(__dirname);
    expect(resolved).toBe(path.join(repoRoot, "python"));
    expect(existsSync(path.join(resolved, "nexus_core", "wrp", "enforce_cli.py"))).toBe(
      true,
    );
    // ...and the resolved root is the one that owns this file, i.e. a worktree
    // resolves to itself rather than to the main checkout next door.
    expect(findRepoRoot(__dirname)).toBe(repoRoot);
  });
});

describe("CIR-SDM PYTHONPATH — no owning checkout", () => {
  it("keeps the legacy fallback when no ancestor has a python tree", () => {
    const root = tempDir("bare-");
    const srcDir = path.join(root, "nowhere", "src");
    mkdirSync(srcDir, { recursive: true });
    expect(findRepoRoot(srcDir)).toBeUndefined();
    expect(resolvePythonPath(srcDir)).toBe(
      path.resolve(srcDir, "../../../../nexus/python"),
    );
  });
});
