package org.nexus.core.aegis.store;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import org.nexus.core.aegis.kernel.AegisDigest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.simple.SimpleJdbcInsert;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;

/**
 * Aegis-schema persistence — the JVM counterpart of the SQL embedded in
 * typescript/aegis-srv/src/routes.ts. Same tables, same columns, same
 * stored-procedure call (aegis.create_registry_revision), same soft-delete
 * and JSONB semantics. Column allow-lists mirror the TS pick() whitelists so
 * the REST surfaces accept identical payloads.
 */
@Service
public class AegisStore {

    private static final Set<String> JSONB_COLS = Set.of(
            "metadata", "value", "initial_value", "domain", "variable_assignments",
            "action", "default_value", "trace", "errors", "warnings", "suggestions", "context");

    private static final List<String> REGISTRY_COLS = List.of(
            "name", "description", "version", "tla_plus_source", "tla_plus_module",
            "metadata", "tags", "is_active", "expires_at", "main_concept_id");

    /**
     * Per-table body-column allowlists — the JVM twin of the TS
     * childHandlers(table, createCols, updateCols) arguments in
     * typescript/aegis-srv/src/routes.ts. Both the table name and every
     * interpolated column identifier MUST come from these static constants;
     * request-body keys never reach SQL text (CodeQL java/sql-injection),
     * and unknown body keys are dropped exactly like the TS pick().
     */
    private static final Map<String, List<String>> CHILD_TABLE_COLS = Map.ofEntries(
            Map.entry("constant", List.of("name", "type", "value", "description", "constraints")),
            Map.entry("variable", List.of("name", "type", "initial_value", "domain", "description", "constraints", "attribute_id")),
            Map.entry("state", List.of("name", "description", "variable_assignments", "constraints", "is_initial", "is_terminal", "concept_id", "attribute_value_id")),
            Map.entry("transition", List.of("name", "description", "guard_expression", "action", "weak_fairness", "strong_fairness", "temporal_conditions", "priority", "from_state_id", "to_state_id", "guard_rule_id", "transition_rule_id", "state_transition_id")),
            Map.entry("invariant", List.of("name", "expression", "description", "is_type_invariant", "rule_id", "expression_id")),
            Map.entry("property", List.of("name", "type", "expression", "description", "is_verified", "verified_at", "verified_by")),
            Map.entry("temporal_property", List.of("name", "operator", "expression", "description")),
            Map.entry("concept_mapping", List.of("tla_name", "concept_id", "mapping_type", "mapping_expression", "cardinality")),
            Map.entry("attribute_mapping", List.of("tla_variable", "attribute_id", "conversion_function", "default_value")),
            Map.entry("relationship_mapping", List.of("tla_relationship", "relationship_id", "mapping_type", "constraints")),
            Map.entry("execution_log", List.of("entity_id", "from_state_id", "to_state_id", "transition_id", "trigger_event", "trigger_user", "context")));

    /** Validate a child table against the allowlist; returns its column list. */
    public static List<String> childColumns(String table) {
        List<String> cols = CHILD_TABLE_COLS.get(table);
        if (cols == null) {
            throw new IllegalArgumentException("unknown aegis child table: " + table);
        }
        return cols;
    }

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate named;
    private final DataSource dataSource;

    public AegisStore(JdbcTemplate jdbc, NamedParameterJdbcTemplate named, DataSource dataSource) {
        this.jdbc = jdbc;
        this.named = named;
        this.dataSource = dataSource;
    }

    // ── Row sanitization (pg-driver parity) ─────────────────────────────
    //
    // The TS service's `pg` driver parses text[] and jsonb columns into JS
    // values automatically. JdbcTemplate.queryForList does NOT: it hands back
    // raw java.sql.Array / PGobject objects, which Jackson cannot serialize
    // (PgArray carries a live PgConnection). Every read goes through q(),
    // which normalizes rows to Jackson-safe values — matching what the TS
    // service would return for the same row.

