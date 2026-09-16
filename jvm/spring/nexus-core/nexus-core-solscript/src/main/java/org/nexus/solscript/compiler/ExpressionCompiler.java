package org.nexus.solscript.compiler;

import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.models.SolOperator;
import org.nexus.solscript.models.ExpressionKind;
import org.nexus.solscript.models.Quantifier;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/**
 * Expression compiler — faithful port of typescript/solscript/src/expression-compiler.ts.
 *
 * Map<String, Object> is a Map<String,Object> with the subject entity under "entity"
 * (plus optional "parent" / "target_entity" / "subject" keys). Compiled
 * expressions are Function<Map<String, Object>,Object>.
 */
public final class ExpressionCompiler {

    /** Host view over interpreter registries (getAttribute/getRelationship/getProposition/getFunction/entities). */
    public interface Host {
        SolModels.ConceptAttribute getAttribute(String attributeId);
        SolModels.ConceptRelationship getRelationship(String relationshipId);
        SolModels.Proposition getProposition(String propositionId);
        FunctionBindingWithFn getFunction(String name);
        Iterable<SolModels.Entity> entities();
    }

    /** FunctionBinding plus an optional callable (fn). */
    public static class FunctionBindingWithFn {
        public final SolModels.FunctionBinding binding;
        public final Function<List<Object>, Object> fn;
        public FunctionBindingWithFn(SolModels.FunctionBinding binding, Function<List<Object>, Object> fn) {
            this.binding = binding;
            this.fn = fn;
        }
    }

    private final Host host;
    private final Map<String, Function<Map<String, Object>, Object>> compiledCache = new LinkedHashMap<>();

    public ExpressionCompiler(Host host) {
        this.host = host;
    }

    public Function<Map<String, Object>, Object> compileExpression(SolModels.Expression expr) {
        String cacheKey = expr.id + "_" + stableHash(expr);
        Function<Map<String, Object>, Object> cached = compiledCache.get(cacheKey);
        if (cached != null) return cached;
        Function<Map<String, Object>, Object> compiled = compileNode(expr);
        compiledCache.put(cacheKey, compiled);
        return compiled;
    }

    // ── Node compiler ────────────────────────────────────────────

    static Object coerceLiteral(Object value, String returnType) {
        if (value == null) return null;
        String rt = (returnType == null ? "" : returnType).toLowerCase();
        if (List.of("integer", "int", "bigint", "smallint").contains(rt)) {
            double n = toNumber(value);
            if (Double.isNaN(n)) return value;
            return (long) Math.floor(n);
        }
        if (List.of("numeric", "decimal", "double", "double precision", "float", "real").contains(rt)) {
            double n = toNumber(value);
            return Double.isNaN(n) ? value : n;
        }
        if (rt.equals("boolean")) {
            if (value instanceof Boolean b) return b;
            String s = String.valueOf(value);
            if (List.of("true", "True", "t", "1").contains(s)) return true;
            if (List.of("false", "False", "f", "0").contains(s)) return false;
            return value;
        }
        return value;
    }

    private static double toNumber(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        try { return Double.parseDouble(String.valueOf(value)); }
        catch (NumberFormatException e) { return Double.NaN; }
    }

    /** Public NaN-safe numeric coercion used by builtins. */
    public static double toNumberSafe(Object value) {
        return toNumber(value);
    }

