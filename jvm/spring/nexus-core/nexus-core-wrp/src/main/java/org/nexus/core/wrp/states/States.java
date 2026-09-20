package org.nexus.core.wrp.states;

import java.util.Map;
import java.util.Set;
import org.nexus.core.wrp.WrpException;

/**
 * WRP state machine primitives — Java mirror of the CANONICAL Python
 * tables (python/nexus_core/wrp/states.py), which are themselves kept in
 * sync with the TypeScript canonical (conduit-mcp receipts.ts,
 * nebula-mcp conduit-wrp-contract.ts). 12 states; COMPLETED and FAILED
 * are terminal (COMPLETED cannot fail).
 */
public final class States {
    private States() {}

    public static final Map<String, Set<String>> WRP_ADJACENCY_MATRIX = Map.ofEntries(
            Map.entry("CREATED", Set.of("INTAKE")),
            Map.entry("INTAKE", Set.of("PLANNING", "FAILED")),
            Map.entry("PLANNING", Set.of("CRITIQUE", "FAILED")),
            Map.entry("CRITIQUE", Set.of("PLANNING", "SPECIFICATION", "FAILED")),
            Map.entry("SPECIFICATION", Set.of("CRITIQUE", "APPROVED", "FAILED")),
            Map.entry("APPROVED", Set.of("SPECIFICATION", "QUEUED", "FAILED")),
            Map.entry("QUEUED", Set.of("EXECUTING", "FAILED")),
            Map.entry("EXECUTING", Set.of("COMPLETED", "FAILED")),
            Map.entry("COMPLETED", Set.of("ARCHIVED")),
            Map.entry("ARCHIVED", Set.of()),
            Map.entry("FAILED", Set.of()));

    /** Conduit receipt type → WRP state (verbatim from the Python canonical). */
    public static final Map<String, String> RECEIPT_TO_WRP_STATE = Map.ofEntries(
            Map.entry("PLANNING", "INTAKE"),
            Map.entry("PLAN_CREATE", "PLANNING"),
            Map.entry("CRITIQUE", "CRITIQUE"),
            Map.entry("CRITIQUE_PASS", "SPECIFICATION"),
            Map.entry("CRITIQUE_REJECT", "PLANNING"),
            Map.entry("IMPLEMENTATION", "EXECUTING"),
            Map.entry("CCNF_EXECUTION", "EXECUTING"),
            Map.entry("REVIEW", "APPROVED"),
            Map.entry("REVIEW_PASS", "COMPLETED"),
            Map.entry("REVIEW_REJECT", "EXECUTING"),
            Map.entry("BLOCK", "FAILED"),
            Map.entry("PLAN_BLOCK", "FAILED"),
            Map.entry("API_LIMIT", "FAILED"),
            Map.entry("HOLD", "QUEUED"),
            Map.entry("REQUEUED", "QUEUED"),
            Map.entry("CANCELLED", "ARCHIVED"),
            Map.entry("ABANDONED", "FAILED"));

    public static boolean isValidTransition(String fromState, String toState) {
        Set<String> allowed = WRP_ADJACENCY_MATRIX.get(fromState);
        return allowed != null && allowed.contains(toState);
    }

    public static String receiptToWrpState(String receiptType) {
        String state = RECEIPT_TO_WRP_STATE.get(receiptType);
        if (state == null) throw new WrpException("Unknown receipt type: " + receiptType);
        return state;
    }
}
