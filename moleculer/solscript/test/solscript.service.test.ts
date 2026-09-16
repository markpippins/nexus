import { ServiceBroker } from "moleculer";
import SolScriptService from "../services/solscript.service";
import ApiService from "../services/api.service";
import { ResolutionInterpreter, Disposition } from "@nexus/solscript";
import { testBrokerConfig } from "./moleculer.config";

/** broker.call returns unknown under strict types; cast to any for test access. */
async function call(broker: ServiceBroker, action: string, params?: any): Promise<any> {
  return broker.call(action, params) as Promise<any>;
}

/**
 * SolScript facade tests — read actions live, write posture 405.
 *
 * The service loads an empty in-memory interpreter; we inject a Document
 * fixture (mirroring typescript/solscript/test/parity/scenario.json) directly
 * into the interpreter so the read actions have something to evaluate.
 */
describe("SolScriptService", () => {
  let broker: ServiceBroker;
  let svc: any;

  /** Build and load the parity Document fixture into the interpreter. */
  function loadFixture(interpreter: ResolutionInterpreter) {
    const doc: any = {
      id: "c-doc",
      name: "Document",
      attributes: {
        status: { id: "a-status", conceptId: "c-doc", name: "status", valueType: "text", isStateAttribute: true, allowedValues: ["draft", "reviewed"] },
        size: { id: "a-size", conceptId: "c-doc", name: "size", valueType: "integer", isStateAttribute: false, allowedValues: [] },
        title: { id: "a-title", conceptId: "c-doc", name: "title", valueType: "text", isStateAttribute: false, allowedValues: [] },
      },
      relationships: {},
      invariants: [],
      derivations: [],
      stateTransitions: [],
      rules: [],
    };
    interpreter.addConcept(doc);

    const review: any = {
      id: "t-review", conceptId: "c-doc", fromValue: "draft", toValue: "reviewed", name: "review",
      guards: [{
        id: "r-title-nonempty", name: "title non-empty", ruleType: "guard", severity: "hard",
        isRelationalCheck: false,
        expression: {
          id: "e-title-neq", kind: "operator", returnType: "boolean", operator: "<>",
          operands: [
            { id: "e-title-ref", kind: "attribute_ref", returnType: "text", attributeId: "a-title", operands: [] },
            { id: "e-title-lit", kind: "literal", returnType: "text", literalValue: "", operands: [] },
          ],
        },
        conditions: [],
      }],
    };
    interpreter.stateTransitions.set(review.id, review);
    interpreter.rules.set("r-title-nonempty", review.guards[0]);

    const ok: any = { id: "ent-ok", conceptId: "c-doc", externalId: "ext-ok", attributes: { status: "draft", size: 10, title: "parity" } };
    interpreter.addEntity(ok);
    const bad: any = { id: "ent-bad", conceptId: "c-doc", externalId: "ext-bad", attributes: { status: "draft", size: 3, title: "" } };
    interpreter.addEntity(bad);

    const bigRule: any = {
      id: "r-big", name: "size > 5", ruleType: "invariant", severity: "hard", isRelationalCheck: false,
      expression: {
        id: "e-size-gt", kind: "operator", returnType: "boolean", operator: ">",
        operands: [
          { id: "e-size-ref", kind: "attribute_ref", returnType: "integer", attributeId: "a-size", operands: [] },
          { id: "e-size-lit", kind: "literal", returnType: "integer", literalValue: 5, operands: [] },
        ],
      },
      conditions: [],
    };
    interpreter.rules.set(bigRule.id, bigRule);

    const big: any = {
      id: "p-big", title: "size over threshold", assetConceptId: "c-doc", subjectEntityId: "ent-ok",
      disposition: Disposition.Pending, assertions: [bigRule], comparisons: [], frameValues: [],
    };
    interpreter.addProposition(big);
  }

  beforeEach(async () => {
    broker = new ServiceBroker(testBrokerConfig);
    svc = broker.createService(SolScriptService) as any;
    await broker.start();
    loadFixture(svc.interpreter);
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("health reports loaded stats", async () => {
    const h = await call(broker, "solscript.health");
    expect(h.status).toBe("ok");
    expect(h.loaded.concepts).toBe(1);
    expect(h.loaded.entities).toBe(2);
  });

  it("evaluate-proposition asserts when assertions pass", async () => {
    const r = await call(broker, "solscript.evaluateProposition", { propositionId: "p-big" });
    expect(r.disposition).toBe("Asserted");
    expect(r.contextStatus).toBe("not_scoped");
  });

  it("check-rule returns passed for valid entity", async () => {
    const r = await call(broker, "solscript.checkRule", { ruleId: "r-big", entityId: "ent-ok" });
    expect(r.passed).toBe(true);
  });

  it("check-rule 404s for unknown entity", async () => {
    await expect(call(broker, "solscript.checkRule", { ruleId: "r-big", entityId: "missing" }))
      .rejects.toMatchObject({ code: 404, type: "ENTITY_NOT_FOUND" });
  });

  it("check-transition-guard passes for valid entity", async () => {
    const r = await call(broker, "solscript.checkTransitionGuard", { transitionId: "t-review", entityId: "ent-ok" });
    expect(r.passed).toBe(true);
  });

  it("execute-query filters and orders", async () => {
    const r = await call(broker, "solscript.executeQuery", {
      query: { conceptName: "Document", filters: [{ attribute: "size", operator: ">", value: 5 }], orderBy: "size", orderDirection: "DESC" },
    });
    expect(r.count).toBe(1);
    expect(r.rows[0].id).toBe("ent-ok");
  });
});

/**
 * Gateway write-posture test: transition-entity → 405.
 */
describe("SolScriptGateway 405 posture", () => {
  let broker: ServiceBroker;
  let api: any;

  beforeEach(async () => {
    process.env.SERVICE_PORT = "45982"; // off live :4060
    broker = new ServiceBroker(testBrokerConfig);
    api = broker.createService(ApiService) as any;
    await broker.start();
  });

  afterEach(async () => {
    await broker.stop();
  });

  it("transition-entity routes to a 405 write posture", async () => {
    await expect(call(broker, "api.transitionReadOnly405", {}))
      .rejects.toMatchObject({ code: 405, type: "METHOD_NOT_ALLOWED" });
  });

  it("health is live", async () => {
    const h = await call(broker, "api.health");
    expect(h.service).toBe("solscript-gateway");
  });
});