    private Function<Map<String, Object>, Object> compileNode(SolModels.Expression expr) {
        switch (expr.kind) {
            case literal: {
                Object val = coerceLiteral(expr.literalValue, expr.returnType);
                return ctx -> val;
            }
            case attribute_ref: {
                SolModels.ConceptAttribute attr = host.getAttribute(expr.attributeId != null ? expr.attributeId : "");
                return ctx -> resolveAttribute(ctx, attr);
            }
            case operator:
                return compileOperator(expr);
            case function_call: {
                FunctionBindingWithFn func = host.getFunction(expr.functionName != null ? expr.functionName : "");
                List<Function<Map<String, Object>, Object>> argFns = new ArrayList<>();
                for (SolModels.Expression op : expr.operands) argFns.add(compileNode(op));
                if (func == null || func.fn == null)
                    throw new IllegalStateException("Unknown function: " + expr.functionName);
                Function<List<Object>, Object> pf = func.fn;
                return ctx -> {
                    List<Object> args = new ArrayList<>();
                    for (Function<Map<String, Object>, Object> af : argFns) args.add(af.apply(ctx));
                    return pf.apply(args);
                };
            }
            case relationship_ref:
                return compileRelationship(expr);
            case proposition_ref: {
                SolModels.Proposition prop = host.getProposition(expr.referencedPropositionId != null ? expr.referencedPropositionId : "");
                String fieldName = expr.propositionRefField;
                if ("value".equals(fieldName)) return ctx -> prop != null ? prop.value : null;
                if ("disposition".equals(fieldName)) return ctx -> prop != null ? prop.disposition : null;
                return ctx -> prop;
            }
            default:
                throw new IllegalStateException("Unsupported expression kind: " + expr.kind);
        }
    }

    private Function<Map<String, Object>, Object> compileOperator(SolModels.Expression expr) {
        Function<Map<String, Object>, Object> leftFn = compileNode(expr.operands.get(0));
        Function<Map<String, Object>, Object> rightFn = expr.operands.size() > 1 ? compileNode(expr.operands.get(1)) : null;
        SolOperator op = expr.operator;
        switch (op) {
            case And: return ctx -> truthy(leftFn.apply(ctx)) && (rightFn != null && truthy(rightFn.apply(ctx)));
            case Or: return ctx -> truthy(leftFn.apply(ctx)) || (rightFn != null && truthy(rightFn.apply(ctx)));
            case Not: return ctx -> !truthy(leftFn.apply(ctx));
            case Eq: return ctx -> java.util.Objects.equals(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null);
            case Neq: return ctx -> !java.util.Objects.equals(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null);
            case Gt: return ctx -> compare(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null) > 0;
            case Lt: return ctx -> compare(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null) < 0;
            case Gte: return ctx -> compare(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null) >= 0;
            case Lte: return ctx -> compare(leftFn.apply(ctx), rightFn != null ? rightFn.apply(ctx) : null) <= 0;
            default: throw new IllegalStateException("Unsupported operator: " + op);
        }
    }

    private Function<Map<String, Object>, Object> compileRelationship(SolModels.Expression expr) {
        SolModels.ConceptRelationship relation = host.getRelationship(expr.conceptRelationshipId != null ? expr.conceptRelationshipId : "");
        SolModels.Expression childExpr = expr.operands.isEmpty() ? null : expr.operands.get(0);
        if (expr.quantifier == Quantifier.Exists)
            return ctx -> checkRelationshipExists(ctx, relation, childExpr);
        if (expr.quantifier == Quantifier.All)
            return ctx -> checkRelationshipAll(ctx, relation, childExpr);
        if (expr.quantifier == Quantifier.Count)
            return ctx -> countRelationship(ctx, relation, childExpr);
        return ctx -> getRelatedEntities(ctx, relation);
    }

    // ── Relationship helpers ──────────────────────────────────────

    private boolean checkRelationshipExists(Map<String, Object> ctx, SolModels.ConceptRelationship relation, SolModels.Expression childExpr) {
        List<SolModels.Entity> related = navigateRelationship(ctx, relation);
        if (related.isEmpty()) return false;
        if (childExpr == null) return true;
        Function<Map<String, Object>, Object> childFn = compileNode(childExpr);
        for (SolModels.Entity entity : related) {
            if (truthy(childFn.apply(childContext(ctx, entity)))) return true;
        }
        return false;
    }

