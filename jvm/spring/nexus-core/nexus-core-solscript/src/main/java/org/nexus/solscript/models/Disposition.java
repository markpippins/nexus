package org.nexus.solscript.models;

/**
 * Proposition disposition (values IDENTICAL to Python — capitalized, as
 * serialized: Disposition.Asserted -> "Asserted").
 */
public enum Disposition {
    Asserted("Asserted"),
    Disputed("Disputed"),
    Rejected("Rejected"),
    Pending("Pending"),
    Proposed("Proposed"),
    Stale("Stale"),
    Retracted("Retracted");

    private final String value;
    Disposition(String value) { this.value = value; }
    public String value() { return value; }
    public static Disposition from(String v) {
        for (Disposition d : values()) if (d.value.equals(v)) return d;
        throw new IllegalArgumentException("Unknown disposition: " + v);
    }
}