/**
 * config tests — port of the TestConfig class in
 * python/substance/tests/test_substance.py.
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_POSTGRES_DSN,
  DEFAULT_REDIS_TTL_SECONDS,
  DEFAULT_REDIS_URL,
  getSettings,
  resetSettings,
  settingsFromEnv,
} from "./config";

describe("settingsFromEnv", () => {
  it("falls back to the documented defaults when nothing is set", () => {
    const s = settingsFromEnv({});
    expect(s.postgresDsn).toBe(DEFAULT_POSTGRES_DSN);
    expect(s.postgresDsn).toContain("postgresql://");
    expect(s.redisUrl).toBe(DEFAULT_REDIS_URL);
    expect(s.redisUrl).toContain("redis://");
    expect(Number.isInteger(s.redisTtlSeconds)).toBe(true);
    expect(s.redisTtlSeconds).toBeGreaterThan(0);
  });

  it("defaults the safety-net TTL to 3600s (1 hour)", () => {
    expect(settingsFromEnv({}).redisTtlSeconds).toBe(3600);
    expect(DEFAULT_REDIS_TTL_SECONDS).toBe(3600);
  });

  it("reads every documented env var", () => {
    const s = settingsFromEnv({
      NEBULA_PG_DSN: "postgresql://u:p@db:5432/nexus",
      NEBULA_REDIS_URL: "redis://cache:6379/3",
      NEBULA_SEGSET_CACHE_TTL: "60",
    });
    expect(s.postgresDsn).toBe("postgresql://u:p@db:5432/nexus");
    expect(s.redisUrl).toBe("redis://cache:6379/3");
    expect(s.redisTtlSeconds).toBe(60);
  });

  it("throws on a non-numeric TTL rather than silently disabling the safety net", () => {
    // Python's int(os.environ.get(...)) raised ValueError on garbage; a
    // misconfigured TTL must not quietly become NaN and disable invalidation.
    expect(() => settingsFromEnv({ NEBULA_SEGSET_CACHE_TTL: "soon" })).toThrow(
      /NEBULA_SEGSET_CACHE_TTL/,
    );
  });

  it("throws on a fractional TTL", () => {
    expect(() => settingsFromEnv({ NEBULA_SEGSET_CACHE_TTL: "1.5" })).toThrow(
      /NEBULA_SEGSET_CACHE_TTL/,
    );
  });
});

describe("getSettings", () => {
  it("is a singleton (Python's @lru_cache)", () => {
    resetSettings();
    expect(getSettings()).toBe(getSettings());
  });

  it("re-reads the environment after reset", () => {
    resetSettings();
    process.env.NEBULA_SEGSET_CACHE_TTL = "77";
    try {
      expect(getSettings().redisTtlSeconds).toBe(77);
    } finally {
      delete process.env.NEBULA_SEGSET_CACHE_TTL;
      resetSettings();
    }
  });
});
