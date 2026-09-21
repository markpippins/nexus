package com.aibizarchitect.nexus.v1.spring.tackleregistry.tackle;

import tools.jackson.databind.json.JsonMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

/**
 * JDBC port of tackle-srv's ai-config registry reads against the SAME
 * PostgreSQL tables (schema "tackle"). The resolve SQL mirrors db.ts
 * getResolvedRoleConfig() so both runtimes resolve identically.
 *
 * <p>The DataSource's currentSchema is pinned to the tackle schema in
 * {@link TackleRegistryDataSourceConfig}, so unqualified table names are safe here.
 */
@Service
public class TackleRegistryService {

    private final JdbcTemplate jdbc;
    private final JsonMapper json = JsonMapper.builder().build();
    private final TackleApiKeyEncryptor apiKeyEncryptor;

    public TackleRegistryService(JdbcTemplate jdbc, TackleApiKeyEncryptor apiKeyEncryptor) {
        this.jdbc = jdbc;
        this.apiKeyEncryptor = apiKeyEncryptor;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> parseJson(String raw) {
        try {
            return (raw == null || raw.isBlank()) ? Map.of() : json.readValue(raw, Map.class);
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String decryptApiKey(String apiKey) {
        return apiKeyEncryptor.decrypt(apiKey);
    }

    // ── Reads ──────────────────────────────────────────────────────

    public List<TackleRecords.Provider> providers() {
        return jdbc.query(
                "SELECT id, name, type, endpoint_url, api_key, config_json FROM providers ORDER BY name",
                (rs, i) -> new TackleRecords.Provider(
                        rs.getString("id"), rs.getString("name"), rs.getString("type"),
                        rs.getString("endpoint_url"), decryptApiKey(rs.getString("api_key")),
                        parseJson(rs.getString("config_json"))));
    }

    public List<TackleRecords.Harness> harnesses() {
        return jdbc.query(
                "SELECT id, name, invocation_semantics FROM harnesses ORDER BY name",
                (rs, i) -> new TackleRecords.Harness(
                        rs.getString("id"), rs.getString("name"),
                        parseJson(rs.getString("invocation_semantics"))));
    }

    public List<TackleRecords.ModelRow> models() {
        return jdbc.query(
                "SELECT id, name, harness_id, provider_id, model_identifier, verified FROM models ORDER BY id",
                (rs, i) -> new TackleRecords.ModelRow(
                        rs.getString("id"), rs.getString("name"), rs.getString("harness_id"),
                        rs.getString("provider_id"), rs.getString("model_identifier"),
                        rs.getBoolean("verified")));
    }

    public List<TackleRecords.RoleRow> roles() {
        return jdbc.query(
                "SELECT id, name, description FROM roles ORDER BY name",
                (rs, i) -> new TackleRecords.RoleRow(
                        rs.getString("id"), rs.getString("name"), rs.getString("description")));
    }

    public List<TackleRecords.ConfigBundle> bundles() {
        return jdbc.query("""
                SELECT id, name, role, model_id, provider_id, harness_id, priority,
                       invocation_mode, command, endpoint_url, timeout_ms, is_active
                FROM config_bundle ORDER BY role, priority""",
                (rs, i) -> new TackleRecords.ConfigBundle(
                        rs.getString("id"), rs.getString("name"), rs.getString("role"),
                        rs.getString("model_id"), rs.getString("provider_id"),
                        rs.getString("harness_id"), rs.getInt("priority"),
                        rs.getString("invocation_mode"), rs.getString("command"),
                        rs.getString("endpoint_url"),
                        rs.getObject("timeout_ms") == null ? null : rs.getInt("timeout_ms"),
                        rs.getObject("is_active") == null ? null : rs.getBoolean("is_active")));
    }

    public List<TackleRecords.ConfigBundle> bundlesForRole(String role) {
        return bundles().stream().filter(b -> b.role().equals(role)).toList();
    }

    /** Mirrors tackle-srv getResolvedRoleConfig(): active + priority ASC, first wins. */
    public TackleRecords.ResolvedRoleConfig resolve(String role) {
        List<TackleRecords.ResolvedRoleConfig> rows = jdbc.query("""
                SELECT cb.role,
                       m.model_identifier,
                       p.id  AS provider_id,
                       p.name AS provider_name,
                       COALESCE(p.type, '') AS provider_type,
                       p.api_key,
                       COALESCE(cb.endpoint_url, p.endpoint_url) AS endpoint_url,
                       COALESCE(h.name, '') AS harness_name
                FROM config_bundle cb
                JOIN models m          ON cb.model_id = m.id
                LEFT JOIN providers p  ON COALESCE(cb.provider_id, m.provider_id) = p.id
                LEFT JOIN harnesses h  ON COALESCE(cb.harness_id, m.harness_id) = h.id
                WHERE cb.role = ? AND cb.is_active = 1
                ORDER BY cb.priority ASC LIMIT 1
                """, (rs, i) -> new TackleRecords.ResolvedRoleConfig(
                        rs.getString("role"), rs.getString("model_identifier"),
                        rs.getString("provider_id"), rs.getString("provider_name"),
                        rs.getString("provider_type"), decryptApiKey(rs.getString("api_key")),
                        rs.getString("endpoint_url"), rs.getString("harness_name"),
                        fallbacks(role)),
                role);
        return rows.isEmpty() ? null : rows.get(0);
    }

    private List<TackleRecords.ResolvedFallback> fallbacks(String role) {
        return jdbc.query("""
                SELECT cb.priority, m.model_identifier,
                       COALESCE(p.type, '') AS provider_type, p.name AS provider_name
                FROM config_bundle cb
                JOIN models m         ON cb.model_id = m.id
                LEFT JOIN providers p ON COALESCE(cb.provider_id, m.provider_id) = p.id
                WHERE cb.role = ? AND cb.is_active = 1
                ORDER BY cb.priority ASC OFFSET 1
                """, (rs, i) -> new TackleRecords.ResolvedFallback(
                        rs.getInt("priority"), rs.getString("model_identifier"),
                        rs.getString("provider_type"), rs.getString("provider_name")),
                role);
    }
}
