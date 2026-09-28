/**
 * End-to-end: the REAL TypeSpec compiler driving the emitter, over the
 * shipped sample files. This is the surface #591 actually shipped —
 * `tsp compile` against the sample tspconfigs — so the suite exercises
 * walker + decorator binding + emit through the compiler itself.
 *
 * Contract pinned here (per #591's design):
 *  - main.tsp compiles CLEAN (exit 0) and emits a migration whose content
 *    is byte-identical to the committed evidence file — the e2e doubles
 *    as a drift guard between compiler output and the reviewed artifact.
 *  - violation-depth.tsp ALSO compiles clean — BY DESIGN. TypeSpec permits
 *    the 5-level chain; the shrapnel DB refuses it at COMMIT (deferred
 *    depth trigger). The oracle is the database, not the compiler: the
 *    emitter deliberately emits, and the committed evidence file documents
 *    exactly what the DB will reject.
 *
 * Hermetic: no DB. The scrap-DB COMMIT-rejection itself was the one-time
 * prototype proof in #591; re-running it here would need the disposable
 * container, which is out of scope for a unit-test shard.
 *
 * Skips explicitly (never silently fails) when `tsp` is unavailable —
 * e.g. a CI matrix shard without the compiler toolchain. The unit suites
 * carry the hermetic coverage in that case.
 */
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { execFile } from "node:child_process";
import { readFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SAMPLE_DIR = path.join(PKG_ROOT, "sample");

const SKIP_REASON =
  "explicit skip: `tsp` CLI not available on this machine/runner — " +
  "run `npm install` in typescript/tsp-eav-emitter (pulls @typespec/compiler) " +
  "or use the unit suites for hermetic coverage";

let tspAvailable = false;
try {
  await new Promise<void>((resolve, _reject) => {
    execFile("npx", ["--no-install", "tsp", "--version"], { cwd: PKG_ROOT, timeout: 30_000 }, (err) => {
      tspAvailable = !err;
      resolve();
    });
  });
} catch {
  tspAvailable = false;
}

function tspCompile(mainFile: string, configPath: string): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    // Main file must be the .tsp (a directory/yaml main is an `invalid-main`
    // compiler error); the config rides on --config. The emitter resolves
    // through the package's own node_modules (dist build required — the
    // `typespec` export points at dist/src/index.js); --no-install keeps
    // the probe honest (never silently downloads the toolchain).
    execFile(
      "npx",
      ["--no-install", "tsp", "compile", mainFile, "--config", configPath],
      { cwd: SAMPLE_DIR, timeout: 120_000, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout, stderr) => {
        const code = err ? 1 : 0;
        resolve({ code, stdout, stderr });
      },
    );
  });
}

/** The compiler's output location has moved across versions (cwd-relative
 *  vs project-root-relative, with or without a tsp-output/ prefix); accept
 *  any of the observed shapes but REQUIRE exactly one to exist. */
async function findEmitted(outputFile: string): Promise<string> {
  const candidates = [
    path.join(SAMPLE_DIR, outputFile),
    path.join(SAMPLE_DIR, "tsp-output", outputFile),
    path.join(PKG_ROOT, outputFile),
    path.join(PKG_ROOT, "tsp-output", outputFile),
  ];
  const hits = candidates.filter((p) => existsSync(p));
  expect(
    hits.length,
    `expected the emitted file at exactly one of:\n${candidates.join("\n")}`,
  ).toBeGreaterThan(0);
  return hits[0];
}

describe.runIf(tspAvailable)("tsp compile e2e (shipped samples)", () => {
  let buildReady = true;
  beforeAll(async () => {
    // The `typespec` export condition points at dist/src/index.js, so the
    // self-import needs a build. `npm test` builds via the pretest hook;
    // a direct `npx vitest run` on a fresh tree does not — build here,
    // and skip EXPLICITLY (with reason) if that fails.
    if (existsSync(path.join(PKG_ROOT, "dist", "src", "index.js"))) return;
    await new Promise<void>((resolve) => {
      execFile("npm", ["run", "build"], { cwd: PKG_ROOT, timeout: 120_000 }, (err) => {
        buildReady = !err;
        resolve();
      });
    });
    if (!buildReady || !existsSync(path.join(PKG_ROOT, "dist", "src", "index.js"))) {
      buildReady = false;
    }
  });

  beforeEach(() => {
    if (!buildReady) {
      throw new Error(
        "explicit skip: dist/ build unavailable — `npm run build` failed in this environment",
      );
    }
  });

  it("compiles main.tsp clean; emitted SQL is byte-identical to the committed evidence", async () => {
    const out = await tspCompile(
      path.join(SAMPLE_DIR, "main.tsp"),
      path.join(SAMPLE_DIR, "tspconfig.yaml"),
    );
    expect(out.code, `tsp compile failed:\n${out.stdout}\n${out.stderr}`).toBe(0);

    // Regeneration lands in sample/ (cwd-relative); the tracked evidence
    // lives at package root. Compare, then remove the regeneration so a
    // test run never leaves an untracked dropping next to the samples.
    const emittedPath = await findEmitted("shrapnel-catalog.sql");
    const emitted = await readFile(emittedPath, "utf8");

    // The reviewed, committed evidence from #591:
    const evidence = await readFile(path.join(PKG_ROOT, "shrapnel-catalog.sql"), "utf8");
    expect(emitted).toBe(evidence); // drift guard

    // And the substance, independent of the evidence file's health:
    expect(emitted).toContain("stereotype_create_revision('Person'");
    expect(emitted).toContain("stereotype_create_revision('VerifiedPerson'");
    expect(emitted).toContain("stereotype_create_revision('Credential'");
    expect(emitted).toContain("shrapnel.stereotype_resolve('Person')");
    expect(emitted).toContain("stereotype_instance_storage");
    expect(emitted).toContain("'CredentialRecord'");

    await rm(emittedPath, { force: true }); // no test droppings
  }, 150_000);

  it("compiles violation-depth.tsp clean BY DESIGN — the DB, not the compiler, refuses depth", async () => {
    const out = await tspCompile(
      path.join(SAMPLE_DIR, "violation-depth.tsp"),
      path.join(SAMPLE_DIR, "tspconfig-violation.yaml"),
    );
    expect(out.code, `unexpected compile refusal:\n${out.stdout}\n${out.stderr}`).toBe(0);

    const emittedPath = await findEmitted("shrapnel-catalog-violation.sql");
    const emitted = await readFile(emittedPath, "utf8");
    const evidence = await readFile(
      path.join(PKG_ROOT, "shrapnel-catalog-violation.sql"),
      "utf8",
    );
    expect(emitted).toBe(evidence); // drift guard

    // The full illegal chain is emitted for the DB to reject at COMMIT:
    for (const name of ["Alpha", "Beta", "Gamma", "Delta", "Echo"]) {
      expect(emitted).toContain(`stereotype_create_revision('${name}'`);
    }

    await rm(emittedPath, { force: true }); // no test droppings
  }, 150_000);
});

describe("environment guard", () => {
  it("reports the skip reason explicitly when tsp is absent", () => {
    // Documents the guard: when tspAvailable is false, the suite above is
    // skipped with SKIP_REASON — visible in vitest output, never silent.
    if (!tspAvailable) {
      expect(SKIP_REASON).toContain("explicit skip");
    }
  });
});