    /** queryForList + per-row pg-type normalization (see sanitize). */
    public List<Map<String, Object>> q(String sql, Object... args) {
        List<Map<String, Object>> rows = args.length == 0
                ? jdbc.queryForList(sql)
                : jdbc.queryForList(sql, args);
        for (int i = 0; i < rows.size(); i++) {
            rows.set(i, sanitize(rows.get(i)));
        }
        return rows;
    }

    /** Normalize pg-specific JDBC values to JSON-serializable equivalents. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> sanitize(Map<String, Object> row) {
        for (Map.Entry<String, Object> e : row.entrySet()) {
            Object v = e.getValue();
            if (v instanceof java.sql.Array arr) {
                try {
                    Object a = arr.getArray();
                    List<Object> out = new ArrayList<>();
                    if (a instanceof Object[] oa) {
                        out.addAll(java.util.Arrays.asList(oa));
                    } else if (a != null) {
                        out.add(a);
                    }
                    v = out;
                } catch (java.sql.SQLException ex) {
                    v = null;
                }
            } else if (v instanceof org.postgresql.util.PGobject pg) {
                String type = pg.getType();
                String value = pg.getValue();
                if ("jsonb".equals(type) || "json".equals(type)) {
                    v = parseJson(value);
                } else {
                    v = value;
                }
            }
            e.setValue(v);
        }
        return row;
    }

    private static Object parseJson(String raw) {
        if (raw == null) return null;
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(raw, Object.class);
        } catch (Exception ex) {
            return raw;
        }
    }

    // ── Helpers mirroring routes.ts ──────────────────────────────────────

    public static boolean isUuid(String v) {
        return v != null && v.matches("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");
    }

    /** Pick only allowed, present columns out of the request body.
     *  Mirrors TS pick(): explicit nulls ARE kept (!== undefined), absent
     *  keys and unknown keys are dropped. */
    public static LinkedHashMap<String, Object> pick(Map<String, Object> body, List<String> allowed) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        if (body == null) return out;
        for (String k : allowed) {
            if (body.containsKey(k)) out.put(k, body.get(k));
        }
        return out;
    }

    /**
     * JSONB columns must arrive as JSON text for pg; everything else passes
     * through — except text[] columns, which must arrive as java.sql.Array
     * (the pg driver cannot bind a java.util.List; TS pg binds JS arrays
     * natively). Array binding needs a live Connection, so list values are
     * wrapped in a SqlTypeValue that creates the array lazily on the
     * executing connection (works under NamedParameterJdbcTemplate).
     */
    private static LinkedHashMap<String, Object> jsonbCoerced(LinkedHashMap<String, Object> cols) {
        LinkedHashMap<String, Object> coerced = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : cols.entrySet()) {
            Object v = e.getValue();
            if (JSONB_COLS.contains(e.getKey()) && v != null) {
                // JSON text bound with Types.OTHER: pg must infer jsonb from
                // the column context (setString would declare varchar and PG
                // rejects 42804 — node-postgres gets this for free by sending
                // unspecified parameter types).
                coerced.put(e.getKey(), otherTyped(AegisDigest.canonicalJson(v)));
            } else if (v instanceof List<?> list) {
                coerced.put(e.getKey(), textArrayOf(list));
            } else {
                coerced.put(e.getKey(), v);
            }
        }
        return coerced;
    }

    /** Bind a value with Types.OTHER so PG infers the target type from context. */
    private static org.springframework.jdbc.core.SqlTypeValue otherTyped(String json) {
        return new org.springframework.jdbc.core.SqlTypeValue() {
            @Override
            public void setTypeValue(PreparedStatement ps, int paramIndex, int sqlType, String typeName)
                    throws SQLException {
                ps.setObject(paramIndex, json, java.sql.Types.OTHER);
            }
        };
    }

    /** SqlTypeValue binding a Java list as a PostgreSQL text[] on the executing connection. */
    private static org.springframework.jdbc.core.SqlTypeValue textArrayOf(List<?> list) {
        final Object[] arr = list.toArray();
        return new org.springframework.jdbc.core.SqlTypeValue() {
            @Override
            public void setTypeValue(PreparedStatement ps, int paramIndex, int sqlType, String typeName)
                    throws SQLException {
                java.sql.Array a = ps.getConnection().createArrayOf("text", arr);
                ps.setArray(paramIndex, a);
            }
        };
    }

    /** Map a pg SQLSTATE to an HTTP status + message (mirrors pgError in routes.ts). */
    public record PgError(int status, String message, boolean unmapped) {
        public PgError(int status, String message) { this(status, message, false); }
    }

    public static PgError mapPgError(String sqlState) {
        if (sqlState == null) return new PgError(500, "internal server error", true);
        return switch (sqlState) {
            case "23505" -> new PgError(409, "duplicate key: conflict");
            case "23503" -> new PgError(400, "foreign key violation: referenced row missing");
            case "23514" -> new PgError(400, "check constraint violation");
            case "22P02" -> new PgError(400, "invalid value");
            default -> new PgError(500, "internal server error", true);
        };
    }

    // ── Registries ───────────────────────────────────────────────────────

    public List<Map<String, Object>> listRegistries() {
        return q("SELECT * FROM aegis.registry ORDER BY created_at");
    }

    public Map<String, Object> registryByName(String name) {
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis.registry WHERE name = ? AND is_active = true", name);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Map<String, Object> registryById(String id) {
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis.registry WHERE id = ?", UUID.fromString(id));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public String registryExists(String id) {
        List<Map<String, Object>> rows = q(
                "SELECT id FROM aegis.registry WHERE id = ?", UUID.fromString(id));
        return rows.isEmpty() ? null : String.valueOf(rows.get(0).get("id"));
    }

    public Map<String, Object> createRegistry(Map<String, Object> body) {
        LinkedHashMap<String, Object> cols = pick(body, REGISTRY_COLS);
        if (cols.isEmpty()) throw new NoFieldsException();
        MapSqlParameterSource params = new MapSqlParameterSource(jsonbCoerced(cols));
        List<Map<String, Object>> rows = named.queryForList(
                "INSERT INTO aegis.registry (" + String.join(", ", cols.keySet())
                        + ") VALUES (:" + String.join(", :", cols.keySet()) + ") RETURNING *",
                params);
        return sanitize(rows.get(0));
    }

    public Map<String, Object> updateRegistry(String id, Map<String, Object> body) {
        LinkedHashMap<String, Object> cols = pick(body, REGISTRY_COLS);
        if (cols.isEmpty()) throw new NoFieldsException();
        MapSqlParameterSource params = new MapSqlParameterSource(jsonbCoerced(cols));
        params.addValue("id", UUID.fromString(id));
        StringBuilder set = new StringBuilder();
        int i = 0;
        for (String col : cols.keySet()) {
            if (i > 0) set.append(", ");
            set.append(col).append(" = :").append(col);
            i++;
        }
        List<Map<String, Object>> rows = named.queryForList(
                "UPDATE aegis.registry SET " + set + " WHERE id = :id RETURNING *", params);
        return rows.isEmpty() ? null : sanitize(rows.get(0));
    }

    public int softDeleteRegistry(String id) {
        return jdbc.update("UPDATE aegis.registry SET is_active = false WHERE id = ?",
                UUID.fromString(id));
    }

    // ── Registry revisions (Phase A) ─────────────────────────────────────

    public Map<String, Object> createRegistryRevision(String registryId, String createdBy) {
        UUID revisionId = jdbc.queryForObject(
                "SELECT aegis.create_registry_revision(?, ?) AS revision_id",
                UUID.class, UUID.fromString(registryId),
                createdBy == null ? null : UUID.fromString(createdBy));
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis.registry_revision WHERE id = ?", revisionId);
        return rows.get(0);
    }

    public Map<String, Object> getRegistryRevision(String registryId, String revisionId) {
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis.registry_revision WHERE id = ? AND registry_id = ?",
                UUID.fromString(revisionId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public List<Map<String, Object>> listRevisions(String registryId) {
        return q(
                "SELECT * FROM aegis.registry_revision WHERE registry_id = ? ORDER BY revision_number DESC",
                UUID.fromString(registryId));
    }

    // ── Validation ───────────────────────────────────────────────────────

    public Map<String, Object> insertValidationResult(String registryId, boolean isValid,
                                                      List<Map<String, Object>> errors,
                                                      List<Map<String, Object>> warnings,
                                                      List<Map<String, Object>> suggestions,
                                                      String validatedBy) {
        return jdbc.queryForMap(
                "INSERT INTO aegis.validation_result (registry_id, is_valid, errors, warnings, suggestions, validated_by) "
                        + "VALUES (?, ?, ?::jsonb, ?::jsonb, ?::jsonb, ?) RETURNING *",
                UUID.fromString(registryId), isValid,
                AegisDigest.canonicalJson(errors),
                AegisDigest.canonicalJson(warnings),
                AegisDigest.canonicalJson(suggestions),
                validatedBy);
    }

    public List<Map<String, Object>> listValidationResults(String registryId) {
        return q(
                "SELECT * FROM aegis.validation_result WHERE registry_id = ? ORDER BY validated_at DESC",
                UUID.fromString(registryId));
    }

    // ── Model-check persistence ──────────────────────────────────────────

    public Map<String, Object> insertModelCheckResult(Map<String, Object> row) {
        return jdbc.queryForMap(
                "INSERT INTO aegis.model_check_result "
                        + "(registry_id, registry_revision_id, property_id, status, trace, checked_properties, "
                        + "execution_time_ms, checked_by, engine, engine_version, checker_config_digest, "
                        + "source_digest, model_digest, input_snapshot_digest, result_digest, "
                        + "safety_status, liveness_status, authority_level, reason) "
                        + "VALUES (?, ?, ?, ?, ?::jsonb, ?::jsonb, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
                row.get("registry_id"), row.get("registry_revision_id"), row.get("property_id"),
                row.get("status"),
                row.get("trace") == null ? null : AegisDigest.canonicalJson(row.get("trace")),
                AegisDigest.canonicalJson(row.get("checked_properties")),
                row.get("execution_time_ms"), row.get("checked_by"), row.get("engine"),
                row.get("engine_version"), row.get("checker_config_digest"), row.get("source_digest"),
                row.get("model_digest"), row.get("input_snapshot_digest"), row.get("result_digest"),
                row.get("safety_status"), row.get("liveness_status"), row.get("authority_level"),
                row.get("reason"));
    }

    public List<Map<String, Object>> listModelCheckResults(String registryId) {
        return q(
                "SELECT * FROM aegis.model_check_result WHERE registry_id = ? ORDER BY checked_at DESC",
                UUID.fromString(registryId));
    }

    // ── Child CRUD (generic, mirroring childHandlers) ────────────────────

    public List<Map<String, Object>> listChildren(String table, String registryId) {
        childColumns(table); // table must be allowlisted
        return q(
                "SELECT * FROM aegis." + table + " WHERE registry_id = ? ORDER BY created_at",
                UUID.fromString(registryId));
    }

    public Map<String, Object> createChild(String table, String registryId, LinkedHashMap<String, Object> body) {
        List<String> allowed = childColumns(table);
        LinkedHashMap<String, Object> bodyCols = pick(body, allowed);
        if (bodyCols.isEmpty()) throw new NoFieldsException();
        // Mirrors the TS childHandlers.create: INSERT (registry_id, <body cols>)
        // RETURNING *. No timestamp injection — PG defaults (created_at now())
        // apply, and child tables have no updated_at column.
        LinkedHashMap<String, Object> coerced = jsonbCoerced(bodyCols);
        StringBuilder cols = new StringBuilder("registry_id");
        StringBuilder marks = new StringBuilder("?");
        int i = 0;
        for (String col : coerced.keySet()) {
            cols.append(", ").append(col);
            marks.append(", ?");
            i++;
        }
        Object[] args = new Object[i + 1];
        args[0] = UUID.fromString(registryId);
        int a = 1;
        for (Object v : coerced.values()) args[a++] = v;
        // RETURNING * (not getGeneratedKeys): uuid PKs are app- or db-generated,
        // and this mirrors the TS INSERT ... RETURNING *.
        return sanitize(jdbc.queryForObject(
                "INSERT INTO aegis." + table + " (" + cols + ") VALUES (" + marks + ") RETURNING *",
                (rs, rowNum) -> {
                    java.sql.ResultSetMetaData md = rs.getMetaData();
                    Map<String, Object> row = new LinkedHashMap<>();
                    for (int c = 1; c <= md.getColumnCount(); c++) {
                        String label = md.getColumnLabel(c);
                        Object v = rs.getObject(c);
                        if (v instanceof java.sql.Array arr) {
                            try {
                                Object a2 = arr.getArray();
                                List<Object> out = new ArrayList<>();
                                if (a2 instanceof Object[] oa) out.addAll(java.util.Arrays.asList(oa));
                                else if (a2 != null) out.add(a2);
                                v = out;
                            } catch (SQLException ex) { v = null; }
                        } else if (v instanceof org.postgresql.util.PGobject pg) {
                            String val = pg.getValue();
                            v = ("jsonb".equals(pg.getType()) || "json".equals(pg.getType()))
                                    ? parseJson(val) : val;
                        }
                        row.put(label, v);
                    }
                    return row;
                }, args));
    }

    public boolean childExists(String table, String registryId, String childId) {
        childColumns(table); // table must be allowlisted
        List<Map<String, Object>> rows = q(
                "SELECT id FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
        return !rows.isEmpty();
    }

    public Map<String, Object> getChild(String table, String registryId, String childId) {
        childColumns(table); // table must be allowlisted
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Map<String, Object> updateChild(String table, String registryId, String childId,
                                           LinkedHashMap<String, Object> body) {
        List<String> allowed = childColumns(table);
        LinkedHashMap<String, Object> bodyCols = pick(body, allowed);
        if (bodyCols.isEmpty()) throw new NoFieldsException();
        // Mirrors the TS childHandlers.update: SET <body cols only>.
        MapSqlParameterSource params = new MapSqlParameterSource(jsonbCoerced(bodyCols));
        params.addValue("id", UUID.fromString(childId));
        params.addValue("registry_id", UUID.fromString(registryId));
        StringBuilder set = new StringBuilder();
        int i = 0;
        for (String col : bodyCols.keySet()) {
            if (i > 0) set.append(", ");
            set.append(col).append(" = :").append(col);
            i++;
        }
        List<Map<String, Object>> rows = named.queryForList(
                "UPDATE aegis." + table + " SET " + set
                        + " WHERE id = :id AND registry_id = :registry_id RETURNING *",
                params);
        return rows.isEmpty() ? null : sanitize(rows.get(0));
    }

    public int deleteChild(String table, String registryId, String childId) {
        childColumns(table); // table must be allowlisted
        return jdbc.update("DELETE FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
    }

    // ── Wind compilation (read surfaces) ─────────────────────────────────

    public List<Map<String, Object>> listWindCompilations(String registryId) {
        return q(
                "SELECT * FROM aegis.wind_compilation WHERE registry_id = ? ORDER BY compiled_at DESC",
                UUID.fromString(registryId));
    }

    public Map<String, Object> getWindCompilation(String registryId, String compilationId) {
        List<Map<String, Object>> rows = q(
                "SELECT * FROM aegis.wind_compilation WHERE id = ? AND registry_id = ?",
                UUID.fromString(compilationId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public static class NoFieldsException extends RuntimeException {
        public NoFieldsException() { super("no fields provided"); }
    }
}
