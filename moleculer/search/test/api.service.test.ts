import { ServiceBroker } from "moleculer";
import ApiService from "../services/api.service";
import { testBrokerConfig } from "./moleculer.config";

describe("ApiService", () => {
  let broker: ServiceBroker;
  let apiService: ApiService;

  // NOTE: config/shape tests run WITHOUT broker.start(). Starting the broker
  // boots the moleculer-web gateway (HTTP bind) which hangs the suite when
  // the default port is taken. The health block below starts its own broker
  // on a collision-free test port.
  beforeEach(() => {
    // Keep the gateway off the live :4050 while tests construct the service.
    process.env.SERVICE_PORT = "45981";
    broker = new ServiceBroker(testBrokerConfig);
    apiService = broker.createService(ApiService) as ApiService;
  });

  afterEach(async () => {
    if (broker) {
      await broker.stop();
    }
  });

  describe("service configuration", () => {
    it("should have the correct service name", () => {
      expect(apiService.name).toBe("api");
    });

    it("should be initialized with ApiGateway mixin", () => {
      expect(apiService.settings).toBeDefined();
      expect(Array.isArray(apiService.settings.routes)).toBe(true);
    });
  });

  describe("settings", () => {
    it("should have routes configured", () => {
      const routes = apiService.settings.routes;
      expect(routes).toBeDefined();
      expect(routes.length).toBeGreaterThan(0);
    });

    it("should have /api path configured", () => {
      const routes = apiService.settings.routes;
      const apiRoute = routes.find((r: any) => r.path === "/api");
      expect(apiRoute).toBeDefined();
    });

    it("should have correct whitelist entries", () => {
      const routes = apiService.settings.routes;
      const apiRoute = routes.find((r: any) => r.path === "/api");
      expect(apiRoute.whitelist).toContain("google-search.*");
      expect(apiRoute.whitelist).toContain("api.*");
    });

    it("should have search and health aliases configured", () => {
      const routes = apiService.settings.routes;
      const apiRoute = routes.find((r: any) => r.path === "/api");
      expect(apiRoute.aliases["POST /search/simple"]).toBe("google-search.simpleSearch");
      expect(apiRoute.aliases["GET /health"]).toBe("api.health");
    });

    it("should have CORS configured with wildcard origin", () => {
      const routes = apiService.settings.routes;
      const apiRoute = routes.find((r: any) => r.path === "/api");
      expect(apiRoute.cors.origin).toBe("*");
    });
  });

  // Health tests share ONE started gateway: each moleculer-web boot costs
  // tens of seconds, so per-test start/stop makes the suite unusable as a
  // gate. Config tests above never boot a broker.
  describe("health action", () => {
    let healthBroker: ServiceBroker;

    beforeAll(async () => {
      healthBroker = new ServiceBroker(testBrokerConfig);
      healthBroker.createService(ApiService);
      await healthBroker.start();
    }, 60000);

    afterAll(async () => {
      if (healthBroker) {
        await healthBroker.stop();
      }
    });

    it("should return correct response structure", async () => {
      const result = await healthBroker.call("api.health");
      expect(result).toHaveProperty("status", "ok");
      expect(result).toHaveProperty("timestamp");
      expect(result).toHaveProperty("service", "moleculer-search");
    });

    it("should return a valid ISO timestamp", async () => {
      const result = await healthBroker.call("api.health") as { timestamp: string };
      const timestamp = new Date(result.timestamp);
      expect(timestamp.toString()).not.toBe("Invalid Date");
    });
  });
});
