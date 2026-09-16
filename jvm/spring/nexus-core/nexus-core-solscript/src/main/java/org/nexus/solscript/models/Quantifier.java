package org.nexus.solscript.models;

/** Relationship quantifiers. */
public enum Quantifier {
    Exists("EXISTS"),
    All("ALL"),
    Count("COUNT");

    private final String value;
    Quantifier(String value) { this.value = value; }
    public String value() { return value; }
    public static Quantifier from(String v) {
        for (Quantifier q : values()) if (q.value.equals(v)) return q;
        throw new IllegalArgumentException("Unknown quantifier: " + v);
    }
}