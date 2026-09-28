import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Only vitest unit tests live under src/. The files in tests/ are
    // self-contained HTTP integration scripts (custom assert() +
    // process.exit(1), no test() blocks) executed by
    // .github/workflows/service-test-gates.yml against a live server +
    // throwaway PostgreSQL. Letting vitest collect those scripts hangs at
    // worker teardown: their fetch connections are still open, no test
    // lifecycle ever completes, and vitest cannot terminate the fork.
    // This boundary keeps `vitest run` (and npm test) safe to run from the
    // package root while the integration suite keeps its own runner.
    include: ["src/**/*.test.ts"],
  },
});
