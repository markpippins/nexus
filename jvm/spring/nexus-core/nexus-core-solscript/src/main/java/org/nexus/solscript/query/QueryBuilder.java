package org.nexus.solscript.query;

import org.nexus.solscript.compiler.ExpressionCompiler;
import org.nexus.solscript.interpreter.ResolutionInterpreter;
import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.models.ExpressionKind;
import org.nexus.solscript.models.SolOperator;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Query builder + transaction context — faithful port of
 * typescript/solscript/src/query-builder.ts.
 */
public class QueryBuilder {

    private final ResolutionInterpreter interpreter;

    public QueryBuilder(ResolutionInterpreter interpreter) {
        this.interpreter = interpreter;
    }

    public Query select(String conceptName) {
        SolModels.Concept concept = interpreter.getConceptByName(conceptName);
        if (concept == null) throw new IllegalStateException("Concept not found: " + conceptName);
        return new Query(interpreter, concept);
    }

    /** Fluent query interface over a single concept. */
    public static class Query {
        private final List<SolModels.Expression> filters = new ArrayList<>();
        private final List<Object[]> orderBys = new ArrayList<>(); // {attribute, direction}
        private Integer limit = null;
        private Integer offset = null;
        private List<String> selectFields = new ArrayList<>();

        private final ResolutionInterpreter interpreter;
        private final SolModels.Concept concept;

        Query(ResolutionInterpreter interpreter, SolModels.Concept concept) {
            this.interpreter = interpreter;
            this.concept = concept;
        }

        public Query filter(SolModels.Expression condition) {
            filters.add(condition);
            return this;
        }

        public Query where(String attribute, SolOperator op, Object value) {
            SolModels.ConceptAttribute attr = null;
            for (SolModels.ConceptAttribute a : concept.attributes.values()) {
                if (a.name.equals(attribute)) { attr = a; break; }
            }
            if (attr == null) throw new IllegalStateException("Attribute not found: " + attribute);

            SolModels.Expression attrExpr = new SolModels.Expression();
            attrExpr.id = UUID.randomUUID().toString();
            attrExpr.kind = ExpressionKind.attribute_ref;
            attrExpr.returnType = attr.valueType;
            attrExpr.attributeId = attr.id;

            SolModels.Expression literalExpr = new SolModels.Expression();
            literalExpr.id = UUID.randomUUID().toString();
            literalExpr.kind = ExpressionKind.literal;
            literalExpr.returnType = attr.valueType;
            literalExpr.literalValue = value;

            SolModels.Expression opExpr = new SolModels.Expression();
            opExpr.id = UUID.randomUUID().toString();
            opExpr.kind = ExpressionKind.operator;
            opExpr.returnType = "boolean";
            opExpr.operator = op;
            opExpr.operands.add(attrExpr);
            opExpr.operands.add(literalExpr);

            filters.add(opExpr);
            return this;
        }

        public Query orderBy(String attribute, String direction) {
            orderBys.add(new Object[] { attribute, direction == null ? "ASC" : direction });
            return this;
        }

        public Query limitN(int n) { this.limit = n; return this; }
        public Query offsetN(int n) { this.offset = n; return this; }
        public Query selectFieldsTo(String... fields) { this.selectFields = List.of(fields); return this; }

        public List<Map<String, Object>> execute() {
            ExpressionCompiler compiler = new ExpressionCompiler(interpreter.host());
            List<SolModels.Entity> entities = new ArrayList<>();
            for (SolModels.Entity e : interpreter.entities.values()) {
                if (e.conceptId.equals(concept.id)) entities.add(e);
            }

            List<Map<String, Object>> results = new ArrayList<>();
            for (SolModels.Entity entity : entities) {
                Map<String, Object> ctx = contextOf(entity);
                boolean passed = true;
                for (SolModels.Expression fExpr : filters) {
                    try {
                        var compiled = compiler.compileExpression(fExpr);
                        if (!truthy(compiled.apply(ctx))) { passed = false; break; }
                    } catch (Exception e) { passed = false; break; }
                }
                if (!passed) continue;

                if (!selectFields.isEmpty()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    for (String field : selectFields) {
                        if (entity.attributes.containsKey(field)) {
                            row.put(field, entity.attributes.get(field));
                        } else if ("id".equals(field)) {
                            row.put("id", entity.id);
                        } else if ("external_id".equals(field)) {
                            row.put("external_id", entity.externalId);
                        }
                    }
                    results.add(row);
                } else {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", entity.id);
                    row.put("external_id", entity.externalId);
                    row.putAll(entity.attributes);
                    results.add(row);
                }
            }

            // Python applies order_bys in reversed() order (last wins).
            List<Object[]> reversed = new ArrayList<>(orderBys);
            java.util.Collections.reverse(reversed);
            for (Object[] ob : reversed) {
                String attribute = (String) ob[0];
                boolean reverse = ((String) ob[1]).toUpperCase().equals("DESC");
                results.sort((a, b) -> {
                    Object av = a.getOrDefault(attribute, "");
                    Object bv = b.getOrDefault(attribute, "");
                    int cmp;
                    if (av instanceof Number an && bv instanceof Number bn) {
                        cmp = Double.compare(an.doubleValue(), bn.doubleValue());
                    } else {
                        cmp = String.valueOf(av).compareTo(String.valueOf(bv));
                    }
                    return reverse ? -cmp : cmp;
                });
            }

            if (offset != null) {
                int off = Math.min(offset, results.size());
                for (int i = 0; i < off; i++) results.remove(0);
            }
            if (limit != null) {
                while (results.size() > limit) results.remove(results.size() - 1);
            }
            return results;
        }

