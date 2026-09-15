/**
 * SOLScript TypeScript core — data models.
 *
 * Ported from python/SOLScript/solscript/models.py (deterministic core).
 * Field casing is camelCase per the TypeSpec wire contract
 * (typespec/v1/solscript/python/models.tsp); enum string values are
 * IDENTICAL to the Python reference (parity requirement). The hybrid
 * techniques reasoning lane is excluded by operator directive.
 */
// ── Enums (values identical to Python) ───────────────────────────────
export var ExpressionKind;
(function (ExpressionKind) {
    ExpressionKind["Literal"] = "literal";
    ExpressionKind["AttributeRef"] = "attribute_ref";
    ExpressionKind["Operator"] = "operator";
    ExpressionKind["FunctionCall"] = "function_call";
    ExpressionKind["RelationshipRef"] = "relationship_ref";
    ExpressionKind["PropositionRef"] = "proposition_ref";
})(ExpressionKind || (ExpressionKind = {}));
export var SolOperator;
(function (SolOperator) {
    SolOperator["Eq"] = "=";
    SolOperator["Neq"] = "<>";
    SolOperator["Gt"] = ">";
    SolOperator["Lt"] = "<";
    SolOperator["Gte"] = ">=";
    SolOperator["Lte"] = "<=";
    SolOperator["And"] = "AND";
    SolOperator["Or"] = "OR";
    SolOperator["Not"] = "NOT";
})(SolOperator || (SolOperator = {}));
export var Quantifier;
(function (Quantifier) {
    Quantifier["Exists"] = "EXISTS";
    Quantifier["All"] = "ALL";
    Quantifier["Count"] = "COUNT";
})(Quantifier || (Quantifier = {}));
export var RuleType;
(function (RuleType) {
    RuleType["Invariant"] = "invariant";
    RuleType["Guard"] = "guard";
    RuleType["Conditional"] = "conditional";
    RuleType["Derivation"] = "derivation";
})(RuleType || (RuleType = {}));
export var Severity;
(function (Severity) {
    Severity["Hard"] = "hard";
    Severity["Soft"] = "soft";
})(Severity || (Severity = {}));
export var Disposition;
(function (Disposition) {
    Disposition["Asserted"] = "Asserted";
    Disposition["Disputed"] = "Disputed";
    Disposition["Rejected"] = "Rejected";
    Disposition["Pending"] = "Pending";
    Disposition["Proposed"] = "Proposed";
    Disposition["Stale"] = "Stale";
    Disposition["Retracted"] = "Retracted";
})(Disposition || (Disposition = {}));
//# sourceMappingURL=models.js.map