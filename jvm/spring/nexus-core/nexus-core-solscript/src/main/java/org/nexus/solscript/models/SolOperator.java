package org.nexus.solscript.models;

/** Comparison/boolean operators (values identical to the Python reference). */
public enum SolOperator {
    Eq("="),
    Neq("<>"),
    Gt(">"),
    Lt("<"),
    Gte(">="),
    Lte("<="),
    And("AND"),
    Or("OR"),
    Not("NOT");

    private final String value;
    SolOperator(String value) { this.value = value; }
    public String value() { return value; }
    public static SolOperator from(String v) {
        for (SolOperator o : values()) if (o.value.equals(v)) return o;
        throw new IllegalArgumentException("Unknown operator: " + v);
    }
}