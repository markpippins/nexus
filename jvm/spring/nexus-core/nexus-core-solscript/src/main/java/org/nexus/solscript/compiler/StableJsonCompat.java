package org.nexus.solscript.compiler;

import org.nexus.solscript.models.StableJson;

/** Bridges expression-compiler's stableHash to the shared StableJson. */
public final class StableJsonCompat {
    private StableJsonCompat() {}

    public static String stringify(Object value) {
        return StableJson.stringify(value);
    }
}