import { ServiceBroker } from "moleculer";
import RegistryClientService from "../services/registry-client.service";
import axios from "axios";
import { testBrokerConfig } from "./moleculer.config";

jest.mock("axios");
const mockedAxios = axios as jest.Mocked<typeof axios>;

describe("RegistryClientService", () => {
  let broker: ServiceBroker;
  let registryClientService: RegistryClientService;

  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(async () => {
    if (broker) {
      await broker.stop();
    }
  });

  describe("service initialization", () => {
    it("should have the correct service name", () => {
      broker = new ServiceBroker(testBrokerConfig);
      registryClientService = broker.createService(RegistryClientService) as RegistryClientService;
      expect(registryClientService.name).toBe("registry-client");
    });
  });

  describe("register action", () => {
    beforeEach(async () => {
      broker = new ServiceBroker(testBrokerConfig);
      registryClientService = broker.createService(RegistryClientService) as RegistryClientService;
      await broker.start();
    });

    it("should send correct registration payload", async () => {
      mockedAxios.post.mockResolvedValue({
        data: { message: "OK" }
      });

      await broker.call("registry-client.register");

      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://localhost:8085/api/v1/registry/register",
        expect.objectContaining({
          serviceName: "moleculer-search",
          operations: ["simpleSearch"],
          endpoint: "http://localhost:4050",
          healthCheck: "http://localhost:4050/api/health",
          framework: "Moleculer",
          version: "1.0.0",
          port: 4050
        }),
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
          timeout: 5000
        })
      );
    });

    it("should include metadata in registration payload", async () => {
      mockedAxios.post.mockResolvedValue({
        data: { message: "OK" }
      });

      await broker.call("registry-client.register");

      const callArgs = mockedAxios.post.mock.calls[0];
      const payload = callArgs[1] as Record<string, any>;
      expect(payload.metadata).toEqual({
        type: "moleculer",
        version: "1.0.0",
        provider: "google"
      });
    });

    it("should not throw on registration failure", async () => {
      mockedAxios.post.mockRejectedValue(new Error("Connection refused"));

      // Should not throw - service continues running
      await expect(broker.call("registry-client.register")).resolves.toBeUndefined();
    });

    it("should log success message on successful registration", async () => {
      mockedAxios.post.mockResolvedValue({
        data: { message: "Service registered" }
      });

      await broker.call("registry-client.register");
      expect(mockedAxios.post).toHaveBeenCalled();
    });
  });

  describe("heartbeat action", () => {
    beforeEach(async () => {
      broker = new ServiceBroker(testBrokerConfig);
      registryClientService = broker.createService(RegistryClientService) as RegistryClientService;
      await broker.start();
    });

    it("should send heartbeat to correct endpoint", async () => {
      mockedAxios.post.mockResolvedValue({
        data: { message: "OK" }
      });

      await broker.call("registry-client.heartbeat");

      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://localhost:8085/api/v1/registry/heartbeat/moleculer-search",
        {},
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
          timeout: 3000
        })
      );
    });

    it("should not throw on heartbeat failure", async () => {
      mockedAxios.post.mockRejectedValue(new Error("Timeout"));

      await expect(broker.call("registry-client.heartbeat")).resolves.toBeUndefined();
    });
  });

  describe("registration with custom environment", () => {
    const originalEnv = process.env;

    afterEach(() => {
      process.env = originalEnv;
    });

    it("should use custom registry URL from environment", async () => {
      process.env.SERVICE_REGISTRY_URL = "http://custom-registry:9090/api/v1/registry";
      process.env.SERVICE_HOST = "custom-host";
      process.env.SERVICE_PORT = "5000";

      broker = new ServiceBroker(testBrokerConfig);
      registryClientService = broker.createService(RegistryClientService) as RegistryClientService;
      await broker.start();

      mockedAxios.post.mockResolvedValue({ data: { message: "OK" } });

      await broker.call("registry-client.register");

      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://custom-registry:9090/api/v1/registry/register",
        expect.objectContaining({
          endpoint: "http://custom-host:5000",
          healthCheck: "http://custom-host:5000/api/health",
          port: 5000
        }),
        expect.any(Object)
      );
    });
  });

  describe("service lifecycle", () => {
    it("should have register and heartbeat actions defined", () => {
      broker = new ServiceBroker(testBrokerConfig);
      registryClientService = broker.createService(RegistryClientService) as RegistryClientService;

      expect(registryClientService.actions).toHaveProperty("register");
      expect(registryClientService.actions).toHaveProperty("heartbeat");
    });
  });
});
