import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    setupFiles: ["./src/schema-test-setup.ts"],
    testTimeout: 30000,
  },
});
