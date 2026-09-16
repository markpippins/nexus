import { Service, ServiceBroker, Context, Errors } from "moleculer";
// SOLScript TypeScript core — deterministic interpreter (built to dist).
import { ResolutionInterpreter, QueryBuilder } from "@nexus/solscript";

/**
 * SOLScript interpreter service — wraps the @nexus/solscript TS core.
 *
 * Exposes the read surface of the moleculer REST facade contract
 * (typespec/v1/solscript/typescript/operations.tsp): evaluateProposition,
 * checkRule, checkTransitionGuard, executeQuery, health.
 *
 * READ-ONLY POSTURE (distribution/failover POC): the interpreter library
 * fully supports state transitions, but the REST tier disallows mutation —
 * transition-entity is handled at the gateway as a 405 (see api.service.ts).
 * This service intentionally does NOT expose a transition action.
 *
 * The interpreter is loaded from the in-memory storage port (fixtures);
 * a datasource-backed port (real resolution schema) is a later step.
 */
export default class SolScriptService extends Service {
  private interpreter: ResolutionInterpreter;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "solscript",
      actions: {
        evaluateProposition: {
          params: {
            propositionId: "string",
            context: { type: "object", optional: true },
          },
          async handler(ctx: Context<{ propositionId: string; context?: Record<string, unknown> }>) {
            const prop = this.interpreter.getProposition(ctx.params.propositionId);
            if (!prop) {
              throw new Errors.MoleculerError(
                `Proposition not found: ${ctx.params.propositionId}`, 404, "PROPOSITION_NOT_FOUND");
            }
            let result: [any, boolean, string];
            try {
              result = this.interpreter.evaluateProposition(prop, ctx.params.context);
            } catch (e: any) {
              throw new Errors.MoleculerError(
                e?.message ?? String(e), 422, "UNKNOWN_FRAME_DIMENSION");
            }
            const [disposition, allPassed, contextStatus] = result;
            // Disposition is a string enum ("Asserted"), not a wrapper — use it directly.
            return {
              propositionId: prop.id,
              disposition: disposition ?? null,
              value: disposition ? allPassed : null,
              groundingStatus: prop.groundingStatus,
              evaluatedAt: new Date().toISOString(),
              contextStatus,
              ...(disposition ? { reason: disposition } : {}),
            };
          },
        },

        checkRule: {
          params: {
            ruleId: "string",
            entityId: "string",
          },
          async handler(ctx: Context<{ ruleId: string; entityId: string }>) {
            const rule = this.interpreter.rules.get(ctx.params.ruleId);
            if (!rule) {
              throw new Errors.MoleculerError(`Rule not found: ${ctx.params.ruleId}`, 404, "RULE_NOT_FOUND");
            }
            const entity = this.interpreter.getEntity(ctx.params.entityId);
            if (!entity) {
              throw new Errors.MoleculerError(`Entity not found: ${ctx.params.entityId}`, 404, "ENTITY_NOT_FOUND");
            }
            const [passed, reason] = this.interpreter.checkRule(rule, entity);
            return {
              ruleId: ctx.params.ruleId,
              entityId: ctx.params.entityId,
              passed,
              reason,
            };
          },
        },

        checkTransitionGuard: {
          params: {
            transitionId: "string",
            entityId: "string",
          },
          async handler(ctx: Context<{ transitionId: string; entityId: string }>) {
            const transition = this.interpreter.getStateTransition(ctx.params.transitionId);
            if (!transition) {
              throw new Errors.MoleculerError(
                `Transition not found: ${ctx.params.transitionId}`, 404, "TRANSITION_NOT_FOUND");
            }
            const entity = this.interpreter.getEntity(ctx.params.entityId);
            if (!entity) {
              throw new Errors.MoleculerError(`Entity not found: ${ctx.params.entityId}`, 404, "ENTITY_NOT_FOUND");
            }
            const [passed, failedGuards] = this.interpreter.checkTransitionGuard(transition, entity);
            return {
              transitionId: ctx.params.transitionId,
              entityId: ctx.params.entityId,
              passed,
              failedGuards,
            };
          },
        },

        executeQuery: {
          params: {
            query: "object",
          },
          async handler(ctx: Context<{ query: any }>) {
            const spec = ctx.params.query;
            const builder = new QueryBuilder(this.interpreter);
            let q: any;
            try {
              q = builder.select(spec.conceptName);
            } catch (e: any) {
              throw new Errors.MoleculerError(
                e?.message ?? String(e), 404, "CONCEPT_NOT_FOUND");
            }
            for (const f of spec.filters ?? []) {
              try {
                q.where(f.attribute, f.operator, f.value);
              } catch (e: any) {
                throw new Errors.MoleculerError(
                  e?.message ?? String(e), 422, "QUERY_ERROR");
              }
            }
            if (spec.orderBy) q.orderBy(spec.orderBy, spec.orderDirection);
            if (spec.limit != null) q.limitN(spec.limit);
            if (spec.offset != null) q.offsetN(spec.offset);
            if (spec.fields) q.selectFieldsTo(...spec.fields);
            const rows = q.execute();
            return { rows, count: rows.length };
          },
        },

        health: {
          async handler() {
            return {
              status: "ok",
              service: "solscript-moleculer",
              loaded: {
                concepts: this.interpreter.concepts.size,
                entities: this.interpreter.entities.size,
                propositions: this.interpreter.propositions.size,
                rules: this.interpreter.rules.size,
                stateTransitions: this.interpreter.stateTransitions.size,
              },
            };
          },
        },
      },

      started: async () => {
        this.interpreter = new ResolutionInterpreter();
        // Load the in-memory storage (fixtures). A datasource port replaces this later.
        this.logger.info("SOLScript interpreter initialized (in-memory port)");
      },
    });

    this.interpreter = new ResolutionInterpreter();
  }
}