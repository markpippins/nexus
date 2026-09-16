package org.nexus.solscript.interpreter;

import org.nexus.solscript.compiler.ExpressionCompiler;
import org.nexus.solscript.events.KeychainEvents;
import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.models.Disposition;
import org.nexus.solscript.models.Severity;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * ResolutionInterpreter — faithful port of typescript/solscript/src/interpreter.ts.
 * In-memory interpreter for the resolution language. Holds the full concept
 * graph, entity store, propositions, rules, and expression/function registries.
 *
 * Wire-shape note: guard-result records keep snake_case keys
 * (rule_id/rule_name/passed/reason) and read_sets keep Python-shaped values,
 * because they flow into KeychainEvent read_sets whose digests must match the
 * Python reference byte-for-byte.
 */
public class ResolutionInterpreter {

    public static class GuardResult {
        public final String rule_id;
        public final String rule_name;
        public final boolean passed;
        public final String reason;
        public GuardResult(String rule_id, String rule_name, boolean passed, String reason) {
            this.rule_id = rule_id;
            this.rule_name = rule_name;
            this.passed = passed;
            this.reason = reason;
        }
        public Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("rule_id", rule_id);
            m.put("rule_name", rule_name);
            m.put("passed", passed);
            m.put("reason", reason);
            return m;
        }
    }

    public static class TransitionOutcome {
        public final boolean committed;
        public final List<Object> results; // GuardResult[] or Map{error:...}
        public final KeychainEvents.KeychainEvent event;
        public TransitionOutcome(boolean committed, List<Object> results, KeychainEvents.KeychainEvent event) {
            this.committed = committed;
            this.results = results;
            this.event = event;
        }
    }

    /** Transition listener — fired on a SUCCESSFUL transition only. */
    public interface TransitionListener {
        void onTransition(Map<String, Object> info);
    }

    /** Transition-event listener — fired for EVERY transition attempt. */
    public interface TransitionEventListener {
        void onEvent(KeychainEvents.KeychainEvent event);
    }

    public interface ChangeHandler {
        void onChange(String conceptName, String entityId,
                      List<Map<String, Object>> results);
    }

    public final Map<String, SolModels.Concept> concepts = new LinkedHashMap<>();
    public final Map<String, SolModels.Entity> entities = new LinkedHashMap<>();
    public final Map<String, SolModels.Proposition> propositions = new LinkedHashMap<>();
    public final Map<String, SolModels.Expression> expressions = new LinkedHashMap<>();
    public final Map<String, SolModels.Rule> rules = new LinkedHashMap<>();
    public final Map<String, ExpressionCompiler.FunctionBindingWithFn> functions = new LinkedHashMap<>();
    public final Map<String, SolModels.Representation> representations = new LinkedHashMap<>();
    public final Map<String, SolModels.ConceptRelationship> relationships = new LinkedHashMap<>();
    public final Map<String, SolModels.ConceptStateTransition> stateTransitions = new LinkedHashMap<>();
    // v31: frame discipline
    public final Map<String, SolModels.FrameDimension> frameDimensions = new LinkedHashMap<>();
    public final Map<String, SolModels.FrameDimensionValue> frameDimensionValues = new LinkedHashMap<>();
    // v35: frame semantics
    public final Map<String, SolModels.FrameDimensionMeaning> frameDimensionMeanings = new LinkedHashMap<>();

    // Runtime state
    public final Map<String, Object> evaluationCache = new LinkedHashMap<>();
    public Map<String, Object> executionContext = new LinkedHashMap<>();
    public final List<ChangeHandler> eventHandlers = new ArrayList<>();
    public final List<TransitionListener> transitionListeners = new ArrayList<>();
    public final List<TransitionEventListener> transitionEventListeners = new ArrayList<>();
    public KeychainEvents.KeychainEvent lastTransitionEvent = null;

    private final ExpressionCompiler compiler;

    public ResolutionInterpreter() {
        this.compiler = new ExpressionCompiler(this.host());
        registerBuiltinFunctions();
    }

    /** ExpressionCompiler host view over this interpreter's registries. */
    public ExpressionCompiler.Host host() {
        return new ExpressionCompiler.Host() {
            public SolModels.ConceptAttribute getAttribute(String attributeId) {
                return ResolutionInterpreter.this.getAttribute(attributeId);
            }
            public SolModels.ConceptRelationship getRelationship(String relationshipId) {
                return ResolutionInterpreter.this.relationships.get(relationshipId);
            }
            public SolModels.Proposition getProposition(String propositionId) {
                return ResolutionInterpreter.this.propositions.get(propositionId);
            }
            public ExpressionCompiler.FunctionBindingWithFn getFunction(String name) {
                return ResolutionInterpreter.this.functions.get(name);
            }
            public Iterable<SolModels.Entity> entities() {
                return ResolutionInterpreter.this.entities.values();
            }
        };
    }

    // ── Built-in functions ────────────────────────────────────────

    private void registerBuiltinFunctions() {
        Map<String, java.util.function.Function<List<Object>, Object>> builtins = new LinkedHashMap<>();
        builtins.put("count", args -> args.stream().filter(a -> a != null).count());
        builtins.put("sum", args -> args.stream()
            .filter(a -> a != null).mapToDouble(a -> toNumber(a)).sum());
        builtins.put("avg", args -> {
            List<Object> vals = args.stream().filter(a -> a != null).toList();
            double total = vals.stream().mapToDouble(ExpressionCompiler::toNumberSafe).sum();
            return total / Math.max(vals.size(), 1);
        });
        builtins.put("min", args -> {
            List<Double> vals = args.stream().filter(a -> a != null)
                .map(ExpressionCompiler::toNumberSafe).toList();
            return vals.isEmpty() ? Double.POSITIVE_INFINITY : vals.stream().mapToDouble(Double::doubleValue).min().getAsDouble();
        });
        builtins.put("max", args -> {
            List<Double> vals = args.stream().filter(a -> a != null)
                .map(ExpressionCompiler::toNumberSafe).toList();
            return vals.isEmpty() ? Double.NEGATIVE_INFINITY : vals.stream().mapToDouble(Double::doubleValue).max().getAsDouble();
        });
        builtins.put("coalesce", args -> args.stream().filter(a -> a != null).findFirst().orElse(null));
        builtins.put("concat", args -> {
            StringBuilder sb = new StringBuilder();
            for (Object a : args) sb.append(a == null ? "" : String.valueOf(a));
            return sb.toString();
        });
        builtins.put("contains", args -> {
            Object s = args.size() > 0 ? args.get(0) : null;
            Object sub = args.size() > 1 ? args.get(1) : null;
            return s != null && String.valueOf(s).contains(String.valueOf(sub));
        });
        builtins.put("starts_with", args -> {
            Object s = args.size() > 0 ? args.get(0) : null;
            Object prefix = args.size() > 1 ? args.get(1) : null;
            return s != null && String.valueOf(s).startsWith(String.valueOf(prefix));
        });
        builtins.put("ends_with", args -> {
            Object s = args.size() > 0 ? args.get(0) : null;
            Object suffix = args.size() > 1 ? args.get(1) : null;
            return s != null && String.valueOf(s).endsWith(String.valueOf(suffix));
        });
        builtins.put("is_null", args -> args.size() > 0 && args.get(0) == null);
        builtins.put("is_not_null", args -> args.size() > 0 && args.get(0) != null);

        for (Map.Entry<String, java.util.function.Function<List<Object>, Object>> e : builtins.entrySet()) {
            String name = e.getKey();
            java.util.function.Function<List<Object>, Object> fn = e.getValue();
            functions.put(name, new ExpressionCompiler.FunctionBindingWithFn(
                new SolModels.FunctionBinding(name, "", 0, "any", "Built-in: " + name),
                fn));
        }
    }

    /** Register or replace a callable function binding. */
    public void registerFunction(String name, java.util.function.Function<List<Object>, Object> fn, String notes) {
        functions.put(name, new ExpressionCompiler.FunctionBindingWithFn(
            new SolModels.FunctionBinding(name, "", 0, "any", notes != null ? notes : "Registered: " + name),
            fn));
    }

    // ── Concept / entity / proposition lookups ────────────────────

    public void addConcept(SolModels.Concept concept) { concepts.put(concept.id, concept); }
    public SolModels.Concept getConcept(String conceptId) { return concepts.get(conceptId); }
    public SolModels.Concept getConceptByName(String name) {
        for (SolModels.Concept c : concepts.values()) if (c.name.equals(name)) return c;
        return null;
    }
    public void addEntity(SolModels.Entity entity) { entities.put(entity.id, entity); }
    public SolModels.Entity getEntity(String entityId) { return entities.get(entityId); }
    public SolModels.Entity getEntityByExternalId(String externalId) {
        for (SolModels.Entity e : entities.values()) if (externalId.equals(e.externalId)) return e;
        return null;
    }
    public void addProposition(SolModels.Proposition proposition) { propositions.put(proposition.id, proposition); }
    public SolModels.Proposition getProposition(String propositionId) { return propositions.get(propositionId); }
    public SolModels.ConceptAttribute getAttribute(String attributeId) {
        for (SolModels.Concept concept : concepts.values()) {
            for (SolModels.ConceptAttribute attr : concept.attributes.values()) {
                if (attr.id.equals(attributeId)) return attr;
            }
        }
        return null;
    }
    public SolModels.ConceptRelationship getRelationship(String relationshipId) { return relationships.get(relationshipId); }
    public SolModels.ConceptStateTransition getStateTransition(String transitionId) { return stateTransitions.get(transitionId); }
    public ExpressionCompiler.FunctionBindingWithFn getFunction(String name) { return functions.get(name); }

    // ── Expression evaluation ─────────────────────────────────────

    public Object evaluate(SolModels.Expression expression, Map<String, Object> context) {
        var compiled = compiler.compileExpression(expression);
        return compiled.apply(context);
    }

    // ── Rule evaluation ───────────────────────────────────────────

    public Map<String, Object> checkRule(SolModels.Rule rule, SolModels.Entity entity) {
        Map<String, Object> ctx = new LinkedHashMap<>();
        ctx.put("entity", entity);
        try {
            if (rule.expression != null) {
                Object result = evaluate(rule.expression, ctx);
                boolean passed = truthy(result);
                Map<String, Object> out = new LinkedHashMap<>();
                out.put("passed", passed);
                out.put("reason", "Rule '" + rule.name + "' " + (passed ? "passed" : "failed"));
                return out;
            }
            return Map.of("passed", false, "reason", "Rule '" + rule.name + "' has no expression");
        } catch (Exception exc) {
            if (rule.severity == Severity.hard) {
                return Map.of("passed", false, "reason",
                    "Rule '" + rule.name + "' error: " + (exc.getMessage() != null ? exc.getMessage() : String.valueOf(exc)));
            }
            return Map.of("passed", true, "reason",
                "Rule '" + rule.name + "' soft error: " + (exc.getMessage() != null ? exc.getMessage() : String.valueOf(exc)));
        }
    }

    public Map<String, Object> checkTransitionGuard(SolModels.ConceptStateTransition transition, SolModels.Entity entity) {
        List<Object> results = new ArrayList<>();
        List<Boolean> passedList = new ArrayList<>();

        java.util.function.Consumer<SolModels.Rule> check = (rule) -> {
            Map<String, Object> r = checkRule(rule, entity);
            results.add(new GuardResult(rule.id, rule.name, (boolean) r.get("passed"), (String) r.get("reason")).toMap());
            if (!(boolean) r.get("passed")) passedList.add(false);
        };

        for (SolModels.Rule rule : transition.guards) check.accept(rule);
        SolModels.Concept concept = concepts.get(transition.conceptId);
        if (concept != null) for (SolModels.Rule rule : concept.invariants) check.accept(rule);

        boolean allPassed = passedList.isEmpty();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("passed", allPassed);
        out.put("failedGuards", results);
        return out;
    }

    // ── State transitions ─────────────────────────────────────────

    public TransitionOutcome transitionEntity(String entityId, String transitionId,
                                              Map<String, Object> opts) {
        String nowIso = java.time.OffsetDateTime.now().toString();
        String sourceEventId = (String) opts.getOrDefault("sourceEventId", "transition:" + transitionId + ":entity:" + entityId);
        String correlationId = (String) opts.get("correlationId");
        String actor = (String) opts.get("actor");
        String sourceNamespace = (String) opts.get("sourceNamespace");

        java.util.function.Function<List<Object>, TransitionOutcome> reject = (results) -> {
            Map<String, Object> input = new LinkedHashMap<>();
            input.put("sourceEventId", sourceEventId);
            input.put("entityId", entityId);
            input.put("transitionId", transitionId);
            input.put("outcome", "rejected");
            input.put("results", results);
            input.put("conceptId", null);
            input.put("correlationId", correlationId);
            input.put("actor", actor);
            input.put("sourceNamespace", sourceNamespace);
            input.put("effectiveAt", nowIso);
            KeychainEvents.KeychainEvent event = KeychainEvents.buildTransitionEvent(input);
            this.lastTransitionEvent = event;
            this.notifyTransitionEvent(event);
            return new TransitionOutcome(false, results, event);
        };

        SolModels.Entity entity = entities.get(entityId);
        if (entity == null) return reject.apply(new ArrayList<>(List.of(Map.of("error", "Entity not found"))));

        SolModels.ConceptStateTransition transition = stateTransitions.get(transitionId);
        if (transition == null) {
            return reject.apply(new ArrayList<>(List.of(Map.of("error", "Transition not found"))));
        }

        Map<String, Object> guardResult = checkTransitionGuard(transition, entity);
        boolean allPassed = (boolean) guardResult.get("passed");
        @SuppressWarnings("unchecked")
        List<Object> results = (List<Object>) guardResult.get("failedGuards");
        String eventId = sourceEventId;
        SolModels.Concept concept = concepts.get(entity.conceptId);
        SolModels.ConceptAttribute stateAttr = null;
        if (concept != null) {
            for (SolModels.ConceptAttribute a : concept.attributes.values()) {
                if (a.isStateAttribute) { stateAttr = a; break; }
            }
        }
        Object stateBefore = stateAttr != null ? entity.attributes.getOrDefault(stateAttr.name, null) : null;
        String outcome = allPassed ? "committed" : "refused";

        if (!allPassed) {
            Map<String, Object> input = new LinkedHashMap<>();
            input.put("sourceEventId", eventId);
            input.put("entityId", entityId);
            input.put("transitionId", transitionId);
            input.put("outcome", outcome);
            input.put("results", results);
            input.put("conceptId", entity.conceptId);
            input.put("stateBefore", stateBefore);
            input.put("stateAfter", stateBefore);
            input.put("correlationId", correlationId);
            input.put("actor", actor);
            input.put("sourceNamespace", sourceNamespace);
            input.put("effectiveAt", nowIso);
            KeychainEvents.KeychainEvent event = KeychainEvents.buildTransitionEvent(input);
            this.lastTransitionEvent = event;
            this.notifyTransitionEvent(event);
            return new TransitionOutcome(false, results, event);
        }

        if (stateAttr != null && stateAttr.allowedValues.contains(transition.toValue)) {
            entity.attributes.put(stateAttr.name, transition.toValue);
        }
        Object stateAfter = stateAttr != null ? entity.attributes.getOrDefault(stateAttr.name, null) : null;
        Map<String, Object> input = new LinkedHashMap<>();
        input.put("sourceEventId", eventId);
        input.put("entityId", entityId);
        input.put("transitionId", transitionId);
        input.put("outcome", outcome);
        input.put("results", results);
        input.put("conceptId", entity.conceptId);
        input.put("stateBefore", stateBefore);
        input.put("stateAfter", stateAfter);
        input.put("correlationId", correlationId);
        input.put("actor", actor);
        input.put("sourceNamespace", sourceNamespace);
        input.put("effectiveAt", nowIso);
        KeychainEvents.KeychainEvent event = KeychainEvents.buildTransitionEvent(input);
        this.lastTransitionEvent = event;
        this.notifyTransitionEvent(event);

        // Fired only on a successful transition (refused/rejected do NOT snapshot).
        for (TransitionListener listener : transitionListeners) {
            try {
                Map<String, Object> info = new LinkedHashMap<>();
                info.put("transition_id", transitionId);
                info.put("entity_id", entityId);
                info.put("to_value", transition.toValue);
                info.put("effective_at", event.effectiveAt);
                info.put("event", event);
                listener.onTransition(info);
            } catch (Exception ignored) { }
        }

        return new TransitionOutcome(true, results, event);
    }

    public void registerTransitionListener(TransitionListener listener) {
        transitionListeners.add(listener);
    }

    public void registerTransitionEventListener(TransitionEventListener listener) {
        transitionEventListeners.add(listener);
    }

    private void notifyTransitionEvent(KeychainEvents.KeychainEvent event) {
        for (TransitionEventListener listener : transitionEventListeners) {
            try { listener.onEvent(event); } catch (Exception ignored) { }
        }
    }

    // ── Frame discipline (v31/v35) ────────────────────────────────

    public void addFrameDimension(SolModels.FrameDimension dim) { frameDimensions.put(dim.id, dim); }
    public SolModels.FrameDimension getFrameDimension(String dimId) { return frameDimensions.get(dimId); }
    public SolModels.FrameDimension getFrameDimensionByName(String name) {
        for (SolModels.FrameDimension d : frameDimensions.values()) if (d.name.equals(name)) return d;
        return null;
    }
    public void addFrameDimensionValue(SolModels.FrameDimensionValue val) { frameDimensionValues.put(val.id, val); }
    public void addPropositionFrameValue(SolModels.PropositionFrameValue pfv) {
        SolModels.Proposition prop = propositions.get(pfv.propositionId);
        if (prop != null) prop.frameValues.add(pfv);
    }
    public void addFrameDimensionMeaning(SolModels.FrameDimensionMeaning meaning) {
        frameDimensionMeanings.put(meaning.id, meaning);
    }

    /** Meaning propositions describing a frame dimension (id or name). */
    public List<SolModels.Proposition> meaningsOf(String dimension, String value) {
        SolModels.FrameDimension dim = frameDimensions.get(dimension) != null
            ? frameDimensions.get(dimension) : getFrameDimensionByName(dimension);
        if (dim == null) return List.of();

        List<SolModels.Proposition> results = new ArrayList<>();
        for (SolModels.FrameDimensionMeaning meaning : frameDimensionMeanings.values()) {
            SolModels.Proposition prop = propositions.get(meaning.propositionId);
            if (prop == null) continue;
            if (dim.id.equals(meaning.dimensionId)) {
                results.add(prop); // whole-dimension meaning always applies
            } else if (meaning.frameDimensionValueId != null) {
                SolModels.FrameDimensionValue fdv = frameDimensionValues.get(meaning.frameDimensionValueId);
                if (fdv != null && dim.id.equals(fdv.dimensionId)) {
                    if (value == null || fdv.value.equals(value)) results.add(prop);
                }
            }
        }
        return results;
    }

    // ── Proposition evaluation ────────────────────────────────────

    /**
     * Evaluate a proposition with optional frame-context discipline (v32).
     * Returns [disposition, allPassed, contextStatus] where contextStatus is
     * 'not_scoped' | 'context_required' | 'context_mismatch' | 'scoped'.
     * A null disposition with a context gate status means "not evaluated".
     */
    public Object[] evaluateProposition(SolModels.Proposition prop, Map<String, Object> context) {
        // ── Context gate: frame discipline (v31/v32) ─────────────
        int framedCount = prop.frameValues.size();
        String contextStatus;
        if (framedCount > 0) {
            if (context == null) return new Object[] { null, false, "context_required" };
            for (String key : context.keySet()) {
                SolModels.FrameDimension dim = getFrameDimensionByName(key);
                if (dim == null) {
                    throw new IllegalStateException(
                        "evaluate_proposition: context key '" + key + "' names no known frame_dimension");
                }
            }
            for (SolModels.PropositionFrameValue pfv : prop.frameValues) {
                SolModels.FrameDimension dim = frameDimensions.get(pfv.dimensionId);
                if (dim == null) return new Object[] { null, false, "context_required" };
                Object ctxVal = context.get(dim.name);
                if (ctxVal == null) return new Object[] { null, false, "context_required" };

                if ("governed_reference".equals(dim.valueKind)) {
                    SolModels.FrameDimensionValue fdv = frameDimensionValues.get(pfv.referenceValueId != null ? pfv.referenceValueId : "");
                    if (fdv == null || !fdv.value.equals(String.valueOf(ctxVal))) {
                        return new Object[] { null, false, "context_mismatch" };
                    }
                } else if ("typed_scalar".equals(dim.valueKind)) {
                    String scalarType = dim.scalarType != null ? dim.scalarType : "text";
                    String scalar = pfv.scalarValue;
                    try {
                        if ("integer".equals(scalarType)) {
                            if ((long) Math.floor(toNumber(ctxVal)) != (long) Math.floor(toNumber(scalar))) {
                                return new Object[] { null, false, "context_mismatch" };
                            }
                        } else if ("numeric".equals(scalarType)) {
                            if (toNumber(ctxVal) != toNumber(scalar)) {
                                return new Object[] { null, false, "context_mismatch" };
                            }
                        } else if ("boolean".equals(scalarType)) {
                            boolean boolVal = truthy(ctxVal);
                            boolean scalarBool = List.of("true", "True", "1").contains(String.valueOf(scalar));
                            if (boolVal != scalarBool) {
                                return new Object[] { null, false, "context_mismatch" };
                            }
                        } else {
                            if (!String.valueOf(ctxVal).equals(String.valueOf(scalar))) {
                                return new Object[] { null, false, "context_mismatch" };
                            }
                        }
                    } catch (Exception e) {
                        return new Object[] { null, false, "context_mismatch" };
                    }
                } else {
                    throw new IllegalStateException(
                        "evaluate_proposition: dimension " + dim.name + " has unrecognized value_kind " + dim.valueKind);
                }
            }
            contextStatus = "scoped";
        } else {
            contextStatus = "not_scoped";
        }

        // ── Assertion evaluation ─────────────────────────────────
        SolModels.Entity entity = entities.get(prop.subjectEntityId);
        if (entity == null) return new Object[] { Disposition.Rejected, false, contextStatus };

        boolean allPassed = true;
        boolean relationalFailed = false;
        for (SolModels.Rule rule : prop.assertions) {
            Map<String, Object> r = checkRule(rule, entity);
            boolean passed = (boolean) r.get("passed");
            if (!passed) {
                allPassed = false;
                if (rule.isRelationalCheck) relationalFailed = true;
            }
        }

        Disposition disposition = allPassed
            ? Disposition.Asserted
            : relationalFailed ? Disposition.Disputed : Disposition.Rejected;

        return new Object[] { disposition, allPassed, contextStatus };
    }

    public Disposition reopenDisputedProposition(SolModels.Proposition prop, String externalId) {
        if (prop.disposition != Disposition.Disputed) return prop.disposition;
        Object[] result = evaluateProposition(prop, null);
        return result[0] != null ? (Disposition) result[0] : prop.disposition;
    }

    // ── Change events ─────────────────────────────────────────────

    public List<Map<String, Object>> onChange(String conceptName, String entityId) {
        List<Map<String, Object>> results = new ArrayList<>();
        SolModels.Entity entity = entities.get(entityId);
        if (entity == null) return results;
        SolModels.Concept concept = getConceptByName(conceptName);
        if (concept == null) return results;

        String externalId = entity.externalId;

        for (SolModels.Proposition prop : propositions.values()) {
            if (entityId.equals(prop.subjectEntityId) && concept.id.equals(prop.assetConceptId)) {
                Disposition old = prop.disposition;
                Disposition next;
                if (old == Disposition.Disputed && externalId != null) {
                    next = reopenDisputedProposition(prop, externalId);
                } else {
                    Object[] r = evaluateProposition(prop, null);
                    next = r[0] != null ? (Disposition) r[0] : null;
                }
                if (next != null && next != old) {
                    prop.disposition = next;
                    prop.lastEvaluatedAt = java.time.OffsetDateTime.now().toString();
                    Map<String, Object> res = new LinkedHashMap<>();
                    res.put("propositionId", prop.id);
                    res.put("action", "event_evaluate");
                    res.put("disposition", next);
                    results.add(res);
                }
            }
        }

        for (ChangeHandler handler : eventHandlers) {
            try { handler.onChange(conceptName, entityId, results); } catch (Exception ignored) { }
        }
        return results;
    }

    public void registerEventHandler(ChangeHandler handler) {
        eventHandlers.add(handler);
    }

    // ── Convenience helpers ───────────────────────────────────────

    public SolModels.Entity addEntityByConceptName(String conceptName, Map<String, Object> attributes, String externalId) {
        SolModels.Concept concept = getConceptByName(conceptName);
        if (concept == null) throw new IllegalStateException("Concept not found: " + conceptName);
        SolModels.Entity entity = new SolModels.Entity();
        entity.id = UUID.randomUUID().toString();
        entity.conceptId = concept.id;
        entity.attributes = new LinkedHashMap<>(attributes);
        if (externalId != null) entity.externalId = externalId;
        entities.put(entity.id, entity);
        return entity;
    }

    // ── Helpers ───────────────────────────────────────────────────

    private static boolean truthy(Object a) {
        if (a == null) return false;
        if (a instanceof Boolean b) return b;
        if (a instanceof Number n) return n.doubleValue() != 0;
        if (a instanceof String s) return !s.isEmpty();
        return true;
    }

    static double toNumber(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        try { return Double.parseDouble(String.valueOf(value)); }
        catch (NumberFormatException e) { return Double.NaN; }
    }
}