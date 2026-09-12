import { ServiceBroker } from "moleculer";
import ApiService from "../services/api.service";
import GoogleSearchService from "../services/google-search.service";
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
    broker = new ServiceBroker(testBrokerConfig);
    broker.createService(ApiService);
    broker.createService(GoogleSearchService);
    await broker.start();
  }, 60000);

  afterEach(async () => {
    if (broker) {
      await broker.stop();
    }
  });

  it("trafficCounts reports per-action request totals", async () => {
    await broker.call("api.health");
    await broker.call("api.health");

    const res = (await broker.call("api.trafficCounts")) as {
      startedAt: string;
      total: number;
      counts: Record<string, number>;
    };

    expect(typeof res.startedAt).toBe("string");
    expect((res.counts["api.health"] ?? 0)).toBeGreaterThanOrEqual(2);
    expect(res.total).toBeGreaterThanOrEqual(2);
  });

  it("counts failed invocations too", async () => {
    const before =
      ((await broker.call("api.trafficCounts")) as any).counts[
        "google-search.simpleSearch"
      ] ?? 0;

    const err: any = await broker
      .call("google-search.simpleSearch", { query: "q" })
      .catch((e: any) => e);

    // Missing creds in this file's env: typed error AND counted.
    expect(err.data).toMatchObject({ code: "SEARCH_CREDENTIALS" });
    const after = ((await broker.call("api.trafficCounts")) as any).counts[
      "google-search.simpleSearch"
    ] ?? 0;
    expect(after - before).toBe(1);
  });
});
