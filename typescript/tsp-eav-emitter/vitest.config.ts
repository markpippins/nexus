import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Source tests only — the compiled dist/ tree must never be collected
    // (house lesson from execution-srv: compiled CJS output crashes vitest's
    // ESM import).
    include: ["src/__tests__/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
    // The real-`tsp` e2e suite is guarded in-code (explicit skip when the
    // compiler CLI is absent, e.g. a bare CI matrix shard) — timeouts keep
    // a wedged npx from hanging the job.
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
