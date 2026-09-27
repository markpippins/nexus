import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Source tests only — the compiled dist/ tree must never be collected.
    include: ["src/**/*.{test,spec}.?(c|m)[jt]s?(x)"],
  },
});
