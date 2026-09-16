package org.nexus.solscript.models;

/**
 * SOLScript domain models — faithful port of typescript/solscript/src/models.ts.
 *
 * JsonValue is represented as Java {@link Object}: null, Boolean, Number,
 * String, List<Object>, or Map<String,Object> (Jackson's natural JSON tree).
 */

public final class SolModels {
    private SolModels() {}

    // ── AttributeBinding ────────────────────────────────────────────
    public record AttributeBinding(String schemaName, String tableName, String columnName) {}

    public static class ConceptAttribute {
        public String id;
        public String conceptId;
        public String name;
        public String description;
        public String valueType;
        public boolean isStateAttribute;
        public AttributeBinding binding;
        public java.util.List<String> allowedValues = new java.util.ArrayList<>();
        public Object defaultValue;
    }

    public record RelationshipBinding(String fromSchema, String fromTable, String fromColumn,
                                      String toSchema, String toTable, String toColumn) {}

    public static class Expression {
        public String id;
        public ExpressionKind kind;
        public String returnType;
        public SolOperator operator;
        public Object literalValue;
        public String attributeId;
        public String functionName;
        public String conceptRelationshipId;
        public Quantifier quantifier;
        public String referencedPropositionId;
        public String propositionRefField;
        public java.util.List<Expression> operands = new java.util.ArrayList<>();
        public String label;
    }

    public static class Rule {
        public String id;
        public String name;
        public RuleType ruleType;
        public Expression expression;
        public Severity severity;
        public String conceptId;
        public String conceptRelationshipId;
        public String representationId;
        public String stateTransitionId;
        public String notes;
        public boolean isRelationalCheck;
        public String conceptAttributeId;
        public String conclusionAttributeId;
        public Object conclusionValue;
        public java.util.List<Expression> conditions = new java.util.ArrayList<>();
    }

    public static class ConceptRelationship {
        public String id;
        public String fromConceptId;
        public String toConceptId;
        public String relationshipType;
        public String path;
        public String notes;
        public RelationshipBinding binding;
        public java.util.List<Rule> conditionals = new java.util.ArrayList<>();
        public String name;
    }

    public static class ConceptStateTransition {
        public String id;
        public String conceptId;
        public String fromValue;
        public String toValue;
        public String name;
        public String notes;
        public java.util.List<Rule> guards = new java.util.ArrayList<>();
    }

    public static class Concept {
        public String id;
        public String name;
        public String description;
        public java.util.LinkedHashMap<String, ConceptAttribute> attributes = new java.util.LinkedHashMap<>();
        public java.util.LinkedHashMap<String, ConceptRelationship> relationships = new java.util.LinkedHashMap<>();
        public java.util.List<Rule> invariants = new java.util.ArrayList<>();
        public java.util.List<Rule> derivations = new java.util.ArrayList<>();
        public java.util.List<ConceptStateTransition> stateTransitions = new java.util.ArrayList<>();
        public java.util.List<Rule> rules = new java.util.ArrayList<>();
    }

    public static class Entity {
        public String id;
        public String conceptId;
        public java.util.LinkedHashMap<String, Object> attributes = new java.util.LinkedHashMap<>();
        public String externalId;
        public String assetId;
    }

    // ── Representations ──────────────────────────────────────────────
    public record RepresentationIdentity(String id, String representationId,
                                         String identityStrategyId, String identityExpression) {}

    public record RepresentationComparison(String id, String representationRelationshipId,
                                           String fromColumn, String toColumn, String notes) {}

    public static class Representation {
        public String id;
        public String conceptId;
        public String label;
        public String schemaName;
        public String tableName;
        public Integer owningSubsystemId;
        public String owner;
        public java.util.LinkedHashMap<String, Object> rawMetadata = new java.util.LinkedHashMap<>();
        public RepresentationIdentity identity;
        public java.util.List<Rule> rules = new java.util.ArrayList<>();
    }

    // ── Frame discipline (v31/v35) ───────────────────────────────────
    public static class FrameDimension {
        public String id;
        public String name;
        public String description;
        public String valueKind;
        public String scalarType;
    }

    public static class FrameDimensionValue {
        public String id;
        public String dimensionId;
        public String value;
        public String description;
    }

    public static class PropositionFrameValue {
        public String id;
        public String propositionId;
        public String dimensionId;
        public String referenceValueId;
        public String scalarValue;
    }

    public static class FrameDimensionMeaning {
        public String id;
        public String propositionId;
        public String dimensionId;
        public String frameDimensionValueId;
    }

    public static class Proposition {
        public String id;
        public String title;
        public String description;
        public String assetConceptId;
        public String subjectEntityId;
        public Disposition disposition;
        public Boolean value;
        public String groundingStatus;
        public java.util.List<Rule> assertions = new java.util.ArrayList<>();
        public java.util.List<RepresentationComparison> comparisons = new java.util.ArrayList<>();
        public String lastEvaluatedAt;
        public String semanticTypeId;
        public java.util.List<PropositionFrameValue> frameValues = new java.util.ArrayList<>();
    }

    /** FunctionBinding: executable binding (python_func is not wire data). */
    public record FunctionBinding(String functionName, String sqlTemplate, int argCount,
                                  String returnType, String notes) {}
}