package org.nexus.core.broker.execution;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.sql.Array;
import java.sql.SQLException;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.postgresql.util.PGobject;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Read-only execution surface. Mirrors the moleculer worker.execution actions
 * (execution.worker.ts) 1:1 against the same `execution` schema tables via
 * search_path=execution — SELECTs only, no write path.
 *
 * Fail-open posture preserved from the source: a DB error surfaces as a 500
 * (the source let the handler error bubble), but every method here is a pure
 * read with no mutation.
 */
@Service
public class ExecutionReadService {

    private static final ObjectMapper MAPPER = new ObjectMapper().findAndRegisterModules();

    private final JdbcTemplate jdbc;

    public ExecutionReadService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private static Object sanitizeValue(Object v) {
        if (v == null) return null;
        // text[] / _text / any ARRAY -> List
        if (v instanceof Array arr) {
            try {
                Object raw = arr.getArray();
                if (raw instanceof Object[] objArr) {
                    return Arrays.stream(objArr).map(ExecutionReadService::sanitizeValue).toList();
                }
                if (raw != null && raw.getClass().isArray()) {
                    // primitive arrays not expected for text[], but handle generically
                    int len = java.lang.reflect.Array.getLength(raw);
                    var out = new java.util.ArrayList<>(len);
                    for (int i = 0; i < len; i++) out.add(sanitizeValue(java.lang.reflect.Array.get(raw, i)));
                    return out;
                }
                return raw;
            } catch (SQLException e) {
                return v.toString();
            }
        }
        if (v instanceof PGobject pg) {
            String type = pg.getType();
            String val = pg.getValue();
            if (val == null) return null;
            if ("json".equals(type) || "jsonb".equals(type)) {
                try {
                    JsonNode node = MAPPER.readTree(val);
                    return node;
                } catch (JsonProcessingException e) {
                    return val;
                }
            }
            if ("uuid".equals(type)) {
                try { return java.util.UUID.fromString(val); } catch (Exception ignore) { return val; }
            }
            // hstore, citext, etc — return raw string value
            return val;
        }
        // org.postgresql.jdbc.PgArray is already covered via java.sql.Array, but keep as fallback
        // Fallback: if Jackson would choke on PgArray internals, toString above would have caught it.
        return v;
    }

    private static Map<String, Object> sanitizeRow(Map<String, Object> row) {
        var out = new LinkedHashMap<String, Object>(row.size());
        for (var e : row.entrySet()) out.put(e.getKey(), sanitizeValue(e.getValue()));
        return out;
    }

    private static List<Map<String, Object>> sanitizeRows(List<Map<String, Object>> rows) {
        return rows.stream().map(ExecutionReadService::sanitizeRow).toList();
    }

    /** Paginated read over a table with optional status filter + ILIKE search. */
    public Map<String, Object> list(String table, String filterColumn, String statusOrType,
                                    String search, int limit, int offset) {
        int p = 1;
        var where = new StringBuilder();
        var args = new java.util.ArrayList<Object>();
        if (statusOrType != null && !statusOrType.isBlank()) {
            where.append("WHERE ").append(filterColumn).append(" = ?");
            args.add(statusOrType);
            p++;
        }
        if (search != null && !search.isBlank()) {
            String cols = switch (table) {
                case "requests" -> "business_key, title, objective, id";
                case "leases" -> "executor_id, id, request_id";
                case "attempts" -> "status, id, request_id, lease_id";
                case "receipts" -> "type, agent_role, summary, id";
                default -> "id";
            };
            where.append(where.length() == 0 ? "WHERE " : " AND ").append("(");
            String[] c = cols.split(", ");
            for (int i = 0; i < c.length; i++) {
                if (i > 0) where.append(" OR ");
                where.append(c[i]).append(" ILIKE ?");
                args.add("%" + search + "%");
                p++;
            }
            where.append(")");
        }
        String orderCol = switch (table) {
            case "receipts" -> "issued_at";
            default -> "created_at";
        };
        String sql = "SELECT *, COUNT(*) OVER() AS full_count FROM " + table + " " + where
            + " ORDER BY " + orderCol + " DESC LIMIT ? OFFSET ?";
        args.add(limit);
        args.add(offset);
        List<Map<String, Object>> rows = jdbc.queryForList(sql, args.toArray());
        int total = rows.isEmpty() ? 0
            : ((Number) rows.get(0).getOrDefault("full_count", 0)).intValue();
        List<Map<String, Object>> items = rows.stream().map(r -> {
            Map<String, Object> copy = new java.util.LinkedHashMap<>(r);
            copy.remove("full_count");
            return sanitizeRow(copy);
        }).toList();
        return Map.of("total", total, "limit", limit, "offset", offset, "items", items);
    }

