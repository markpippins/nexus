package org.nexus.core.shrapnel;

import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.sql.Timestamp;
import java.sql.Types;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Port of {@code typescript/shrapnel/src/lib/encode.js} — encode a payload into
 * the shrapnel EAV store and decode an object back to JSON.
 *
 * <p>All SQL mirrors the TS service statement-for-statement (schema-qualified
 * {@code shrapnel.*}), executed inside a Spring-managed transaction replacing
 * the TS {@code withTransaction} helper.</p>
 */
public final class ShrapnelCodec {

    private ShrapnelCodec() {}

    // ── field spec normalisation (port of normaliseFieldSpec) ───────────────

    @SuppressWarnings("unchecked")
    public static Map<String, Object> normaliseFieldSpec(Object entry) {
        if (!(entry instanceof Map)) {
            throw ShrapnelApiException.badRequest("field spec must be an object");
        }
        Map<String, Object> e = (Map<String, Object>) entry;
        String propertyName = str(e.get("property_name"));
        if (propertyName == null) propertyName = str(e.get("propertyName"));
        if (propertyName == null || propertyName.isEmpty()) {
            throw ShrapnelApiException.badRequest("field spec requires property_name");
        }
        Integer typeCode = intOrNull(e.get("field_type_code"));
        if (typeCode == null) typeCode = intOrNull(e.get("fieldTypeCode"));
        if (typeCode == null) {
            String typeName = str(e.get("type"));
            if (typeName == null) typeName = str(e.get("type_name"));
            if (typeName == null) typeName = str(e.get("typeName"));
            if (typeName == null) {
                throw ShrapnelApiException.badRequest("field spec '" + propertyName + "' missing type");
            }
            typeCode = ShrapnelTypes.assertKnownTypeName(typeName);
        } else if (ShrapnelTypes.TYPE_NAMES.get(typeCode) == null) {
            throw ShrapnelApiException.badRequest("unknown field_type_code " + typeCode);
        }
        Object isCalcRaw = firstNonNull(e.get("is_calculated"), e.get("isCalculated"));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("is_calculated", isCalcRaw != null && "true".equals(String.valueOf(isCalcRaw).toLowerCase()));
        Object idx = firstNonNull(e.get("field_index"), e.get("fieldIndex"));
        out.put("field_index", idx instanceof Number ? ((Number) idx).intValue() : 0);
        Object label = firstNonNull(e.get("label"), e.get("property_label"));
        out.put("label", label);
        Object name = e.get("name");
        out.put("name", name != null ? name : propertyName);
        out.put("property_name", propertyName);
        out.put("field_type_code", typeCode);
        return out;
    }

    // ── encode (port of encodePayload) ──────────────────────────────────────

    /** Result record mirroring the TS return shape { object_id, fields }. */
    public record EncodeResult(long objectId, List<Map<String, Object>> fields) {}

