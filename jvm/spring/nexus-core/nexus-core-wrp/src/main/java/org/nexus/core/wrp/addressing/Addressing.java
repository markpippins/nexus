package org.nexus.core.wrp.addressing;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.nexus.core.wrp.WrpException;

/**
 * CAL addressing — Java port of python/nexus_core/wrp/addressing.py
 * (itself a zero-dep port of the archived nbk P5 primitive).
 *
 * Format: cal://{realm}/{graph}/{trajectory}/{node}/{version} where the
 * default version is the first 12 hex chars of SHA-256 over the '|'-joined
 * location path — deterministic, content-addressed, no external state.
 * Golden-vector parity with the Python module is proven in
 * WrpGoldenParityTest.
 */
public final class Addressing {
    private Addressing() {}

    /** Deterministic content hash: first 12 hex chars of SHA-256("|".join(parts)). */
    public static String contentHash(String... parts) {
        String raw = String.join("|", parts);
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder(12);
            for (int i = 0; i < 6; i++) out.append(String.format("%02x", digest[i]));
            return out.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    public static String makeAddress(String realm, String graph, String trajectory, String nodeId, String version) {
        String ver = version != null && !version.isEmpty()
                ? version
                : contentHash(realm + "/" + graph + "/" + trajectory + "/" + nodeId);
        return "cal://" + realm + "/" + graph + "/" + trajectory + "/" + nodeId + "/" + ver;
    }

    /** Parses a CAL address; returns null for non-cal:// or too-short addresses (mirrors Python). */
    public static Map<String, String> parseAddress(String address) {
        if (address == null || !address.startsWith("cal://")) return null;
        String[] parts = address.substring(6).split("/");
        if (parts.length < 4) return null;
        Map<String, String> out = new LinkedHashMap<>();
        out.put("realm", parts[0]);
        out.put("graph", parts[1]);
        out.put("trajectory", parts[2]);
        out.put("node_id", parts[3]);
        out.put("version", parts.length > 4 ? parts[4] : "");
        return out;
    }
}