    public Map<String, Object> requestState(String id) {
        Map<String, Object> request = sanitizeRow(jdbc.queryForMap("SELECT * FROM requests WHERE id = ?::uuid", id));
        Map<String, Object> lease;
        try {
            lease = sanitizeRow(jdbc.queryForMap(
                "SELECT * FROM leases WHERE request_id = ?::uuid ORDER BY (status = 'ACTIVE') DESC, acquired_at DESC LIMIT 1", id));
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            lease = null;
        }
        Map<String, Object> attempt;
        try {
            attempt = sanitizeRow(jdbc.queryForMap(
                "SELECT * FROM attempts WHERE request_id = ?::uuid ORDER BY created_at DESC, started_at DESC NULLS LAST LIMIT 1", id));
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            attempt = null;
        }
        List<Map<String, Object>> receipts = sanitizeRows(jdbc.queryForList(
            "SELECT * FROM receipts WHERE request_id = ?::uuid ORDER BY issued_at ASC", id));
        // Map.of rejects null values — build nullable map manually
        var out = new LinkedHashMap<String, Object>();
        out.put("request", request);
        out.put("current_lease", lease);
        out.put("latest_attempt", attempt);
        out.put("receipts", receipts);
        out.put("receipt_count", receipts.size());
        return out;
    }

    public Map<String, Object> staleLeases() {
        List<Map<String, Object>> rows = sanitizeRows(jdbc.queryForList(
            "SELECT l.id AS lease_id, l.request_id, l.executor_id, l.ttl_seconds, l.acquired_at,"
            + " l.expires_at, l.created_at, r.business_key, r.title, r.status AS request_status,"
            + " EXTRACT(EPOCH FROM (NOW() - l.expires_at))::int AS overdue_seconds"
            + " FROM leases l JOIN requests r ON r.id = l.request_id"
            + " WHERE l.status = 'ACTIVE' AND l.expires_at < NOW() ORDER BY l.expires_at ASC"));
        return Map.of("count", rows.size(), "stale_leases", rows);
    }

    public Map<String, Object> leaseLifecycle(String id) {
        Map<String, Object> row;
        try {
            row = sanitizeRow(jdbc.queryForMap(
                "SELECT *, EXTRACT(EPOCH FROM (expires_at - acquired_at))::int AS promised_ttl_seconds,"
                + " EXTRACT(EPOCH FROM (COALESCE(released_at, NOW()) - acquired_at))::int AS actual_held_seconds,"
                + " CASE WHEN status = 'RELEASED' AND released_at > expires_at THEN EXTRACT(EPOCH FROM (released_at - expires_at))::int"
                + " WHEN status = 'ACTIVE' AND NOW() > expires_at THEN EXTRACT(EPOCH FROM (NOW() - expires_at))::int ELSE 0 END AS overdue_seconds,"
                + " CASE WHEN status = 'RELEASED' THEN 'released' WHEN status = 'EXPIRED' THEN 'expired_unreleased'"
                + " WHEN status = 'ACTIVE' AND NOW() > expires_at THEN 'stale_active' WHEN status = 'ACTIVE' THEN 'live' ELSE status END AS lifecycle_state"
                + " FROM leases WHERE id = ?::uuid", id));
        } catch (org.springframework.dao.EmptyResultDataAccessException e) {
            throw new org.springframework.web.server.ResponseStatusException(
                org.springframework.http.HttpStatus.NOT_FOUND, "lease not found");
        }
        return row;
    }

    public Map<String, Object> health() {
        Map<String, Object> counts = jdbc.queryForMap(
            "SELECT (SELECT COUNT(*) FROM requests) AS requests,"
            + " (SELECT COUNT(*) FROM leases) AS leases,"
            + " (SELECT COUNT(*) FROM attempts) AS attempts,"
            + " (SELECT COUNT(*) FROM receipts) AS receipts");
        return Map.of("status", "ok", "db", true, "schema", "execution", "counts", counts);
    }
}