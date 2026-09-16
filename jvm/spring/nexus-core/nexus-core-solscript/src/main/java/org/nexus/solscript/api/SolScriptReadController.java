package org.nexus.solscript.api;

import org.nexus.solscript.interpreter.ResolutionInterpreter;
import org.nexus.solscript.models.Disposition;
import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.models.SolOperator;
import org.nexus.solscript.query.QueryBuilder;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Read-only projection of the SOLScript Moleculer REST facade
 * (typespec/v1/solscript/typescript/operations.tsp).
 *
 * The library (ResolutionInterpreter) fully supports state transitions, but
 * the REST tier is READ-ONLY: the read/query actions below are live, and the
 * write action (transition-entity) is disallowed (405) — see
 * SolScriptWriteController. This is the readonly-by-default posture: querying
 * stays available, mutation is gated off until the JetStream write path lands.
 */
@RestController
@RequestMapping("/api/solscript")
public class SolScriptReadController {

    private final SolScriptRuntime runtime;

    public SolScriptReadController(SolScriptRuntime runtime) {
        this.runtime = runtime;
    }

    private ResolutionInterpreter interp() {
        return runtime.interpreter();
    }

    // ── POST /api/solscript/evaluate-proposition ──────────────────
    @PostMapping("/evaluate-proposition")
    public Object evaluateProposition(@RequestBody Map<String, Object> body) {
        String propositionId = str(body.get("propositionId"));
        SolModels.Proposition prop = interp().getProposition(propositionId);
        if (prop == null) throw new SolScriptException(HttpStatus.NOT_FOUND, "PROPOSITION_NOT_FOUND",
            "Proposition not found: " + propositionId, false);
        @SuppressWarnings("unchecked")
        Map<String, Object> context = (Map<String, Object>) body.get("context");
        Object[] result;
        try {
            result = interp().evaluateProposition(prop, context);
        } catch (Exception e) {
            throw new SolScriptException(HttpStatus.UNPROCESSABLE_ENTITY, "UNKNOWN_FRAME_DIMENSION",
                e.getMessage() != null ? e.getMessage() : String.valueOf(e), false);
        }
        Disposition disposition = (Disposition) result[0];
        boolean allPassed = (boolean) result[1];
        String contextStatus = (String) result[2];

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("propositionId", propositionId);
        out.put("disposition", disposition != null ? disposition.value() : null);
        out.put("value", disposition != null ? allPassed : null);
        out.put("groundingStatus", prop.groundingStatus);
        out.put("evaluatedAt", java.time.OffsetDateTime.now().toString());
        out.put("contextStatus", contextStatus);
        if (disposition != null) out.put("reason", disposition.value());
        return out;
    }

    // ── POST /api/solscript/check-rule ────────────────────────────
    @PostMapping("/check-rule")
    public Object checkRule(@RequestBody Map<String, Object> body) {
        String ruleId = str(body.get("ruleId"));
        String entityId = str(body.get("entityId"));
        SolModels.Rule rule = interp().rules.get(ruleId);
        if (rule == null) throw new SolScriptException(HttpStatus.NOT_FOUND, "RULE_NOT_FOUND", "Rule not found: " + ruleId, false);
        SolModels.Entity entity = interp().getEntity(entityId);
        if (entity == null) throw new SolScriptException(HttpStatus.NOT_FOUND, "ENTITY_NOT_FOUND", "Entity not found: " + entityId, false);
        Map<String, Object> r = interp().checkRule(rule, entity);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ruleId", ruleId);
        out.put("entityId", entityId);
        out.put("passed", r.get("passed"));
        out.put("reason", r.get("reason"));
        return out;
    }

    // ── POST /api/solscript/check-transition-guard ────────────────
    @PostMapping("/check-transition-guard")
    public Object checkTransitionGuard(@RequestBody Map<String, Object> body) {
        String transitionId = str(body.get("transitionId"));
        String entityId = str(body.get("entityId"));
        SolModels.ConceptStateTransition transition = interp().getStateTransition(transitionId);
        if (transition == null) throw new SolScriptException(HttpStatus.NOT_FOUND, "TRANSITION_NOT_FOUND",
            "Transition not found: " + transitionId, false);
        SolModels.Entity entity = interp().getEntity(entityId);
        if (entity == null) throw new SolScriptException(HttpStatus.NOT_FOUND, "ENTITY_NOT_FOUND", "Entity not found: " + entityId, false);
        Map<String, Object> g = interp().checkTransitionGuard(transition, entity);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("transitionId", transitionId);
        out.put("entityId", entityId);
        out.put("passed", g.get("passed"));
        out.put("failedGuards", g.get("failedGuards"));
        return out;
    }

    // ── POST /api/solscript/execute-query ─────────────────────────
    @PostMapping("/execute-query")
    public Object executeQuery(@RequestBody Map<String, Object> body) {
        @SuppressWarnings("unchecked")
        Map<String, Object> querySpec = (Map<String, Object>) body.get("query");
        if (querySpec == null) throw new SolScriptException(HttpStatus.UNPROCESSABLE_ENTITY, "QUERY_ERROR", "query required", false);
        String conceptName = str(querySpec.get("conceptName"));
        QueryBuilder builder = new QueryBuilder(interp());
        QueryBuilder.Query q;
        try {
            q = builder.select(conceptName);
        } catch (Exception e) {
            throw new SolScriptException(HttpStatus.NOT_FOUND, "CONCEPT_NOT_FOUND",
                e.getMessage() != null ? e.getMessage() : String.valueOf(e), false);
        }
        List<Map<String, Object>> filters = listOfMaps(querySpec.get("filters"));
        for (Map<String, Object> f : filters) {
            String attribute = str(f.get("attribute"));
            String opStr = str(f.get("operator"));
            Object value = f.get("value");
            try {
                q.where(attribute, SolOperator.from(opStr), value);
            } catch (Exception e) {
                throw new SolScriptException(HttpStatus.UNPROCESSABLE_ENTITY, "QUERY_ERROR",
                    e.getMessage() != null ? e.getMessage() : String.valueOf(e), false);
            }
        }
        if (querySpec.get("orderBy") != null) q.orderBy(str(querySpec.get("orderBy")), str(querySpec.get("orderDirection")));
        if (querySpec.get("limit") != null) q.limitN(((Number) querySpec.get("limit")).intValue());
        if (querySpec.get("offset") != null) q.offsetN(((Number) querySpec.get("offset")).intValue());
        if (querySpec.get("fields") != null) {
            List<Object> fields = (List<Object>) querySpec.get("fields");
            q.selectFieldsTo(fields.stream().map(String::valueOf).toArray(String[]::new));
        }
        List<Map<String, Object>> rows = q.execute();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("rows", rows);
        out.put("count", rows.size());
        return out;
    }

    // ── GET /api/solscript/health ─────────────────────────────────
    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> loaded = new LinkedHashMap<>();
        loaded.put("concepts", interp().concepts.size());
        loaded.put("entities", interp().entities.size());
        loaded.put("propositions", interp().propositions.size());
        loaded.put("rules", interp().rules.size());
        loaded.put("stateTransitions", interp().stateTransitions.size());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", "ok");
        out.put("service", "solscript-java");
        out.put("loaded", loaded);
        return out;
    }

    // ── Helpers ───────────────────────────────────────────────────

    static String str(Object o) { return o != null ? String.valueOf(o) : null; }

    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> listOfMaps(Object o) {
        if (!(o instanceof List<?> l)) return new ArrayList<>();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object item : l) if (item instanceof Map) out.add((Map<String, Object>) item);
        return out;
    }
}