        public int count() {
            return execute().size();
        }
    }

    public static boolean truthy(Object a) {
        if (a == null) return false;
        if (a instanceof Boolean b) return b;
        if (a instanceof Number n) return n.doubleValue() != 0;
        if (a instanceof String s) return !s.isEmpty();
        return true;
    }

    static Map<String, Object> contextOf(SolModels.Entity entity) {
        Map<String, Object> ctx = new LinkedHashMap<>();
        ctx.put("entity", entity);
        return ctx;
    }

    // ── Transaction context ─────────────────────────────────────────

    public static class ChangeRecord {
        public final String type;
        public final Map<String, Object> data;
        public final String timestamp;
        public ChangeRecord(String type, Map<String, Object> data, String timestamp) {
            this.type = type;
            this.data = data;
            this.timestamp = timestamp;
        }
    }

    /** Snapshot/rollback/commit semantics (port of TS TransactionContext). */
    public static class TransactionContext {
        public final List<ChangeRecord> changes = new ArrayList<>();
        private Map<String, Object> snapshot = null;

        private final ResolutionInterpreter interpreter;

        public TransactionContext(ResolutionInterpreter interpreter) {
            this.interpreter = interpreter;
        }

        /** begin — capture the snapshot. */
        public TransactionContext begin() {
            Map<String, Object> snap = new LinkedHashMap<>();
            snap.put("entities", deepCopy(interpreter.entities));
            snap.put("propositions", deepCopy(interpreter.propositions));
            snap.put("evaluationCache", new LinkedHashMap<>(interpreter.evaluationCache));
            this.snapshot = snap;
            return this;
        }

        public void addChange(String changeType, Map<String, Object> data) {
            changes.add(new ChangeRecord(changeType, data, java.time.OffsetDateTime.now().toString()));
        }

        @SuppressWarnings("unchecked")
        public void rollback() {
            if (snapshot == null) throw new IllegalStateException("TransactionContext not started");
            interpreter.entities.clear();
            interpreter.entities.putAll((Map<String, SolModels.Entity>) snapshot.get("entities"));
            interpreter.propositions.clear();
            interpreter.propositions.putAll((Map<String, SolModels.Proposition>) snapshot.get("propositions"));
            interpreter.evaluationCache.clear();
            interpreter.evaluationCache.putAll((Map<String, Object>) snapshot.get("evaluationCache"));
            changes.clear();
        }

        public void commit() {
            for (ChangeRecord change : changes) {
                if ("entity_update".equals(change.type)) {
                    SolModels.Entity entity = (SolModels.Entity) change.data.get("entity");
                    interpreter.entities.put(entity.id, entity);
                    SolModels.Concept concept = interpreter.getConcept(entity.conceptId);
                    if (concept != null) interpreter.onChange(concept.name, entity.id);
                } else if ("proposition_update".equals(change.type)) {
                    SolModels.Proposition prop = (SolModels.Proposition) change.data.get("proposition");
                    interpreter.propositions.put(prop.id, prop);
                }
            }
            changes.clear();
        }
    }

    // ── Deep copy (port of structuredClone) ────────────────────────

    @SuppressWarnings("unchecked")
    public static <K, V> Map<K, V> deepCopy(Map<K, V> src) {
        Map<K, V> out = new LinkedHashMap<>();
        for (Map.Entry<K, V> e : src.entrySet()) {
            out.put(e.getKey(), (V) cloneValue(e.getValue()));
        }
        return out;
    }

    static Object cloneValue(Object v) {
        if (v == null) return null;
        if (v instanceof SolModels.Entity e) {
            SolModels.Entity c = new SolModels.Entity();
            c.id = e.id;
            c.conceptId = e.conceptId;
            c.externalId = e.externalId;
            c.assetId = e.assetId;
            c.attributes = new LinkedHashMap<>(e.attributes);
            return c;
        }
        if (v instanceof SolModels.Proposition p) {
            SolModels.Proposition c = new SolModels.Proposition();
            c.id = p.id;
            c.title = p.title;
            c.description = p.description;
            c.assetConceptId = p.assetConceptId;
            c.subjectEntityId = p.subjectEntityId;
            c.disposition = p.disposition;
            c.value = p.value;
            c.groundingStatus = p.groundingStatus;
            c.assertions = new ArrayList<>(p.assertions);
            c.comparisons = new ArrayList<>(p.comparisons);
            c.lastEvaluatedAt = p.lastEvaluatedAt;
            c.semanticTypeId = p.semanticTypeId;
            c.frameValues = new ArrayList<>(p.frameValues);
            return c;
        }
        if (v instanceof Map<?, ?> m) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : m.entrySet()) out.put(String.valueOf(e.getKey()), cloneValue(e.getValue()));
            return out;
        }
        if (v instanceof List<?> l) {
            List<Object> out = new ArrayList<>();
            for (Object item : l) out.add(cloneValue(item));
            return out;
        }
        return v;
    }
}