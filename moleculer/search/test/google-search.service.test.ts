import { ServiceBroker } from "moleculer";
import GoogleSearchService, {
  __useTestCacheCollection,
  __resetCacheState,
} from "../services/google-search.service";
import axios from "axios";
import { testBrokerConfig } from "./moleculer.config";

jest.mock("axios");
const mockedAxios = axios as jest.Mocked<typeof axios>;

describe("GoogleSearchService", () => {
  let broker: ServiceBroker;
  let googleSearchService: GoogleSearchService;

  const originalEnv = process.env;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
    jest.clearAllMocks();
    // This suite proves LIVE-path behavior: cache fully disabled so no
    // test can accidentally pass via a cache hit. Cache behavior lives in
    // test/search-cache.test.ts (fake collections).
    __useTestCacheCollection(null);
  });

  afterEach(async () => {
    process.env = originalEnv;
    __resetCacheState();
    if (broker) {
      await broker.stop();
    }
  });

  describe("service initialization", () => {
    it("should have the correct service name", () => {
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      expect(googleSearchService.name).toBe("google-search");
    });

    it("should be unversioned (actions called as google-search.*)", () => {
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      // NOTE: the schema declares no `version`, so actions stay unversioned.
      // Adding one would namespace them (v1.google-search.*) and break the
      // api gateway whitelist — see api.service.ts. Assert the real contract.
      expect(googleSearchService.version).toBeUndefined();
    });
  });

  describe("simpleSearch action - parameter validation", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should require query parameter", async () => {
      await expect(broker.call("google-search.simpleSearch", {} as any)).rejects.toThrow();
    });

    it("should accept query as string", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [], searchInformation: { totalResults: "0", searchTime: 0.1 } }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "test query" });
      expect(result).toBeDefined();
    });

    it("should accept optional token parameter", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [], searchInformation: { totalResults: "0", searchTime: 0.1 } }
      });

      const result = await broker.call("google-search.simpleSearch", {
        query: "test query",
        token: "some-token"
      });
      expect(result).toBeDefined();
    });
  });

  describe("simpleSearch action - Google API call", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should call Google API with correct parameters", async () => {
      const mockResponse = {
        data: {
          items: [
            { title: "Test Result", link: "https://example.com", snippet: "Test snippet" }
          ],
          searchInformation: { totalResults: "1", searchTime: 0.1 }
        }
      };
      mockedAxios.get.mockResolvedValue(mockResponse);

      const result = await broker.call("google-search.simpleSearch", { query: "test query" }) as { items: Array<{title: string; link: string; snippet: string}> };

      expect(mockedAxios.get).toHaveBeenCalledWith(
        "https://www.googleapis.com/customsearch/v1",
        {
          params: {
            key: "test-api-key",
            cx: "test-engine-id",
            q: "test query"
          }
        }
      );

      expect(result.items).toHaveLength(1);
      expect(result.items[0]).toEqual({
        title: "Test Result",
        link: "https://example.com",
        snippet: "Test snippet",
        // No displayLink in the Google payload → hostname fallback (slice-1 G1).
        displayLink: "example.com",
      });
    });

    it("should handle empty results", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [], searchInformation: { totalResults: "0", searchTime: 0.1 } }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "test query" }) as { items: any[] };
      expect(result.items).toEqual([]);
    });

    it("should handle missing items in response", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { searchInformation: { totalResults: "0", searchTime: 0.1 } }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "test query" }) as { items: any[] };
      expect(result.items).toEqual([]);
    });
  });

  describe("simpleSearch action - error handling", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should handle API errors gracefully", async () => {
      mockedAxios.get.mockRejectedValue(new Error("Network error"));

      await expect(
        broker.call("google-search.simpleSearch", { query: "test query" })
      ).rejects.toThrow("Failed to perform search: Network error");
    });

    it("should handle 403 errors from Google API", async () => {
      const error = new Error("Request failed with status code 403");
      mockedAxios.get.mockRejectedValue(error);

      await expect(
        broker.call("google-search.simpleSearch", { query: "test query" })
      ).rejects.toThrow("Failed to perform search: Request failed with status code 403");
    });
  });

  describe("missing credentials", () => {
    it("should throw error when API key is missing", async () => {
      process.env.GOOGLE_API_KEY = "";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;

      await expect(
        googleSearchService.performSearch("test query")
      ).rejects.toThrow("Google API credentials not configured");
    });

    it("should throw error when search engine ID is missing", async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;

      await expect(
        googleSearchService.performSearch("test query")
      ).rejects.toThrow("Google API credentials not configured");
    });

    it("should throw error when both credentials are missing", async () => {
      process.env.GOOGLE_API_KEY = "";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;

      await expect(
        googleSearchService.performSearch("test query")
      ).rejects.toThrow("Google API credentials not configured");
    });
  });

  describe("health action", () => {
    beforeEach(async () => {
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should return correct health response", async () => {
      const result = await broker.call("google-search.health");
      expect(result).toEqual({
        status: "ok",
        service: "google-search"
      });
    });
  });

  describe("displayLink mapping (slice-1 G1)", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should pass through Google displayLink when present", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [{ title: "t", link: "https://www.example.com/x", snippet: "s", displayLink: "www.example.com" }] }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "q" }) as { items: Array<{ displayLink: string }> };
      expect(result.items[0].displayLink).toBe("www.example.com");
    });

    it("should fall back to the link hostname when displayLink is absent", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [{ title: "t", link: "https://sub.example.org/path?q=1", snippet: "s" }] }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "q" }) as { items: Array<{ displayLink: string }> };
      expect(result.items[0].displayLink).toBe("sub.example.org");
    });

    it("should fall back to the link itself when the URL is malformed", async () => {
      mockedAxios.get.mockResolvedValue({
        data: { items: [{ title: "t", link: "not-a-url", snippet: "s" }] }
      });

      const result = await broker.call("google-search.simpleSearch", { query: "q" }) as { items: Array<{ displayLink: string }> };
      expect(result.items[0].displayLink).toBe("not-a-url");
    });
  });

  describe("token echo (slice-1 D4)", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should echo the caller token in the response when supplied", async () => {
      mockedAxios.get.mockResolvedValue({ data: { items: [] } });

      const result = await broker.call("google-search.simpleSearch", {
        query: "q",
        token: "caller-token-123"
      }) as { token?: string };
      expect(result.token).toBe("caller-token-123");
    });

    it("should omit the token field when none is supplied", async () => {
      mockedAxios.get.mockResolvedValue({ data: { items: [] } });

      const result = await broker.call("google-search.simpleSearch", { query: "q" }) as { token?: string };
      expect(result.token).toBeUndefined();
    });

    it("should accept planned page/size params but ignore them (D5)", async () => {
      mockedAxios.get.mockResolvedValue({ data: { items: [] } });

      const result = await broker.call("google-search.simpleSearch", {
        query: "q",
        page: 2,
        size: 20,
      }) as { items: unknown[] };
      expect(result.items).toEqual([]);
      // No start/count forwarded to Google: contract documents page/size
      // as PLANNED, implementation must not pretend otherwise.
      expect(mockedAxios.get).toHaveBeenCalledWith(
        "https://www.googleapis.com/customsearch/v1",
        { params: { key: "test-api-key", cx: "test-engine-id", q: "q" } }
      );
    });
  });

  describe("typed errors (slice-1 D3)", () => {
    beforeEach(async () => {
      process.env.GOOGLE_API_KEY = "test-api-key";
      process.env.GOOGLE_SEARCH_ENGINE_ID = "test-engine-id";
      broker = new ServiceBroker(testBrokerConfig);
      googleSearchService = broker.createService(GoogleSearchService) as GoogleSearchService;
      await broker.start();
    });

    it("should throw SEARCH_CREDENTIALS (no retry) when keys are missing", async () => {
      process.env.GOOGLE_API_KEY = "";
      const svcBroker = new ServiceBroker(testBrokerConfig);
      svcBroker.createService(GoogleSearchService);
      await svcBroker.start();
      try {
        const err: any = await svcBroker.call("google-search.simpleSearch", { query: "q" }).catch((e: any) => e);
        expect(err.data).toMatchObject({ code: "SEARCH_CREDENTIALS", retryable: false, provider: "google" });
      } finally {
        await svcBroker.stop();
      }
    });

    it("should throw retryable SEARCH_PROVIDER on Google 500", async () => {
      const e500: any = new Error("Request failed with status code 500");
      e500.response = { status: 500 };
      mockedAxios.get.mockRejectedValue(e500);

      const err: any = await broker.call("google-search.simpleSearch", { query: "q" }).catch((e: any) => e);
      expect(err.data).toMatchObject({ code: "SEARCH_PROVIDER", retryable: true, status: 500 });
    });

    it("should throw non-retryable SEARCH_PROVIDER on Google 400", async () => {
      const e400: any = new Error("Request failed with status code 400");
      e400.response = { status: 400 };
      mockedAxios.get.mockRejectedValue(e400);

      const err: any = await broker.call("google-search.simpleSearch", { query: "q" }).catch((e: any) => e);
      expect(err.data).toMatchObject({ code: "SEARCH_PROVIDER", retryable: false, status: 400 });
    });

    it("should throw retryable SEARCH_PROVIDER on network failure", async () => {
      mockedAxios.get.mockRejectedValue(new Error("socket hang up"));

      const err: any = await broker.call("google-search.simpleSearch", { query: "q" }).catch((e: any) => e);
      expect(err.data).toMatchObject({ code: "SEARCH_PROVIDER", retryable: true });
    });

    it("should reject missing query with a ValidationError", async () => {
      const err: any = await broker.call("google-search.simpleSearch", {} as any).catch((e: any) => e);
      expect(err.name).toBe("ValidationError");
    });
  });
});