    private boolean checkRelationshipAll(Map<String, Object> ctx, SolModels.ConceptRelationship relation, SolModels.Expression childExpr) {
        List<SolModels.Entity> related = navigateRelationship(ctx, relation);
        if (related.isEmpty()) return true; // vacuously true
        if (childExpr == null) return true;
        Function<Map<String, Object>, Object> childFn = compileNode(childExpr);
        for (SolModels.Entity entity : related) {
            if (!truthy(childFn.apply(childContext(ctx, entity)))) return false;
        }
        return true;
    }

    private int countRelationship(Map<String, Object> ctx, SolModels.ConceptRelationship relation, SolModels.Expression childExpr) {
        List<SolModels.Entity> related = navigateRelationship(ctx, relation);
        if (related.isEmpty()) return 0;
        if (childExpr == null) return related.size();
        Function<Map<String, Object>, Object> childFn = compileNode(childExpr);
        int count = 0;
        for (SolModels.Entity entity : related) {
            if (truthy(childFn.apply(childContext(ctx, entity)))) count++;
        }
        return count;
    }

    private List<SolModels.Entity> getRelatedEntities(Map<String, Object> ctx, SolModels.ConceptRelationship relation) {
        return navigateRelationship(ctx, relation);
    }

    private List<SolModels.Entity> navigateRelationship(Map<String, Object> ctx, SolModels.ConceptRelationship relation) {
        Object raw = ctx.getOrDefault("entity", ctx.getOrDefault("target_entity", ctx.get("subject")));
        if (!(raw instanceof SolModels.Entity entity)) return List.of();
        if (relation == null) return List.of();
        List<SolModels.Entity> related = new ArrayList<>();
        for (SolModels.Entity other : host.entities()) {
            if (other.conceptId.equals(relation.toConceptId) && relationshipExists(entity, other, relation)) {
                related.add(other);
            }
        }
        return related;
    }

    // ── Module-level helpers (pure; shared with query builder) ────

    public static Map<String, Object> childContext(Map<String, Object> ctx, SolModels.Entity entity) {
        Map<String, Object> out = new LinkedHashMap<>(ctx);
        out.put("entity", entity);
        out.put("parent", ctx.get("entity"));
        return out;
    }

    public static boolean relationshipExists(SolModels.Entity fromEntity, SolModels.Entity toEntity, SolModels.ConceptRelationship relation) {
        SolModels.RelationshipBinding binding = relation.binding;
        if (binding != null) {
            Object fromVal = fromEntity.attributes.get(binding.fromColumn());
            Object toVal = toEntity.attributes.get(binding.toColumn());
            return java.util.Objects.equals(fromVal, toVal);
        }
        return false;
    }

    public static Object resolveAttribute(Map<String, Object> ctx, SolModels.ConceptAttribute attr) {
        if (attr == null) return null;
        Object rawEntity = ctx.get("entity");
        if (rawEntity instanceof SolModels.Entity entity) {
            return entity.attributes.getOrDefault(attr.name, null);
        }
        if (ctx.containsKey(attr.name)) return ctx.get(attr.name);
        return null;
    }

    static boolean truthy(Object a) {
        if (a == null) return false;
        if (a instanceof Boolean b) return b;
        if (a instanceof Number n) return n.doubleValue() != 0;
        if (a instanceof String s) return !s.isEmpty();
        return true;
    }

    /** Total-order comparison for >, <, >=, <= over mixed scalars. */
    static int compare(Object a, Object b) {
        if (a instanceof Number an && b instanceof Number bn) {
            double d = an.doubleValue() - bn.doubleValue();
            return d < 0 ? -1 : d > 0 ? 1 : 0;
        }
        String sa = String.valueOf(a);
        String sb = String.valueOf(b);
        return sa.compareTo(sb);
    }

    /** Deterministic structural hash for expression cache keys. */
    public static String stableHash(Object value) {
        return StableJsonCompat.stringify(value);
    }
}