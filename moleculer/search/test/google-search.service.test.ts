import { ServiceBroker } from "moleculer";
import GoogleSearchService from "../services/google-search.service";
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
  });

  afterEach(async () => {
    process.env = originalEnv;
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
        snippet: "Test snippet"
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
});
