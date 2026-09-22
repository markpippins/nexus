package org.nexus.core.shrapnel;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Shrapnel type registry — mirror of the {@code shrapnel.field_type} table and
 * port of {@code typescript/shrapnel/src/lib/types.js}. Codes are authoritative
 * (see the TS service's migrations/0001_init.sql and the shared TypeSpec enum
 * {@code typespec/v1/shrapnel/main.tsp}).
 */
public final class ShrapnelTypes {

    private ShrapnelTypes() {}

    // TYPE_CODES (name -> code)
    public static final int LONG = 1;
    public static final int STRING = 2;
    public static final int DOUBLE = 3;
    public static final int BOOLEAN = 4;
    public static final int TIMESTAMP = 5;
    public static final int JSONB = 6;
    public static final int UUID = 7;

    /** name -> code (insertion order = canonical order from types.js). */
    public static final java.util.LinkedHashMap<String, Integer> TYPE_CODES = new java.util.LinkedHashMap<>();
    /** code -> name. */
    public static final java.util.LinkedHashMap<Integer, String> TYPE_NAMES = new java.util.LinkedHashMap<>();
    /** code -> extension table (value_long, value_string, ...). */
    public static final java.util.LinkedHashMap<Integer, String> EXTENSION_TABLES = new java.util.LinkedHashMap<>();

    static {
        register("Long", 1, "value_long");
        register("String", 2, "value_string");
        register("Double", 3, "value_double");
        register("Boolean", 4, "value_boolean");
        register("Timestamp", 5, "value_timestamp");
        register("JSONB", 6, "value_jsonb");
        register("UUID", 7, "value_uuid");
    }

    private static void register(String name, int code, String table) {
        TYPE_CODES.put(name, code);
        TYPE_NAMES.put(code, name);
        EXTENSION_TABLES.put(code, table);
    }

    public static boolean isKnownTypeName(String name) {
        return name != null && TYPE_CODES.containsKey(name);
    }

    /** Port of assertKnownTypeName — throws ShrapnelApiException(400) on unknown. */
    public static int assertKnownTypeName(String name) {
        Integer code = name == null ? null : TYPE_CODES.get(name);
        if (code == null) {
            throw new ShrapnelApiException(400, "unknown type '" + name + "'. Valid: "
                    + String.join(", ", TYPE_CODES.keySet()));
        }
        return code;
    }

    public static String typeName(int code) {
        String name = TYPE_NAMES.get(code);
        if (name == null) {
            throw new ShrapnelApiException(400, "unknown field_type_code " + code);
        }
        return name;
    }

    public static String extensionTable(int code) {
        String table = EXTENSION_TABLES.get(code);
        if (table == null) {
            throw new ShrapnelApiException(400, "unknown type code " + code);
        }
        return table;
    }

    // ── identifier guard (port of assertIdentifier) ─────────────────────────

    private static final java.util.regex.Pattern IDENT_RE =
            java.util.regex.Pattern.compile("^[a-zA-Z_][a-zA-Z0-9_]*$");

    /** Guard so formatted SQL cannot be injected via table/param names. */
    public static void assertIdentifier(String name) {
        if (name == null || !IDENT_RE.matcher(name).matches()) {
            throw new ShrapnelApiException(400, "invalid identifier: " + name);
        }
    }

    // ── type inference (port of inferTypeName) ──────────────────────────────

    private static final java.util.regex.Pattern ISO_TS =
            java.util.regex.Pattern.compile("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}");
    private static final java.util.regex.Pattern UUID_RE = java.util.regex.Pattern.compile(
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    /** Port of inferTypeName over a decoded JSON value. */
    public static String inferTypeName(Object value) {
        if (value == null) {
            throw new ShrapnelApiException(400, "cannot infer type from null/undefined");
        }
        if (value instanceof Boolean) return "Boolean";
        if (value instanceof Integer || value instanceof Long) return "Long";
        if (value instanceof Float || value instanceof Double) return "Double";
        if (value instanceof String s) {
            if (ISO_TS.matcher(s).find() && canParseInstant(s)) return "Timestamp";
            if (UUID_RE.matcher(s).matches()) return "UUID";
            return "String";
        }
        if (value instanceof java.util.Map || value instanceof java.util.List) return "JSONB";
        throw new ShrapnelApiException(400, "cannot infer shrapnel type for " + value.getClass().getSimpleName());
    }

    private static boolean canParseInstant(String s) {
        try {
            Instant.parse(s);
            return true;
        } catch (Exception e) {
            try {
                OffsetDateTime.parse(s);
                return true;
            } catch (Exception e2) {
                return false;
            }
        }
    }

    // ── coercion (ports of coerceForStorage / coerceFromStorage) ────────────

    /** Coerce a JSON-decoded value into the storage form for the extension table. */
    public static Object coerceForStorage(Object value, int typeCode) {
        return switch (typeCode) {
            case LONG -> value instanceof Number n
                    ? Long.valueOf(n.longValue()) : Long.parseLong(String.valueOf(value));
            case STRING -> value instanceof String s ? s : String.valueOf(value);
            case DOUBLE -> value instanceof Number n ? n.doubleValue() : Double.parseDouble(String.valueOf(value));
            case BOOLEAN -> value instanceof Boolean b ? b : Boolean.parseBoolean(String.valueOf(value));
            case TIMESTAMP -> {
                if (value instanceof OffsetDateTime odt) yield odt.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME);
                if (value instanceof Instant inst) yield inst.toString();
                yield String.valueOf(value);
            }
            case JSONB -> value;
            case UUID -> String.valueOf(value);
            default -> throw new ShrapnelApiException(400, "unknown type code " + typeCode);
        };
    }

    /** Reverse-direction coercion: storage text -> JSON-serialisable value. */
    public static Object coerceFromStorage(String raw, int typeCode) {
        if (raw == null) return null;
        return switch (typeCode) {
            case LONG -> Long.parseLong(raw);
            case DOUBLE -> Double.parseDouble(raw);
            case BOOLEAN -> {
                if ("true".equals(raw) || "t".equals(raw)) yield Boolean.TRUE;
                if ("false".equals(raw) || "f".equals(raw)) yield Boolean.FALSE;
                yield Boolean.parseBoolean(raw);
            }
            case TIMESTAMP -> {
                if (ISO_TS.matcher(raw).find()) yield raw;
                // PG text form uses a space separator and short offsets
                // ("2026-09-20 12:00:00+00"); normalise to ISO-8601 UTC (the
                // TS path relies on JS Date's lenient parsing for this).
                try {
                    String iso = raw.replace(' ', 'T');
                    // Short offset like "+00" -> append ":00" (no regex, to
                    // avoid Java string-escape pitfalls).
                    if (iso.length() >= 3) {
                        char sign = iso.charAt(iso.length() - 3);
                        boolean twoDigits = Character.isDigit(iso.charAt(iso.length() - 2))
                                && Character.isDigit(iso.charAt(iso.length() - 1));
                        if ((sign == '+' || sign == '-') && twoDigits) {
                            iso = iso + ":00";
                        }
                    }
                    yield OffsetDateTime.parse(iso).toInstant().toString();
                } catch (Exception e) {
                    yield raw;
                }
            }
            case JSONB -> raw; // JSONB arrives pre-parsed via PGobject/Map handling in the store
            default -> raw;
        };
    }
}
