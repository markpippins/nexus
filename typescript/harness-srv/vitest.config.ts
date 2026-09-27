import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Source tests only — the compiled dist/ tree must never be collected
    // (its CJS output crashes vitest's ESM import). tests/*.test.ts are
    // dependency-free tsx-driven scripts run by `npm run test:unit`; they
    // are not vitest suites and would fail collection here.
    include: ["src/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
  },
});
