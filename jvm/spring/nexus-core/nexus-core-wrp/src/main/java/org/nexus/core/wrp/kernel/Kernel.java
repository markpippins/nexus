package org.nexus.core.wrp.kernel;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.nexus.core.wrp.WrpException;

/**
 * Kernel data types — Java port of python/nexus_core/wrp/kernel.py.
 * Zero dependencies on conduit/tackle internals; pure data any component
 * can consume. Immutability by construction replaces Python frozen
 * dataclasses. Errors are first-class lineage nodes, not exceptions.
 */
public final class Kernel {
    private Kernel() {}

    /** One atomic batch of state change (idempotent; version monotonic, no gaps). */
    public record KernelDelta(String deltaId, String batchId, List<Map<String, Object>> receipts,
                              Set<String> affectedPlans, Set<String> invalidatedPlans, long version) {
        public KernelDelta {
            if (deltaId == null || deltaId.isEmpty()) throw new WrpException("delta_id is required");
            if (version < 0) throw new WrpException("version must be >= 0, got " + version);
            receipts = receipts == null ? List.of() : List.copyOf(receipts);
            affectedPlans = affectedPlans == null ? Set.of() : Set.copyOf(affectedPlans);
            invalidatedPlans = invalidatedPlans == null ? Set.of() : Set.copyOf(invalidatedPlans);
        }
    }

    /** A sequenced list of KernelDeltas ready for replay. */
    public record KernelDeltaBatch(String batchId, List<KernelDelta> deltas, String sourceHash) {
        public KernelDeltaBatch {
            deltas = deltas == null ? List.of() : List.copyOf(deltas);
        }
        public int totalReceipts() {
            return deltas.stream().mapToInt(d -> d.receipts().size()).sum();
        }
    }

    /** Error classifications (kernel lineage node types). */
    public static final String INVARIANT_VIOLATION = "INVARIANT_VIOLATION";
    public static final String IDENTITY_CONFLICT = "IDENTITY_CONFLICT";
    public static final String GRAPH_CYCLE = "GRAPH_CYCLE";
    public static final String VERSION_MISMATCH = "VERSION_MISMATCH";
    public static final String INVALID_TRANSITION = "INVALID_TRANSITION";
    public static final String VALIDATION_ERROR = "VALIDATION_ERROR";

    /** A first-class error node in the kernel lineage graph — recorded, not thrown. */
    public record KernelError(String type, String message, List<String> affectedNodes,
                              boolean recoverable, String step) {
        public static KernelError of(String type, String message, String step) {
            return new KernelError(type, message, List.of(), false, step);
        }
    }

    /** Result of a reduce() call — value or error, never both. */
    public record KernelResult(KernelState value, KernelError error, String lineageEventId) {
        public boolean isOk() { return value != null && error == null; }
        public boolean isError() { return error != null; }

        public static KernelResult ok(KernelState state, String lineageEventId) {
            return new KernelResult(state, null, lineageEventId);
        }
        public static KernelResult err(KernelError error, String lineageEventId) {
            return new KernelResult(null, error, lineageEventId);
        }
    }

    /** Versioned checkpoint of KernelState for accelerated reconstruction. */
    public record KernelSnapshot(long version, Map<String, Object> state, String identityHash,
                                 String graphHash, Long lineageCursor, Map<String, Object> metadata) {}

    /**
     * The composite kernel state: versioned receipts, plans, transitions,
     * graph, identity, lineage. Each reduce produces a new state with an
     * incremented version (no-op deltas do not increment).
     */
    public static final class KernelState {
        private final long version;
        private final Map<String, Map<String, Object>> receipts;
        private final Set<String> plans;
        private final List<Map<String, Object>> transitions;
        private final long lineageSequence;

        public KernelState() {
            this(0, new LinkedHashMap<>(), new LinkedHashSet<>(), new ArrayList<>(), 0L);
        }

        private KernelState(long version, Map<String, Map<String, Object>> receipts, Set<String> plans,
                            List<Map<String, Object>> transitions, long lineageSequence) {
            this.version = version;
            this.receipts = receipts;
            this.plans = plans;
            this.transitions = transitions;
            this.lineageSequence = lineageSequence;
        }

        public long version() { return version; }
        public Map<String, Map<String, Object>> receipts() { return Map.copyOf(receipts); }
        public Set<String> plans() { return Set.copyOf(plans); }
        public List<Map<String, Object>> transitions() { return List.copyOf(transitions); }
        public long lineageSequence() { return lineageSequence; }

        public KernelState withVersion(long next) {
            return new KernelState(next, new LinkedHashMap<>(receipts), new LinkedHashSet<>(plans),
                    new ArrayList<>(transitions), lineageSequence);
        }

        public KernelState withApplied(String receiptId, Map<String, Object> receipt, Map<String, Object> transition) {
            Map<String, Map<String, Object>> nextReceipts = new LinkedHashMap<>(receipts);
            nextReceipts.put(receiptId, receipt);
            Set<String> nextPlans = new LinkedHashSet<>(plans);
            Object planId = transition.get("plan_id");
            if (planId != null) nextPlans.add(String.valueOf(planId));
            List<Map<String, Object>> nextTransitions = new ArrayList<>(transitions);
            nextTransitions.add(transition);
            return new KernelState(version, nextReceipts, nextPlans, nextTransitions, lineageSequence);
        }

        public KernelState withLineage(long nextSequence) {
            return new KernelState(version, new LinkedHashMap<>(receipts), new LinkedHashSet<>(plans),
                    new ArrayList<>(transitions), nextSequence);
        }
    }
}