    @SuppressWarnings("unchecked")
    public static EncodeResult encodePayload(JdbcTemplate jdbc, TransactionTemplate tx, Object body) {
        if (!(body instanceof Map)) {
            throw ShrapnelApiException.badRequest("body must be a JSON object");
        }
        Map<String, Object> b = (Map<String, Object>) body;

        return tx.execute(status -> {
            Map<String, Object> values = b.containsKey("values") && b.get("values") instanceof Map
                    ? (Map<String, Object>) b.get("values") : b;
            if (values == null || values.isEmpty()) {
                throw ShrapnelApiException.badRequest("values must be a JSON object");
            }
            // Arrays are not valid value maps (TS: Array.isArray check).
            if (b.containsKey("values") && b.get("values") instanceof java.util.List) {
                throw ShrapnelApiException.badRequest("values must be a JSON object");
            }

            // ---- STEP 1: resolve / upsert field metadata ----
            List<Map<String, Object>> fieldSpecs = new ArrayList<>();
            if (b.get("fields") instanceof List<?> rawFields && !((List<?>) rawFields).isEmpty()) {
                int idx = 1;
                for (Object o : (List<Object>) rawFields) {
                    Map<String, Object> f = normaliseFieldSpec(o);
                    int fi = (Integer) f.get("field_index");
                    if (fi == 0) {
                        f.put("field_index", idx);
                    }
                    idx += 1;
                    fieldSpecs.add(f);
                }
            } else {
                // Infer fields from values, in insertion order (LinkedHashMap preserved).
                int idx = 1;
                for (Map.Entry<String, Object> en : values.entrySet()) {
                    String propName = en.getKey();
                    String typeName = ShrapnelTypes.inferTypeName(en.getValue());
                    Map<String, Object> f = new LinkedHashMap<>();
                    f.put("is_calculated", false);
                    f.put("field_index", idx++);
                    f.put("label", propName);
                    f.put("name", propName);
                    f.put("property_name", propName);
                    f.put("field_type_code", ShrapnelTypes.TYPE_CODES.get(typeName));
                    fieldSpecs.add(f);
                }
            }

            Map<String, Long> fieldIds = new LinkedHashMap<>();
            for (Map<String, Object> f : fieldSpecs) {
                KeyHolder kh = new GeneratedKeyHolder();
                jdbc.update(con -> {
                    PreparedStatement ps = con.prepareStatement(
                            "INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)"
                                    + " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (property_name) DO UPDATE SET name = EXCLUDED.name",
                            Statement.RETURN_GENERATED_KEYS);
                    ps.setBoolean(1, (Boolean) f.get("is_calculated"));
                    ps.setInt(2, (Integer) f.get("field_index"));
                    Object label = f.get("label");
                    if (label == null) ps.setNull(3, Types.VARCHAR); else ps.setString(3, String.valueOf(label));
                    ps.setString(4, String.valueOf(f.get("name")));
                    ps.setString(5, String.valueOf(f.get("property_name")));
                    ps.setInt(6, (Integer) f.get("field_type_code"));
                    return ps;
                }, kh);
                fieldIds.put((String) f.get("property_name"), kh.getKey().longValue());
            }

            // ---- STEP 2: create object_instance ----
            KeyHolder oiKh = new GeneratedKeyHolder();
            jdbc.update(con -> con.prepareStatement(
                    "INSERT INTO shrapnel.object_instance DEFAULT VALUES",
                    Statement.RETURN_GENERATED_KEYS), oiKh);
            long objectId = oiKh.getKey().longValue();

            // ---- STEP 3: for each value: value + value_<type> + OAV ----
            for (Map<String, Object> f : fieldSpecs) {
                String propName = (String) f.get("property_name");
                if (!values.containsKey(propName)) continue;
                Object rawValue = values.get(propName);
                if (rawValue == null) continue; // NULLs not allowed in extension tables
                int typeCode = (Integer) f.get("field_type_code");
                String table = ShrapnelTypes.extensionTable(typeCode);
                ShrapnelTypes.assertIdentifier(table);
                Object storageValue = ShrapnelTypes.coerceForStorage(rawValue, typeCode);

                KeyHolder vKh = new GeneratedKeyHolder();
                jdbc.update(con -> {
                    PreparedStatement ps = con.prepareStatement(
                            "INSERT INTO shrapnel.value (value_type_code) VALUES (?)",
                            Statement.RETURN_GENERATED_KEYS);
                    ps.setInt(1, typeCode);
                    return ps;
                }, vKh);
                long valueId = vKh.getKey().longValue();

                jdbc.update(con -> {
                    PreparedStatement ps = con.prepareStatement(
                            "INSERT INTO shrapnel." + table + " (id, value) VALUES (?, ?)");
                    ps.setLong(1, valueId);
                    setStorageValue(ps, 2, storageValue, typeCode);
                    return ps;
                });
                jdbc.update(
                        "INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id) VALUES (?, ?, ?)",
                        objectId, fieldIds.get(propName), valueId);
            }

            return new EncodeResult(objectId, fieldSpecs);
        });
    }

    private static void setStorageValue(PreparedStatement ps, int idx, Object v, int typeCode) {
        try {
            switch (typeCode) {
            case ShrapnelTypes.LONG -> ps.setLong(idx, ((Number) v).longValue());
            case ShrapnelTypes.STRING, ShrapnelTypes.UUID -> ps.setString(idx, String.valueOf(v));
            case ShrapnelTypes.DOUBLE -> ps.setDouble(idx, ((Number) v).doubleValue());
            case ShrapnelTypes.BOOLEAN -> ps.setBoolean(idx, (Boolean) v);
            case ShrapnelTypes.TIMESTAMP -> ps.setTimestamp(idx, Timestamp.from(Instant_of(String.valueOf(v)).toInstant()));
            case ShrapnelTypes.JSONB -> {
                // Cast the JSON string to jsonb in SQL: value_jsonb.value is jsonb.
                ps.setString(idx, String.valueOf(v));
            }
            default -> throw ShrapnelApiException.badRequest("unknown type code " + typeCode);
            }
        } catch (java.sql.SQLException | RuntimeException e) {
            if (e instanceof RuntimeException re) throw re;
            throw new IllegalStateException(e);
        }
    }

