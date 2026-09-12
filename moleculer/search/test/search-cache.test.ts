import { ServiceBroker } from "moleculer";
import GoogleSearchService, {
  __useTestCacheCollection,
  __resetCacheState,
  normalizeSearchQuery,
  isCacheRowValid,
} from "../services/google-search.service";
import axios from "axios";
import { testBrokerConfig } from "./moleculer.config";

jest.mock("axios");
const mockedAxios = axios as jest.Mocked<typeof axios>;

// Slice-2 phase 1: shared-cache READS. Mongo is faked at the collection
// boundary (no live DB in tests); the fake implements only findOne.
describe("search cache reads", () => {
  let broker: ServiceBroker;
  let findOne: jest.Mock;

  const originalEnv = process.env;

  const googlePayload = {
    data: {
      items: [{ title: "Live", link: "https://live.example/x", snippet: "S" }],
      searchInformation: { totalResults: "1", searchTime: 0.1 },
    },
  };

  function cachedRow(query: string, ageMinutes: number, withItems = true) {
    return {
      query,
      items: withItems
        ? [{ title: "Cached", link: "https://cached.example/x", snippet: "C", displayLink: "cached.example" }]
        : [],
      timestamp: new Date(Date.now() - ageMinutes * 60000),
      expiresAt: new Date(Date.now() + (30 - ageMinutes) * 60000),
    };
  }

  beforeEach(async () => {
    jest.clearAllMocks();
    process.env = {
      ...originalEnv,
      GOOGLE_API_KEY: "test-api-key",
      GOOGLE_SEARCH_ENGINE_ID: "test-engine-id",
    };
    findOne = jest.fn().mockResolvedValue(null);
    __useTestCacheCollection({ findOne } as any);
    broker = new ServiceBroker(testBrokerConfig);
    broker.createService(GoogleSearchService);
    await broker.start();
  });

  afterEach(async () => {
    process.env = originalEnv;
    __resetCacheState();
    if (broker) {
      await broker.stop();
    }
  });

  it("serves a fresh normalized hit without calling Google", async () => {
    findOne.mockResolvedValue(cachedRow("angular signals", 5));

    const result = (await broker.call("google-search.simpleSearch", {
      query: "  Angular   SIGNALS ",
    })) as any;

    // Normalized lookup key observed…
    expect(findOne).toHaveBeenCalledWith({ query: "angular signals" });
    expect(mockedAxios.get).not.toHaveBeenCalled();
    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toEqual({
      title: "Cached",
      link: "https://cached.example/x",
      snippet: "C",
      displayLink: "cached.example",
    });
    // …and no provider metadata on cache hits (mirrors legacy buildResult).
    expect(result.searchInformation).toBeUndefined();
  });

  it("falls back to the raw query when normalized misses (lazy migration)", async () => {
    findOne.mockImplementation(async (filter: any) =>
      filter.query === "Angular Signals" ? cachedRow("Angular Signals", 5) : null
    );

    const result = (await broker.call("google-search.simpleSearch", {
      query: "Angular Signals",
    })) as any;

    expect(findOne).toHaveBeenNthCalledWith(1, { query: "angular signals" });
    expect(findOne).toHaveBeenNthCalledWith(2, { query: "Angular Signals" });
    expect(mockedAxios.get).not.toHaveBeenCalled();
    expect(result.items).toHaveLength(1);
  });

  it("treats expired rows as misses and goes live", async () => {
    findOne.mockResolvedValue(cachedRow("q", 60)); // expiresAt in the past
    mockedAxios.get.mockResolvedValue(googlePayload);

    const result = (await broker.call("google-search.simpleSearch", { query: "q" })) as any;

    expect(mockedAxios.get).toHaveBeenCalled();
    expect(result.items[0].title).toBe("Live");
    expect(result.searchInformation).toBeDefined();
  });

  it("fails open to live search when Mongo throws", async () => {
    findOne.mockRejectedValue(new Error("topology closed"));
    mockedAxios.get.mockResolvedValue(googlePayload);

    const result = (await broker.call("google-search.simpleSearch", { query: "q" })) as any;

    expect(result.items[0].title).toBe("Live");
  });

  it("serves cache hits even without credentials (resilience)", async () => {
    process.env.GOOGLE_API_KEY = "";
    findOne.mockResolvedValue(cachedRow("q", 5));

    // Fresh broker so the service constructs with empty creds…
    await broker.stop();
    broker = new ServiceBroker(testBrokerConfig);
    broker.createService(GoogleSearchService);
    await broker.start();

    const result = (await broker.call("google-search.simpleSearch", { query: "q" })) as any;

    expect(result.items[0].title).toBe("Cached");
    expect(mockedAxios.get).not.toHaveBeenCalled();
  });

  it("forceSearch bypasses the cache entirely", async () => {
    findOne.mockResolvedValue(cachedRow("q", 5));
    mockedAxios.get.mockResolvedValue(googlePayload);

    const result = (await broker.call("google-search.forceSearch", { query: "q" })) as any;

    expect(findOne).not.toHaveBeenCalled();
    expect(mockedAxios.get).toHaveBeenCalled();
    expect(result.items[0].title).toBe("Live");
  });

  it("normalizeSearchQuery mirrors the broker normalizer", () => {
    expect(normalizeSearchQuery("  Angular   SIGNALS ")).toBe("angular signals");
    expect(normalizeSearchQuery("a\tb\nc")).toBe("a b c");
    expect(normalizeSearchQuery("")).toBe("");
  });

  it("isCacheRowValid mirrors legacy isExpired", () => {
    expect(isCacheRowValid(null)).toBe(false);
    expect(isCacheRowValid(undefined)).toBe(false);
    // Absent expiresAt = valid forever (legacy: expired only when set+past).
    expect(isCacheRowValid({ query: "q" })).toBe(true);
    expect(isCacheRowValid({ query: "q", expiresAt: new Date(Date.now() + 60000) })).toBe(true);
    expect(isCacheRowValid({ query: "q", expiresAt: new Date(Date.now() - 60000) })).toBe(false);
  });
});
