/**
 * The repo-root helper is deliberately duplicated: `conduit-srv` and the
 * AdonisJS control edge share no build, so neither can import the other. (That
 * duplicating-everything instinct is exactly what produced the three session-log
 * twins, each with its own copy of the same broken default.)
 *
 * This suite is the other half of the deal: the copies may exist, but they may
 * never differ. Duplication with a CI-enforced equality check is a shared
 * implementation; duplication without one is drift waiting to happen.
 *
 * Only `conduit-srv` has a CI job (`ts-unit-tests.yml` matrix), so this lives
 * here and reaches across to the AdonisJS copy rather than the reverse.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { findRepoRoot } from "./lib/repo-root.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const ROOT = findRepoRoot(__dirname);
if (!ROOT) throw new Error(`no checkout ancestor above ${__dirname}`);

const CONDUIT_COPY = path.join(__dirname, "lib", "repo-root.ts");
const ADONIS_COPY = path.join(ROOT, "adonisjs", "nexus-control-edge", "app", "lib", "repo-root.ts");
const ADONIS_CONTROLLER = path.join(
  ROOT,
  "adonisjs",
  "nexus-control-edge",
  "app",
  "controllers",
  "conduit_controller.ts",
);

describe("repo-root helper copies", () => {
  it("are byte-identical in conduit-srv and the adonisjs control edge", () => {
    const conduit = readFileSync(CONDUIT_COPY, "utf8");
    const adonis = readFileSync(ADONIS_COPY, "utf8");

    // Diff the whole file, not a hash: the failure message has to say what
    // diverged, otherwise the next person just re-copies without learning why.
    expect(adonis).toBe(conduit);
  });

  it("route every caller through the shared resolver, not a directory count", () => {
    const controller = readFileSync(ADONIS_CONTROLLER, "utf8");
    expect(controller).toContain("resolvePipelineDir(__dirname)");
    // The pre-fix default, in any of its spellings. Directory counting is the
    // thing being retired, so its reintroduction should fail a test rather than
    // wait for someone to notice an empty log stream from a worktree.
    expect(controller).not.toMatch(/\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/nexus\/audit/);
  });
});
