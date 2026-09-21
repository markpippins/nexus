package org.nexus.core.shrapnel;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Store layer for the shrapnel port — the direct SQL surface of the TS
 * service's routers ({@code routes/objects.js}, {@code routes/fields.js},
 * {@code routes/field-types.js}, {@code routes/stereotypes.js},
 * {@code routes/health.js}), statement-for-statement over the schema-qualified
 * {@code shrapnel.*} tables and API functions.
 */
@Service
public class ShrapnelStore {

    private final JdbcTemplate jdbc;
    private final org.springframework.transaction.support.TransactionTemplate tx;

    public ShrapnelStore(JdbcTemplate jdbc,
                         org.springframework.transaction.support.TransactionTemplate tx) {
        this.jdbc = jdbc;
        this.tx = tx;
    }

    // ── shared row mappers ──────────────────────────────────────────────────

    private static Map<String, Object> row(java.sql.ResultSet rs, int rowNum) throws java.sql.SQLException {
        java.sql.ResultSetMetaData md = rs.getMetaData();
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 1; i <= md.getColumnCount(); i++) {
            String col = md.getColumnLabel(i);
            Object v = rs.getObject(i);
            if (v instanceof org.postgresql.util.PGobject pg) v = pg.getValue();
            out.put(col, v);
        }
        return out;
    }

    private static final RowMapper<Map<String, Object>> MAP = ShrapnelStore::row;

    // ── health (routes/health.js) ───────────────────────────────────────────

    public Map<String, Object> healthCounts() {
        return jdbc.queryForObject(
                "SELECT"
                        + " (SELECT COUNT(*)::int FROM shrapnel.field_type) AS field_type_count,"
                        + " (SELECT COUNT(*)::int FROM shrapnel.field) AS field_count,"
                        + " (SELECT COUNT(*)::int FROM shrapnel.object_instance) AS object_count,"
                        + " (SELECT COUNT(*)::int FROM shrapnel.value) AS value_count,"
                        + " (SELECT COUNT(*)::int FROM shrapnel.object_attribute_value) AS binding_count",
                MAP);
    }

    // ── field types (routes/field-types.js) ─────────────────────────────────

    public List<Map<String, Object>> listFieldTypes() {
        return jdbc.query(
                "SELECT code, name, description, pg_type FROM shrapnel.field_type ORDER BY code", MAP);
    }

    public Map<String, Object> getFieldType(int code) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT code, name, description, pg_type FROM shrapnel.field_type WHERE code = ?", MAP, code);
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ── fields (routes/fields.js) ───────────────────────────────────────────

    public List<Map<String, Object>> listFields(Integer typeCode, int limit, int offset) {
        StringBuilder q = new StringBuilder(
                "SELECT id, is_calculated, field_index, label, name, property_name, field_type_code, created_at, updated_at"
                        + " FROM shrapnel.field");
        Object[] args;
        if (typeCode != null) {
            q.append(" WHERE field_type_code = ?");
            args = new Object[]{limit, offset, typeCode};
        } else {
            args = new Object[]{limit, offset};
        }
        q.append(" ORDER BY field_index, id LIMIT ? OFFSET ?");
        return jdbc.query(q.toString(), MAP, args);
    }

    public Map<String, Object> getField(long id) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, is_calculated, field_index, label, name, property_name, field_type_code, created_at, updated_at"
                        + " FROM shrapnel.field WHERE id = ?",
                MAP, id);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Map<String, Object> upsertField(Map<String, Object> spec) {
        List<Map<String, Object>> rows = jdbc.query(
                "INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)"
                        + " VALUES (?, ?, ?, ?, ?, ?)"
                        + " ON CONFLICT (property_name) DO UPDATE SET name = EXCLUDED.name"
                        + " RETURNING *",
                MAP,
                (Boolean) spec.get("is_calculated"),
                (Integer) spec.get("field_index"),
                spec.get("label"),
                spec.get("name"),
                spec.get("property_name"),
                (Integer) spec.get("field_type_code"));
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ── objects (routes/objects.js) ─────────────────────────────────────────

    public List<Map<String, Object>> listObjects(int limit, int offset) {
        return jdbc.query(
                "SELECT id, created_at FROM shrapnel.object_instance ORDER BY id DESC LIMIT ? OFFSET ?",
                MAP, limit, offset);
    }

    public Map<String, Object> getObjectRow(long id) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT id, created_at FROM shrapnel.object_instance WHERE id = ?", MAP, id);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public Long deleteObject(long id) {
        List<Map<String, Object>> rows = jdbc.query(
                "DELETE FROM shrapnel.object_instance WHERE id = ? RETURNING id", MAP, id);
        return rows.isEmpty() ? null : ((Number) rows.get(0).get("id")).longValue();
    }

    public Map<String, Object> objectConformance(long id) {
        return jdbc.queryForObject(
                "SELECT shrapnel.object_conformance(?) AS conformance", MAP, id);
    }

    /** Port of POST /api/objects/:id/classify — atomic, PG-error-mapped. */
    public Map<String, Object> classify(long id, long revisionId, String disposition) {
        return tx.execute(status -> {
            Map<String, Object> chk = getObjectRow(id);
            if (chk == null) {
                throw ShrapnelApiException.notFound("object " + id + " not found");
            }
            try {
                return jdbc.queryForObject(
                        "SELECT shrapnel.object_classify(?, ?, ?) AS result", MAP, id, revisionId, disposition);
            } catch (org.springframework.dao.DataAccessException e) {
                ShrapnelApiException mapped = ShrapnelCodec.mapPg(e);
                if (mapped != null) throw mapped;
                throw e;
            }
        });
    }

    public List<Map<String, Object>> objectValues(long id) {
        return jdbc.query(
                "SELECT f.id AS field_id, f.property_name, f.label, f.name, f.field_type_code,"
                        + " oav.value_id, oav.created_at AS bound_at"
                        + " FROM shrapnel.object_attribute_value oav"
                        + " JOIN shrapnel.field f ON f.id = oav.field_id"
                        + " WHERE oav.object_id = ?"
                        + " ORDER BY f.field_index, f.id",
                MAP, id);
    }

    // ── stereotypes (routes/stereotypes.js) ─────────────────────────────────

    public Map<String, Object> resolveHeadRevision(String name) {
        List<Map<String, Object>> rows = jdbc.query(
                "SELECT stereotype_id, head_revision_id, version FROM shrapnel.stereotype_resolve(?)",
                MAP, name);
        if (rows.isEmpty()) {
            throw ShrapnelApiException.notFound("stereotype '" + name + "' not found");
        }
        return rows.get(0);
    }

    public List<Map<String, Object>> listStereotypes() {
        return jdbc.query(
                "SELECT s.id AS stereotype_id,"
                        + " s.name,"
                        + " s.description,"
                        + " r.id AS head_revision_id,"
                        + " r.version,"
                        + " r.depth,"
                        + " r.contract_fingerprint,"
                        + " r.created_at"
                        + " FROM shrapnel.stereotype s"
                        + " JOIN shrapnel.stereotype_revision r ON r.stereotype_id = s.id"
                        + " WHERE r.version = (SELECT max(version) FROM shrapnel.stereotype_revision"
                        + "                     WHERE stereotype_id = s.id)"
                        + " ORDER BY s.name",
                MAP);
    }

    public Map<String, Object> getRevision(long headRevisionId) {
        return jdbc.queryForObject(
                "SELECT r.id, r.stereotype_id, r.version, r.parent_revision_id,"
                        + " r.parent_stereotype_id, r.extends_rationale, r.depth,"
                        + " r.contract_fingerprint, r.created_at"
                        + " FROM shrapnel.stereotype_revision r"
                        + " WHERE r.id = ?",
                MAP, headRevisionId);
    }

    public List<Map<String, Object>> stereotypeChain(long headRevisionId) {
        return jdbc.query(
                "SELECT name, version, hop FROM shrapnel.stereotype_chain(?)", MAP, headRevisionId);
    }

    public List<Map<String, Object>> stereotypeContract(long headRevisionId) {
        return jdbc.query(
                "SELECT property_name, required, origin_revision, origin_stereotype, origin_version"
                        + " FROM shrapnel.stereotype_effective_contract(?)"
                        + " ORDER BY required DESC, property_name",
                MAP, headRevisionId);
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> createStereotypeRevision(Object body) {
        Map<String, Object> b = body instanceof Map ? (Map<String, Object>) body : Map.of();
        String name = b.get("name") instanceof String s && !s.isBlank() ? s.trim() : "";
        if (name.isEmpty()) {
            throw ShrapnelApiException.badRequest("name is required");
        }
        java.util.List<String> requiredFields = stringArray(b.get("required_fields"), "required_fields");
        if (requiredFields == null) {
            throw ShrapnelApiException.badRequest("required_fields is required");
        }
        java.util.List<String> optionalFields = stringArray(b.get("optional_fields"), "optional_fields");

        Object extendsRevision = b.get("extends_revision");
        Long extendsId = null;
        if (extendsRevision != null && !"".equals(extendsRevision)) {
            try {
                extendsId = Long.parseLong(String.valueOf(extendsRevision));
            } catch (NumberFormatException e) {
                throw ShrapnelApiException.badRequest("extends_revision must be an integer revision id");
            }
        }
        String rationale = b.get("rationale") instanceof String s && !s.isBlank() ? s.trim() : null;
        if (extendsId != null && rationale == null) {
            throw ShrapnelApiException.badRequest("rationale is required when extends_revision is provided");
        }
        if (extendsId == null) rationale = null;

        Long finalExtendsId = extendsId;
        String finalRationale = rationale;

        // The text[] args must be created on the connection executing the call,
        // so the whole flow runs inside a ConnectionCallback — which participates
        // in the surrounding Spring transaction via DataSourceUtils.
        return jdbc.execute((org.springframework.jdbc.core.ConnectionCallback<Map<String, Object>>) con -> {
            try {
                java.sql.Array reqA = con.createArrayOf("text", requiredFields.toArray());
                java.sql.Array optA = optionalFields == null ? null
                        : con.createArrayOf("text", optionalFields.toArray());
                Long revisionId = jdbc.queryForObject(
                        "SELECT shrapnel.stereotype_create_revision(?, ?, ?, ?, ?) AS revision_id",
                        Long.class, name, finalExtendsId, finalRationale, reqA, optA);
                return jdbc.queryForObject(
                        "SELECT r.id AS revision_id, s.name, r.version, r.depth, r.contract_fingerprint"
                                + " FROM shrapnel.stereotype_revision r"
                                + " JOIN shrapnel.stereotype s ON s.id = r.stereotype_id"
                                + " WHERE r.id = ?",
                        MAP, revisionId);
            } catch (org.springframework.dao.DataAccessException e) {
                ShrapnelApiException mapped = ShrapnelCodec.mapPg(e);
                if (mapped != null) throw mapped;
                throw e;
            }
        });
    }

    // ── helpers ─────────────────────────────────────────────────────────────

    private static java.util.List<String> stringArray(Object value, String label) {
        if (value == null) return null;
        if (!(value instanceof java.util.List<?> list)
                || list.stream().anyMatch(x -> !(x instanceof String s) || s.isBlank())) {
            throw ShrapnelApiException.badRequest(label + " must be an array of non-empty strings");
        }
        return list.stream().map(x -> String.valueOf(x).trim()).toList();
    }
}
