package org.nexus.core.wrp.engine;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.nexus.core.wrp.kernel.Kernel;
import org.nexus.core.wrp.states.States;

/**
 * KernelEngine — Java port of the conduit wrp_kernel engine's reduce
 * pipeline (pure function: same input → same output, no IO, no side
 * effects). Steps per delta: materialize receipts → validate transitions
 * against the canonical adjacency matrix → apply → record lineage → commit
 * (version increment). Invalid transitions produce first-class KernelError
 * results (INVALID_TRANSITION), never thrown exceptions, mirroring the
 * reference error model. No-op deltas do not increment version.
 */
public final class KernelEngine {
    private KernelEngine() {}

    public static Kernel.KernelResult reduce(Kernel.KernelState state, Kernel.KernelDelta delta) {
        String lineageId = "lineage-" + state.version() + "-" + delta.deltaId();

        if (delta.receipts().isEmpty()) {
            // No-op delta: lineage recorded, version NOT incremented.
            return Kernel.KernelResult.ok(state.withLineage(state.lineageSequence() + 1), lineageId);
        }

        Kernel.KernelState next = state.withVersion(state.version() + 1);
        for (Map<String, Object> receipt : delta.receipts()) {
            String receiptId = String.valueOf(receipt.getOrDefault("receipt_id", "r-" + next.version() + "-" + next.transitions().size()));
            String receiptType = String.valueOf(receipt.getOrDefault("type", ""));
            String fromState = String.valueOf(receipt.getOrDefault("from_state", "CREATED"));
            String targetState;
            try {
                targetState = States.receiptToWrpState(receiptType);
            } catch (org.nexus.core.wrp.WrpException e) {
                return Kernel.KernelResult.err(Kernel.KernelError.of(Kernel.VALIDATION_ERROR, e.getMessage(), "materialize"), lineageId);
            }
            String planId = String.valueOf(receipt.getOrDefault("plan_id", ""));

            if (!fromState.isEmpty() && !States.isValidTransition(fromState, targetState)) {
                return Kernel.KernelResult.err(Kernel.KernelError.of(
                        Kernel.INVALID_TRANSITION,
                        "invalid transition " + fromState + " → " + targetState + " (" + receiptType + ")",
                        "validate"), lineageId);
            }

            Map<String, Object> transition = new LinkedHashMap<>();
            transition.put("plan_id", planId);
            transition.put("receipt_id", receiptId);
            transition.put("receipt_type", receiptType);
            transition.put("from_state", fromState);
            transition.put("to_state", targetState);
            next = next.withApplied(receiptId, receipt, transition);
        }

        return Kernel.KernelResult.ok(next, lineageId);
    }

    /** Apply an ordered batch; short-circuits on the first error (replay-safe ordering). */
    public static Kernel.KernelResult reduceBatch(Kernel.KernelState state, Kernel.KernelDeltaBatch batch) {
        Kernel.KernelState current = state;
        String lastLineage = null;
        for (Kernel.KernelDelta delta : batch.deltas()) {
            Kernel.KernelResult result = reduce(current, delta);
            if (result.isError()) return result;
            current = result.value();
            lastLineage = result.lineageEventId();
        }
        return Kernel.KernelResult.ok(current, lastLineage);
    }

    /** Snapshot the state (accelerated reconstruction input). */
    public static Kernel.KernelSnapshot snapshot(Kernel.KernelState state, Map<String, Object> metadata) {
        Map<String, Object> serialized = new LinkedHashMap<>();
        serialized.put("version", state.version());
        serialized.put("plans", new ArrayList<>(state.plans()));
        serialized.put("transitions", state.transitions());
        return new Kernel.KernelSnapshot(state.version(), serialized, null, null, state.lineageSequence(), metadata);
    }
}
