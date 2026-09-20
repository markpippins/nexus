package org.nexus.core.wrp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.nexus.core.wrp.addressing.Addressing;
import org.nexus.core.wrp.identity.CcnfIdentity;
import org.nexus.core.wrp.states.States;

/**
 * Byte-exact cross-runtime parity against Python-generated golden vectors
 * (wrp-golden-vectors.json, produced by generate-golden-fixtures.py from
 * the Python zero-dep substrate — itself byte-identical to the Go ccnf-ref
 * and guarded by wr-conf-010/011).
 *
 * Proves per vector:
 *   1. CAL addressing (make/parse/content_hash) incl. unicode + explicit versions.
 *   2. CCNF CanonicalJSON byte shapes (Go serializer rules).
 *   3. entity_key derivation via the emission path (normalize + derive),
 *      per-domain scopes, and the Go error contract for bad intents.
 *   4. The 12-state adjacency matrix and receipt→state mapping.
 */
class WrpGoldenParityTest {
    private static JsonNode vectors;

    @BeforeAll
    static void loadVectors() throws IOException {
        try (InputStream stream = WrpGoldenParityTest.class.getResourceAsStream("/wrp-golden-vectors.json")) {
            if (stream == null) throw new IOException("wrp-golden-vectors.json missing from test resources");
            vectors = new ObjectMapper().readTree(stream);
        }
    }

    // ---- 1. CAL addressing parity ---------------------------------------

    @Test
    void calAddressingMatchesPythonGoldenValues() {
        for (JsonNode vector : vectors.get("addressing")) {
            String realm = vector.get("realm").asText();
            String graph = vector.get("graph").asText();
            String trajectory = vector.get("trajectory").asText();
            String node = vector.get("node_id").asText();
            assertEquals(vector.get("expected_address").asText(),
                    Addressing.makeAddress(realm, graph, trajectory, node, null),
                    "address drift for " + realm + "/" + graph);
            assertEquals(vector.get("expected_content_hash").asText(),
                    Addressing.contentHash(realm + "/" + graph + "/" + trajectory + "/" + node));
            assertEquals(vector.get("explicit_version_address").asText(),
                    Addressing.makeAddress(realm, graph, trajectory, node, "v9"));
            Map<String, String> parsed = Addressing.parseAddress(vector.get("expected_address").asText());
            JsonNode expectedParsed = vector.get("parsed");
            expectedParsed.properties().forEach(e ->
                    assertEquals(e.getValue().asText(), parsed.get(e.getKey()), "parsed field " + e.getKey()));
            assertNull(Addressing.parseAddress(vector.get("parsed_non_cal").asText()));
        }
    }

    // ---- 2. CCNF canonical JSON byte parity ------------------------------

    @Test
    void ccnfCanonicalJsonIsByteIdenticalToPython() {
        for (JsonNode vector : vectors.get("canonical_json_bytes")) {
            String java = CcnfIdentity.canonicalJson(toJavaValue(vector.get("value")));
            assertEquals(vector.get("expected_json").asText(), java,
                    "canonical byte mismatch for: " + java);
            assertEquals(vector.get("expected_sha256").asText(),
                    org.nexus.core.wrp.serialization.Sha256.sha256Hex(java));
        }
    }

    // ---- 3. identity / entity_key parity ---------------------------------

    @Test
    void entityKeysMatchPythonEmissionPath() {
        for (JsonNode vector : vectors.get("identity")) {
            @SuppressWarnings("unchecked")
            Map<String, Object> doc = (Map<String, Object>) toJavaValue(vector.get("doc"));
            CcnfIdentity.Identity identity = CcnfIdentity.emitIdentity(doc);
            assertEquals(vector.get("expected_entity_key").asText(), identity.entityKey(),
                    "entity_key drift for doc " + vector.get("doc").get("event_id").asText());
            assertEquals(vector.get("expected_event_type").asText(), identity.eventType());
            assertEquals(vector.get("expected_scope").asText(), identity.scope());
        }
    }

    @Test
    void intentErrorContractMatchesPython() {
        for (JsonNode vector : vectors.get("identity_errors")) {
            WrpException exception = assertThrows(WrpException.class,
                    () -> CcnfIdentity.normalizeIntent(toJavaValue(vector.get("intent"))),
                    "expected normalization failure: " + vector.get("label").asText());
            assertEquals(vector.get("expected_error").asText(), exception.getMessage());
        }
    }

    // ---- 4. state machine parity -----------------------------------------

    @Test
    void adjacencyMatrixAndReceiptMapMatchCanonical() {
        JsonNode matrix = vectors.get("states").get("matrix");
        assertEquals(matrix.size(), States.WRP_ADJACENCY_MATRIX.size(), "state count drift");
        matrix.properties().forEach(entry -> {
            List<String> expected = new ArrayList<>();
            entry.getValue().forEach(n -> expected.add(n.asText()));
            assertEquals(expected, States.WRP_ADJACENCY_MATRIX.get(entry.getKey()).stream().sorted().toList(),
                    "adjacency drift for state " + entry.getKey());
        });
        JsonNode receiptMap = vectors.get("states").get("receipt_map");
        assertEquals(receiptMap.size(), States.RECEIPT_TO_WRP_STATE.size(), "receipt mapping count drift");
        receiptMap.properties().forEach(entry ->
                assertEquals(entry.getValue().asText(), States.RECEIPT_TO_WRP_STATE.get(entry.getKey()),
                        "receipt mapping drift for " + entry.getKey()));
    }

    @Test
    void transitionVectorsMatch() {
        for (JsonNode vector : vectors.get("state_transitions")) {
            boolean valid = States.isValidTransition(vector.get("from").asText(), vector.get("to").asText());
            assertEquals(vector.get("valid").asBoolean(), valid,
                    "transition drift: " + vector.get("from").asText() + " → " + vector.get("to").asText());
        }
    }

    // ---- helpers ----------------------------------------------------------

    private static Object toJavaValue(JsonNode node) {
        if (node == null || node.isNull()) return null;
        if (node.isBoolean()) return node.asBoolean();
        if (node.isInt() || node.isLong()) return node.asLong();
        if (node.isFloatingPointNumber()) return node.asDouble();
        if (node.isTextual()) return node.asText();
        if (node.isArray()) {
            List<Object> out = new ArrayList<>();
            node.forEach(child -> out.add(toJavaValue(child)));
            return out;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        node.properties().forEach(entry -> out.put(entry.getKey(), toJavaValue(entry.getValue())));
        return out;
    }
}
