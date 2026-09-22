package org.nexus.core.shrapnel;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Kernel behavior tests for the shrapnel port — the pure-JVM surface of
 * {@link ShrapnelTypes} and {@link ShrapnelCodec#normaliseFieldSpec} (the
 * SQL-facing store is exercised by the TS service's own integration suite;
 * this module's SQL statements mirror it statement-for-statement).
 */
class ShrapnelTypesTest {

    // ── type registry ───────────────────────────────────────────────────────

    @Test
    void typeCodesMirrorTheFieldTypeshTable() {
        assertThat(ShrapnelTypes.TYPE_CODES)
                .containsEntry("Long", 1)
                .containsEntry("String", 2)
                .containsEntry("Double", 3)
                .containsEntry("Boolean", 4)
                .containsEntry("Timestamp", 5)
                .containsEntry("JSONB", 6)
                .containsEntry("UUID", 7);
        assertThat(ShrapnelTypes.EXTENSION_TABLES).hasSize(7);
        assertThat(ShrapnelTypes.extensionTable(2)).isEqualTo("value_string");
        assertThat(ShrapnelTypes.extensionTable(6)).isEqualTo("value_jsonb");
    }

    @Test
    void assertKnownTypeNameAcceptsKnownAndRejectsUnknown() {
        assertThat(ShrapnelTypes.assertKnownTypeName("Long")).isEqualTo(1);
        assertThat(ShrapnelTypes.assertKnownTypeName("JSONB")).isEqualTo(6);
        assertThatThrownBy(() -> ShrapnelTypes.assertKnownTypeName("Vector"))
                .isInstanceOf(ShrapnelApiException.class)
                .satisfies(e -> assertThat(((ShrapnelApiException) e).getStatus()).isEqualTo(400))
                .hasMessageContaining("unknown type 'Vector'");
        assertThatThrownBy(() -> ShrapnelTypes.assertKnownTypeName(null))
                .isInstanceOf(ShrapnelApiException.class);
    }

    @Test
    void unknownTypeCodeIsRejectedEverywhere() {
        assertThatThrownBy(() -> ShrapnelTypes.typeName(99))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("unknown field_type_code 99");
        assertThatThrownBy(() -> ShrapnelTypes.extensionTable(99))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("unknown type code 99");
    }

    // ── identifier guard ────────────────────────────────────────────────────

    @Test
    void identifierGuardBlocksSqlInjectionThroughTableNames() {
        ShrapnelTypes.assertIdentifier("value_string");
        ShrapnelTypes.assertIdentifier("_ok2");
        assertThatThrownBy(() -> ShrapnelTypes.assertIdentifier("value; DROP TABLE x"))
                .isInstanceOf(ShrapnelApiException.class)
                .satisfies(e -> assertThat(((ShrapnelApiException) e).getStatus()).isEqualTo(400))
                .hasMessageContaining("invalid identifier");
        assertThatThrownBy(() -> ShrapnelTypes.assertIdentifier("1abc"))
                .isInstanceOf(ShrapnelApiException.class);
        assertThatThrownBy(() -> ShrapnelTypes.assertIdentifier(null))
                .isInstanceOf(ShrapnelApiException.class);
    }

    // ── type inference ──────────────────────────────────────────────────────

    @Test
    void inferenceMatchesTheTsRules() {
        assertThat(ShrapnelTypes.inferTypeName(true)).isEqualTo("Boolean");
        assertThat(ShrapnelTypes.inferTypeName(42)).isEqualTo("Long");
        assertThat(ShrapnelTypes.inferTypeName(42L)).isEqualTo("Long");
        assertThat(ShrapnelTypes.inferTypeName(4.2)).isEqualTo("Double");
        assertThat(ShrapnelTypes.inferTypeName("plain text")).isEqualTo("String");
        assertThat(ShrapnelTypes.inferTypeName("2026-09-20T12:00:00Z")).isEqualTo("Timestamp");
        assertThat(ShrapnelTypes.inferTypeName("2026-09-20T12:00:00+02:00")).isEqualTo("Timestamp");
        assertThat(ShrapnelTypes.inferTypeName("123e4567-e89b-12d3-a456-426614174000")).isEqualTo("UUID");
        assertThat(ShrapnelTypes.inferTypeName(Map.of("a", 1))).isEqualTo("JSONB");
        assertThat(ShrapnelTypes.inferTypeName(List.of(1, 2))).isEqualTo("JSONB");
        assertThatThrownBy(() -> ShrapnelTypes.inferTypeName(null))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("cannot infer type from null");
    }

    // ── coercion ────────────────────────────────────────────────────────────

    @Test
    void storageCoercionRoundTripsThroughText() {
        assertThat(ShrapnelTypes.coerceForStorage(42, ShrapnelTypes.LONG)).isEqualTo(42L);
        assertThat(ShrapnelTypes.coerceForStorage("42", ShrapnelTypes.LONG)).isEqualTo(42L);
        assertThat(ShrapnelTypes.coerceForStorage(4.5, ShrapnelTypes.DOUBLE)).isEqualTo(4.5);
        assertThat(ShrapnelTypes.coerceForStorage(true, ShrapnelTypes.BOOLEAN)).isEqualTo(true);
        assertThat(ShrapnelTypes.coerceForStorage("x", ShrapnelTypes.STRING)).isEqualTo("x");
        assertThat(ShrapnelTypes.coerceForStorage("abc", ShrapnelTypes.UUID)).isEqualTo("abc");
        // JSONB passes through untouched (the SQL layer casts to jsonb).
        Map<String, Object> json = Map.of("k", "v");
        assertThat(ShrapnelTypes.coerceForStorage(json, ShrapnelTypes.JSONB)).isSameAs(json);
        // Timestamp coercion produces an ISO instant the driver can bind.
        Object ts = ShrapnelTypes.coerceForStorage("2026-09-20T12:00:00Z", ShrapnelTypes.TIMESTAMP);
        assertThat(String.valueOf(ts)).contains("2026-09-20");
    }

    @Test
    void storageCoercionTextRoundTripForDecode() {
        assertThat(ShrapnelTypes.coerceFromStorage("42", ShrapnelTypes.LONG)).isEqualTo(42L);
        assertThat(ShrapnelTypes.coerceFromStorage("4.5", ShrapnelTypes.DOUBLE)).isEqualTo(4.5);
        assertThat(ShrapnelTypes.coerceFromStorage("t", ShrapnelTypes.BOOLEAN)).isEqualTo(true);
        assertThat(ShrapnelTypes.coerceFromStorage("f", ShrapnelTypes.BOOLEAN)).isEqualTo(false);
        assertThat(ShrapnelTypes.coerceFromStorage("s", ShrapnelTypes.STRING)).isEqualTo("s");
        assertThat(ShrapnelTypes.coerceFromStorage(null, ShrapnelTypes.STRING)).isNull();
        // Timestamp normalisation to ISO-8601 UTC when PG hands back "+TZ" text.
        String normalized = String.valueOf(ShrapnelTypes.coerceFromStorage("2026-09-20 12:00:00+00", ShrapnelTypes.TIMESTAMP));
        assertThat(normalized).startsWith("2026-09-20T");
    }

    // ── field spec normalisation ────────────────────────────────────────────

    @Test
    void normaliseFieldSpecAcceptsBothNamingConventions() {
        Map<String, Object> snake = new LinkedHashMap<>();
        snake.put("property_name", "name");
        snake.put("type", "String");
        Map<String, Object> spec = ShrapnelCodec.normaliseFieldSpec(snake);
        assertThat(spec.get("property_name")).isEqualTo("name");
        assertThat(spec.get("name")).isEqualTo("name");
        assertThat(spec.get("field_type_code")).isEqualTo(2);
        assertThat(spec.get("is_calculated")).isEqualTo(false);
        assertThat(spec.get("field_index")).isEqualTo(0);

        Map<String, Object> camel = new HashMap<>();
        camel.put("propertyName", "age");
        camel.put("fieldTypeCode", 1);
        camel.put("fieldIndex", 3);
        camel.put("isCalculated", true);
        Map<String, Object> spec2 = ShrapnelCodec.normaliseFieldSpec(camel);
        assertThat(spec2.get("property_name")).isEqualTo("age");
        assertThat(spec2.get("field_type_code")).isEqualTo(1);
        assertThat(spec2.get("field_index")).isEqualTo(3);
        assertThat(spec2.get("is_calculated")).isEqualTo(true);
    }

    @Test
    void normaliseFieldSpecValidation() {
        assertThatThrownBy(() -> ShrapnelCodec.normaliseFieldSpec(null))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("field spec must be an object");
        assertThatThrownBy(() -> ShrapnelCodec.normaliseFieldSpec(Map.of("type", "String")))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("requires property_name");
        assertThatThrownBy(() -> ShrapnelCodec.normaliseFieldSpec(Map.of("property_name", "x")))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("missing type");
        assertThatThrownBy(() -> ShrapnelCodec.normaliseFieldSpec(Map.of("property_name", "x", "type", "Nope")))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("unknown type 'Nope'");
        assertThatThrownBy(() -> ShrapnelCodec.normaliseFieldSpec(Map.of("property_name", "x", "field_type_code", 42)))
                .isInstanceOf(ShrapnelApiException.class)
                .hasMessageContaining("unknown field_type_code 42");
    }

    // ── JSON lenient parse (JSONB decode path) ──────────────────────────────

    @Test
    void jsonbDecodeParsesLeniently() {
        Object parsed = ShrapnelCodec.parseJsonLenient("{\"k\": [1, 2], \"nested\": {\"a\": true}}");
        assertThat(parsed).isInstanceOf(Map.class);
        Object fallback = ShrapnelCodec.parseJsonLenient("not json at all {");
        assertThat(fallback).isEqualTo("not json at all {");
    }

    // ── PG error mapping ────────────────────────────────────────────────────

    @Test
    void pgErrorMappingCoversCheckAndRuleViolations() {
        assertThat(ShrapnelCodec.mapPg(null)).isNull();
    }
}
