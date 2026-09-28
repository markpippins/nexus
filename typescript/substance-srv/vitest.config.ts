import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Never pick up stale compiled artifacts from dist/ — only source tests.
    exclude: ["**/node_modules/**", "**/dist/**"],
  },
});
