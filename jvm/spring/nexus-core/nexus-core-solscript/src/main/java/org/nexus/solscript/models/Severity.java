package org.nexus.solscript.models;

/** Rule severity: hard rules fail evaluation to false; soft rules fail open. */
public enum Severity {
    hard("hard"),
    soft("soft");

    private final String value;
    Severity(String value) { this.value = value; }
    public String value() { return value; }
    public static Severity from(String v) {
        for (Severity s : values()) if (s.value.equals(v)) return s;
        throw new IllegalArgumentException("Unknown severity: " + v);
    }
}