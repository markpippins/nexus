import { ServiceBroker } from "moleculer";
import ApiService from "../services/api.service";
import GoogleSearchService from "../services/google-search.service";
import { trafficCounterMiddleware, trafficSnapshot } from "../services/traffic-counter";
import { testBrokerConfig } from "./moleculer.config";

// M1 traffic canary tests. NOTE: no GOOGLE_* keys are set in this file, so
// google-search.simpleSearch calls exercise the credentials-error path —
// which is exactly what proves failed invocations are counted too.
describe("traffic canary", () => {
  let broker: ServiceBroker;

  beforeEach(async () => {
    // Off the live :4050; the gateway bind is required by the ApiGateway
    // mixin even though these tests only use in-process broker.call.
    process.env.SERVICE_PORT = "45982";
    broker = new ServiceBroker({
      ...testBrokerConfig,
      middlewares: [trafficCounterMiddleware()],
    });
    broker.createService(ApiService);
    broker.createService(GoogleSearchService);
    await broker.start();
  }, 60000);

  afterEach(async () => {
    if (broker) {
      await broker.stop();
    }
  });

  it("counts local action invocations by action name", async () => {
    const before = trafficSnapshot().counts["api.health"] ?? 0;

    await broker.call("api.health");
    await broker.call("api.health");

    expect(trafficSnapshot().counts["api.health"] - before).toBe(2);
  });

  it("counts failed invocations too", async () => {
    const before = trafficSnapshot().counts["google-search.simpleSearch"] ?? 0;

    const err: any = await broker
      .call("google-search.simpleSearch", { query: "q" })
      .catch((e: any) => e);

    // Missing creds in this file's env: typed error AND counted.
    expect(err.data).toMatchObject({ code: "SEARCH_CREDENTIALS" });
    expect(trafficSnapshot().counts["google-search.simpleSearch"] - before).toBe(1);
  });

  it("trafficCounts action returns the snapshot shape", async () => {
    const res = (await broker.call("api.trafficCounts")) as {
      startedAt: string;
      total: number;
      counts: Record<string, number>;
    };

    expect(typeof res.startedAt).toBe("string");
    expect(typeof res.total).toBe("number");
    expect(typeof res.counts).toBe("object");
    // This very call was counted.
    expect(res.counts["api.trafficCounts"] ?? 0).toBeGreaterThan(0);
  });
});
