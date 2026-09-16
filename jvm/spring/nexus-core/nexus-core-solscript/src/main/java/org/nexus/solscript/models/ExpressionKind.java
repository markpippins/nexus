package org.nexus.solscript.models;

/** Expression tree node kinds (values identical to the Python reference). */
public enum ExpressionKind {
    literal("literal"),
    attribute_ref("attribute_ref"),
    operator("operator"),
    function_call("function_call"),
    relationship_ref("relationship_ref"),
    proposition_ref("proposition_ref");

    private final String value;
    ExpressionKind(String value) { this.value = value; }
    public String value() { return value; }
    public static ExpressionKind from(String v) {
        for (ExpressionKind k : values()) if (k.value.equals(v)) return k;
        throw new IllegalArgumentException("Unknown expression kind: " + v);
    }
}