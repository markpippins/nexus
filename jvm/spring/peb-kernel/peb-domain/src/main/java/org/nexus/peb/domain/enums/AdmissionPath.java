package org.nexus.peb.domain.enums;

/**
 * Admission path at the PEB Kernel, derived from the MCP tool name on an
 * incoming {@link org.nexus.peb.domain.entity.PebTransaction}. Each path
 * chooses the kernel's audit-row admission_result and (for violations)
 * whether the invariant validator is bypassed.
 *
 * Lives in pebble-domain because both pebble-api (controller) and pebble-core
 * (engine) need to see this enum. Putting it in pebble-api would create a
 * pebble-core -> pebble-api dependency that doesn't exist in the Maven graph.
 */
public enum AdmissionPath {

    /** Read-only / validate-only tools. Persists as ALLOWED on the audit row. */
    VALIDATE,

    /** State-mutating tools (record decision, append trace, route clarification,
     *  propose extension). Full admission + transaction; default ALLOWED. */
    MUTATE,

    /** Tools that are themselves a violation report. Skips invariant validation
     *  — a violation must not pass invariants, that's the point of one — and
     *  persists as REJECTED with admission_result = REJECTED. */
    REPORT_VIOLATION,

    /** Unknown tool name. Kept separate so the audit log records ambiguity
     *  (admission_result = ROUTED) instead of silently treating the call
     *  as a vanilla mutation. */
    UNKNOWN;

    /**
     * Maps the MCP facade tool name (carried in PebTransaction.toolName) to
     * the admission path the kernel should follow. Unknown values fall
     * through to {@link #UNKNOWN} so we still record an audit row.
     */
    public static AdmissionPath fromToolName(String toolName) {
        if (toolName == null) {
            return UNKNOWN;
        }
        switch (toolName) {
            case "peb_validate_transition":
            case "peb_check_invariants":
            case "peb_validate_transform":
                return VALIDATE;

            case "peb_record_decision":
            case "peb_append_trace_segment":
            case "peb_request_clarification":
            case "peb_extension_proposal":
                return MUTATE;

            // wrp git_claim_producer submission path (execution-claim
            // admission). The payload carries execution_claim/execution_evidence,
            // which PebGovernanceEngine routes to the resolution admission
            // adapter; discovered because the harness-bridge e2e gate
            // (previously self-skipping on a bad health probe) first ran
            // against a real kernel and every admission 422'd as an
            // unknown tool. Treat as MUTATE: validator-gated, admitted on
            // the adapter's resolution-side verdict.
            case "peb_admit_git_execution_claim":
                return MUTATE;

            case "peb_report_violation":
                return REPORT_VIOLATION;

            default:
                return UNKNOWN;
        }
    }

    /**
     * The admission_result the kernel writes on the audit row for this path,
     * before validator filtering. The validator can still deny a VALIDATE/MUTATE
     * via its own gate; REPORT_VIOLATION bypasses the validator entirely.
     */
    public AdmissionResult defaultAdmissionResult() {
        switch (this) {
            case VALIDATE:
            case MUTATE:
                return AdmissionResult.ALLOWED;
            case REPORT_VIOLATION:
                return AdmissionResult.REJECTED;
            case UNKNOWN:
            default:
                return AdmissionResult.ROUTED;
        }
    }
}
