package org.nexus.core.aegis.store;

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

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate named;
    private final DataSource dataSource;

    public AegisStore(JdbcTemplate jdbc, NamedParameterJdbcTemplate named, DataSource dataSource) {
        this.jdbc = jdbc;
        this.named = named;
        this.dataSource = dataSource;
    }

    // ── Helpers mirroring routes.ts ──────────────────────────────────────

    public static boolean isUuid(String v) {
        return v != null && v.matches("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");
    }

    /** Pick only allowed, present columns out of the request body. */
    public static LinkedHashMap<String, Object> pick(Map<String, Object> body, List<String> allowed) {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        if (body == null) return out;
        for (String k : allowed) {
            if (body.containsKey(k) && body.get(k) != null) out.put(k, body.get(k));
        }
        return out;
    }

    /** JSONB columns must arrive as JSON text for pg; everything else passes through. */
    private static LinkedHashMap<String, Object> jsonbCoerced(LinkedHashMap<String, Object> cols) {
        LinkedHashMap<String, Object> coerced = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : cols.entrySet()) {
            coerced.put(e.getKey(), JSONB_COLS.contains(e.getKey()) && e.getValue() != null
                    ? AegisDigest.canonicalJson(e.getValue()) : e.getValue());
        }
        return coerced;
    }

    /** Map a pg SQLSTATE to an HTTP status + message (mirrors pgError in routes.ts). */
    public record PgError(int status, String message) {}

    public static PgError mapPgError(String sqlState) {
        if (sqlState == null) return new PgError(500, "internal server error");
        return switch (sqlState) {
            case "23505" -> new PgError(409, "duplicate key: conflict");
            case "23503" -> new PgError(400, "foreign key violation: referenced row missing");
            case "23514" -> new PgError(400, "check constraint violation");
            case "22P02" -> new PgError(400, "invalid value");
            default -> new PgError(500, "internal server error");
        };
    }

    // ── Registries ───────────────────────────────────────────────────────

    public List<Map<String, Object>> listRegistries() {
        return jdbc.queryForList("SELECT * FROM aegis.registry ORDER BY created_at");
    }

    public Map<String, Object> registryByName(String name) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis.registry WHERE name = ? AND is_active = true", name);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Map<String, Object> registryById(String id) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis.registry WHERE id = ?", UUID.fromString(id));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public String registryExists(String id) {
        List<Map<String, Object>> rows = jdbc.queryForList(
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
        return rows.get(0);
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
        return rows.isEmpty() ? null : rows.get(0);
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
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis.registry_revision WHERE id = ?", revisionId);
        return rows.get(0);
    }

    public Map<String, Object> getRegistryRevision(String registryId, String revisionId) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis.registry_revision WHERE id = ? AND registry_id = ?",
                UUID.fromString(revisionId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public List<Map<String, Object>> listRevisions(String registryId) {
        return jdbc.queryForList(
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
        return jdbc.queryForList(
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
        return jdbc.queryForList(
                "SELECT * FROM aegis.model_check_result WHERE registry_id = ? ORDER BY checked_at DESC",
                UUID.fromString(registryId));
    }

    // ── Child CRUD (generic, mirroring childHandlers) ────────────────────

    public List<Map<String, Object>> listChildren(String table, String registryId) {
        return jdbc.queryForList(
                "SELECT * FROM aegis." + table + " WHERE registry_id = ? ORDER BY created_at",
                UUID.fromString(registryId));
    }

    public Map<String, Object> createChild(String table, String registryId, LinkedHashMap<String, Object> body) {
        if (body.isEmpty()) throw new NoFieldsException();
        LinkedHashMap<String, Object> coerced = jsonbCoerced(body);
        coerced.put("registry_id", UUID.fromString(registryId));
        SimpleJdbcInsert insert = new SimpleJdbcInsert(dataSource)
                .withTableName(table).withSchemaName("aegis").usingGeneratedKeyColumns();
        java.sql.Timestamp ts = new java.sql.Timestamp(System.currentTimeMillis());
        LinkedHashMap<String, Object> withDefaults = new LinkedHashMap<>(coerced);
        withDefaults.putIfAbsent("created_at", ts);
        withDefaults.putIfAbsent("updated_at", ts);
        Number key = insert.executeAndReturnKeyHolder(withDefaults).getKey();
        return jdbc.queryForMap("SELECT * FROM aegis." + table + " WHERE id = ?", key);
    }

    public boolean childExists(String table, String registryId, String childId) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT id FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
        return !rows.isEmpty();
    }

    public Map<String, Object> getChild(String table, String registryId, String childId) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Map<String, Object> updateChild(String table, String registryId, String childId,
                                           LinkedHashMap<String, Object> body) {
        if (body.isEmpty()) throw new NoFieldsException();
        MapSqlParameterSource params = new MapSqlParameterSource(jsonbCoerced(body));
        params.addValue("id", UUID.fromString(childId));
        params.addValue("registry_id", UUID.fromString(registryId));
        StringBuilder set = new StringBuilder();
        int i = 0;
        for (String col : body.keySet()) {
            if (i > 0) set.append(", ");
            set.append(col).append(" = :").append(col);
            i++;
        }
        List<Map<String, Object>> rows = named.queryForList(
                "UPDATE aegis." + table + " SET " + set
                        + " WHERE id = :id AND registry_id = :registry_id RETURNING *",
                params);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public int deleteChild(String table, String registryId, String childId) {
        return jdbc.update("DELETE FROM aegis." + table + " WHERE id = ? AND registry_id = ?",
                UUID.fromString(childId), UUID.fromString(registryId));
    }

    // ── Wind compilation (read surfaces) ─────────────────────────────────

    public List<Map<String, Object>> listWindCompilations(String registryId) {
        return jdbc.queryForList(
                "SELECT * FROM aegis.wind_compilation WHERE registry_id = ? ORDER BY compiled_at DESC",
                UUID.fromString(registryId));
    }

    public Map<String, Object> getWindCompilation(String registryId, String compilationId) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM aegis.wind_compilation WHERE id = ? AND registry_id = ?",
                UUID.fromString(compilationId), UUID.fromString(registryId));
        return rows.isEmpty() ? null : rows.get(0);
    }

    public static class NoFieldsException extends RuntimeException {
        public NoFieldsException() { super("no fields provided"); }
    }
}
