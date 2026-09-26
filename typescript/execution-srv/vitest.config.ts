import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Source tests only — the compiled dist/ tree must never be collected
    // (its CJS output crashes vitest's ESM import; found by CI).
    // src/routes.test.ts and src/metrics.test.ts are dependency-free tsx-driven
    // self-executing conformance scripts (`npm run test:witnessed-run`), not
    // vitest suites, so they are excluded here.
    include: ["src/__tests__/**/*.{test,spec}.?(c|m)[jt]s?(x)", "src/limiter.test.ts"],
  },
});