    /** OffsetDateTime.ofInstant — inlined helper to keep imports minimal. */
    private static java.time.OffsetDateTime Instant_of(String iso) {
        try {
            return java.time.OffsetDateTime.parse(iso);
        } catch (Exception e) {
            return java.time.OffsetDateTime.ofInstant(java.time.Instant.parse(iso), java.time.ZoneOffset.UTC);
        }
    }

    // ── decode (port of decodeObject) ───────────────────────────────────────

    private static final RowMapper<Map<String, Object>> DECODE_ROW = (rs, i) -> {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("property_name", rs.getString("property_name"));
        row.put("field_type_code", rs.getInt("field_type_code"));
        Object raw = rs.getObject("raw_value");
        row.put("raw_value", raw instanceof org.postgresql.util.PGobject pg ? pg.getValue()
                : raw == null ? null : String.valueOf(raw));
        return row;
    };

    /** Decode an object_id back into a JSON object (single SQL round-trip). */
    public static Map<String, Object> decodeObject(JdbcTemplate jdbc, long objectId) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT f.property_name,"
                        + " CASE f.field_type_code"
                        + "   WHEN 1 THEN (SELECT v.value::text FROM shrapnel.value_long v WHERE v.id = oav.value_id)"
                        + "   WHEN 2 THEN (SELECT v.value FROM shrapnel.value_string v WHERE v.id = oav.value_id)"
                        + "   WHEN 3 THEN (SELECT v.value::text FROM shrapnel.value_double v WHERE v.id = oav.value_id)"
                        + "   WHEN 4 THEN (SELECT v.value::text FROM shrapnel.value_boolean v WHERE v.id = oav.value_id)"
                        + "   WHEN 5 THEN (SELECT v.value::text FROM shrapnel.value_timestamp v WHERE v.id = oav.value_id)"
                        + "   WHEN 6 THEN (SELECT v.value::text FROM shrapnel.value_jsonb v WHERE v.id = oav.value_id)"
                        + "   WHEN 7 THEN (SELECT v.value::text FROM shrapnel.value_uuid v WHERE v.id = oav.value_id)"
                        + " END AS raw_value,"
                        + " f.field_type_code"
                        + " FROM shrapnel.object_attribute_value oav"
                        + " JOIN shrapnel.field f ON f.id = oav.field_id"
                        + " WHERE oav.object_id = ?"
                        + " ORDER BY f.field_index, f.id",
                DECODE_ROW, objectId);

        Map<String, Object> obj = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            String prop = String.valueOf(row.get("property_name"));
            int typeCode = ((Number) row.get("field_type_code")).intValue();
            String raw = (String) row.get("raw_value");
            obj.put(prop, typeCode == ShrapnelTypes.JSONB && raw != null ? parseJsonLenient(raw)
                    : ShrapnelTypes.coerceFromStorage(raw, typeCode));
        }
        return obj;
    }

    /** Lenient JSON parse for JSONB decode; falls back to the raw string. */
    public static Object parseJsonLenient(String raw) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(raw, Object.class);
        } catch (Exception e) {
            return raw;
        }
    }

    /** Unwrap DataAccessException with a PG SQLSTATE into the API exception space. */
    public static ShrapnelApiException mapPg(DataAccessException e) {
        String code = pgCode(e);
        if ("23514".equals(code)) {
            return ShrapnelApiException.conflict(e.getMostSpecificCause().getMessage(), e.getMostSpecificCause().getMessage());
        }
        if ("P0001".equals(code)) {
            return ShrapnelApiException.conflict(e.getMostSpecificCause().getMessage());
        }
        return null;
    }

    /** Extract the five-char SQLSTATE from a Spring data-access exception chain. */
    public static String pgCode(DataAccessException e) {
        Throwable t = e;
        while (t != null) {
            if (t instanceof java.sql.SQLException se) {
                return se.getSQLState();
            }
            t = t.getCause();
        }
        return null;
    }

    private static Object firstNonNull(Object a, Object b) {
        return a != null ? a : b;
    }

    private static String str(Object o) {
        return o instanceof String s ? s : null;
    }

    private static Integer intOrNull(Object o) {
        if (o instanceof Number n) return n.intValue();
        if (o instanceof String s && !s.isBlank()) {
            try {
                return Integer.parseInt(s.trim());
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }
}
