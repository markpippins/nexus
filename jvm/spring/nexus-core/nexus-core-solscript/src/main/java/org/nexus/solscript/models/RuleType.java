package org.nexus.solscript.models;

/** Rule type vocabulary. */
public enum RuleType {
    invariant("invariant"),
    guard("guard"),
    conditional("conditional"),
    derivation("derivation");

    private final String value;
    RuleType(String value) { this.value = value; }
    public String value() { return value; }
    public static RuleType from(String v) {
        for (RuleType t : values()) if (t.value.equals(v)) return t;
        throw new IllegalArgumentException("Unknown rule type: " + v);
    }
}