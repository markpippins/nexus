package org.nexus.core.aegis.rest;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.nexus.core.aegis.kernel.AegisDigest;
import org.nexus.core.aegis.kernel.ModelChecker;
import org.nexus.core.aegis.store.AegisStore;
import org.nexus.core.aegis.store.AegisStore.NoFieldsException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * JVM port of the aegis-srv REST surface (routes.ts) — namespaced under
 * /api/aegis so the read-only monolith can host it alongside the other
 * projection modules. Response shapes, status codes, and error bodies match
 * the TS service; routes are declared with literal paths so the TypeSpec
 * reconciler and the apidocs Spring extractor can statically prove coverage
 * (contract-first convention).
 */
@RestController
@RequestMapping("/api/aegis")
public class AegisController {

    private static final Logger log = LoggerFactory.getLogger(AegisController.class);

    private final AegisStore store;

    public AegisController(AegisStore store) {
        this.store = store;
    }

    // ── Error mapping ────────────────────────────────────────────────────

    private static ResponseEntity<Map<String, Object>> err(int status, String message) {
        return ResponseEntity.status(status)
                .body(Map.of("error", message, "message", message));
    }

    private static ResponseEntity<Map<String, Object>> pgError(DataAccessException e) {
        String sqlState = (e.getCause() instanceof java.sql.SQLException sql) ? sql.getSQLState() : null;
        AegisStore.PgError mapped = AegisStore.mapPgError(sqlState);
        if (mapped.unmapped()) {
            log.warn("[aegis] unmapped DB error (sqlState={})", sqlState, e);
        }
        return err(mapped.status(), mapped.message());
    }

    private ResponseEntity<Map<String, Object>> requireRegistry(String id) {
        if (!AegisStore.isUuid(id)) return err(400, "invalid registry id");
        if (store.registryExists(id) == null) return err(404, "registry not found");
        return null;
    }

    private ResponseEntity<Map<String, Object>> requireChild(String table, String registryId, String cid) {
        if (!AegisStore.isUuid(cid)) return err(400, "invalid child id");
        if (!store.childExists(table, registryId, cid)) return err(404, table + " not found");
        return null;
    }

    // ── Registries (root CRUD) ───────────────────────────────────────────

    @GetMapping("/registries")
    public ResponseEntity<?> listRegistries() {
        try {
            return ResponseEntity.ok(Map.of("items", store.listRegistries()));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @GetMapping("/registries/name/{name}")
    public ResponseEntity<?> registryByName(@PathVariable String name) {
        try {
            Map<String, Object> row = store.registryByName(name);
            if (row == null) return err(404, "registry not found");
            return ResponseEntity.ok(row);
        } catch (DataAccessException e) { return pgError(e); }
    }

    @PostMapping("/registries")
    public ResponseEntity<?> createRegistry(@RequestBody(required = false) Map<String, Object> body) {
        try {
            return ResponseEntity.status(201).body(store.createRegistry(body));
        } catch (NoFieldsException e) {
            return err(400, "no fields provided");
        } catch (DataAccessException e) { return pgError(e); }
    }

    @GetMapping("/registries/{id}")
    public ResponseEntity<?> getRegistry(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(store.registryById(id));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @PatchMapping("/registries/{id}")
    public ResponseEntity<?> patchRegistry(@PathVariable String id,
                                           @RequestBody(required = false) Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(store.updateRegistry(id, body));
        } catch (NoFieldsException e) {
            return err(400, "no fields provided");
        } catch (DataAccessException e) { return pgError(e); }
    }

    /** Soft delete: is_active = false (schema partial unique index on active name). */
    @DeleteMapping("/registries/{id}")
    public ResponseEntity<?> deleteRegistry(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            store.softDeleteRegistry(id);
            return ResponseEntity.ok(Map.of("deleted", id));
        } catch (DataAccessException e) { return pgError(e); }
    }

    // ── Immutable registry revisions (Phase A) ───────────────────────────

    @GetMapping("/registries/{id}/revisions")
    public ResponseEntity<?> listRevisions(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(Map.of("items", store.listRevisions(id)));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @PostMapping("/registries/{id}/revisions")
    public ResponseEntity<?> createRevision(@PathVariable String id,
                                            @RequestBody(required = false) Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            String createdBy = body != null && body.get("created_by") != null
                    ? String.valueOf(body.get("created_by")) : null;
            return ResponseEntity.status(201).body(store.createRegistryRevision(id, createdBy));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @GetMapping("/registries/{id}/revisions/{rid}")
    public ResponseEntity<?> getRevision(@PathVariable String id, @PathVariable String rid) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            if (!AegisStore.isUuid(rid)) return err(400, "invalid revision id");
            Map<String, Object> revision = store.getRegistryRevision(id, rid);
            if (revision == null) return err(404, "revision not found");
            return ResponseEntity.ok(revision);
        } catch (DataAccessException e) { return pgError(e); }
    }

    // ── Validate ─────────────────────────────────────────────────────────

    @PostMapping("/registries/{id}/validate")
    public ResponseEntity<?> validate(@PathVariable String id,
                                      @RequestBody(required = false) Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            Map<String, Object> registry = store.registryById(id);
            List<Map<String, Object>> errors = new ArrayList<>();
            List<Map<String, Object>> warnings = new ArrayList<>();
            List<Map<String, Object>> suggestions = new ArrayList<>();
            if (registry.get("name") == null) {
                errors.add(Map.of("code", "missing_name", "message", "registry has no name"));
            }
            if (registry.get("version") == null) {
                warnings.add(Map.of("code", "missing_version", "message",
                        "registry has no version, defaulting to 1.0.0"));
            }
            String validatedBy = body != null && body.get("validated_by") != null
                    ? String.valueOf(body.get("validated_by")) : null;
            Map<String, Object> result = store.insertValidationResult(id, errors.isEmpty(),
                    errors, warnings, suggestions, validatedBy);
            return ResponseEntity.status(201).body(result);
        } catch (DataAccessException e) { return pgError(e); }
    }

    // ── Model-check ──────────────────────────────────────────────────────

    @PostMapping("/registries/{id}/model-check")
    public ResponseEntity<?> modelCheck(@PathVariable String id,
                                        @RequestBody(required = false) Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            long started = System.currentTimeMillis();
            Map<String, Object> b = body == null ? Map.of() : body;

            Map<String, Object> revision;
            if (b.get("revision_id") != null) {
                if (!AegisStore.isUuid(String.valueOf(b.get("revision_id")))) {
                    return err(400, "invalid revision id");
                }
                revision = store.getRegistryRevision(id, String.valueOf(b.get("revision_id")));
                if (revision == null) return err(404, "revision not found");
            } else {
                String createdBy = b.get("checked_by") != null ? String.valueOf(b.get("checked_by")) : null;
                revision = store.createRegistryRevision(id, createdBy);
            }

            Object snapshotObj = b.getOrDefault("input_snapshot", Map.of());
            String inputSnapshotDigest = AegisDigest.digestJson(snapshotObj);
            if (b.get("input_snapshot_digest") != null
                    && !b.get("input_snapshot_digest").equals(inputSnapshotDigest)) {
                return err(400, "input_snapshot_digest does not match input_snapshot");
            }

            ModelChecker.McModel model = modelFromRevision(revision);
            String tlaSource = revision.get("source") == null ? "" : String.valueOf(revision.get("source"));
            boolean hasTlaSource = !tlaSource.isBlank();
            String engine = hasTlaSource ? "tlc" : "structural";
            String engineVersion = hasTlaSource ? "tla2tools.jar" : "aegis-structural-checker-v1";

            List<String> checkedProperties = new ArrayList<>();
            String checkerStatus;
            String reason;
            Map<String, Object> trace = null;

            if (hasTlaSource) {
                // TLC is failure-isolated: without the tla2tools.jar artifact this
                // stays an `error`-status result, never a 500 (mirrors the TS
                // unavailable branch via mapCheckerOutcome).
                try {
                    org.nexus.core.aegis.kernel.TlcRunner.TlcResult tlc =
                            org.nexus.core.aegis.kernel.TlcRunner.run(
                            "AegisModule", tlaSource,
                            model.invariants().stream().map(ModelChecker.McInvariant::name).toList(),
                            model.properties().stream().map(ModelChecker.McProperty::name).toList(),
                            30_000L);
                    if (tlc == null) {
                        checkerStatus = "error";
                        reason = "tla2tools.jar not available in this deployment profile";
                    } else {
                        checkerStatus = tlc.status();
                        trace = tlc.trace() == null ? null : AegisDigest.obj(
                                "engine", engine, "steps", tlc.trace(), "violated", tlc.violated());
                        reason = tlc.violated() != null ? "violated: " + tlc.violated()
                                : (tlc.summary() != null ? tlc.summary() : "TLC completed");
                    }
                } catch (Exception ex) {
                    checkerStatus = "error";
                    reason = "TLC invocation failed: " + ex.getMessage();
                }
            } else {
                ModelChecker.Report report = ModelChecker.checkModel(model);
                checkerStatus = "success".equals(report.status()) ? "success" : "failure";
                for (ModelChecker.Verdict v : report.verdicts()) {
                    checkedProperties.add(v.kind() + ":" + v.name() + "=" + v.result()
                            + (v.type() != null ? "(" + v.type() + ")" : "") + ": " + v.detail());
                }
                if (!report.unreachableStates().isEmpty()) {
                    checkedProperties.add("unreachable:" + String.join(",", report.unreachableStates()));
                }
                trace = report.deadlockTrace() != null
                        ? AegisDigest.obj("engine", engine, "deadlock", report.deadlockTrace(),
                                "errors", report.errors())
                        : null;
                reason = (String.join("; ", report.errors()) + " " + String.join("; ", report.warnings()))
                        .trim();
                if (reason.isBlank()) reason = "Structural analysis is not a formal proof";
            }

            AegisDigest.TruthfulOutcome outcome = AegisDigest.mapCheckerOutcome(engine, checkerStatus, reason);
            checkedProperties.add("truthful_status:" + outcome.status());
            checkedProperties.add("liveness_status:" + outcome.livenessStatus());

            String checkerConfigDigest = AegisDigest.digestJson(AegisDigest.obj(
                    "init", "Init", "next", "Next",
                    "invariants", model.invariants().stream().map(ModelChecker.McInvariant::name).toList(),
                    "properties", model.properties().stream().map(ModelChecker.McProperty::name).toList()));
            Map<String, Object> resultMaterial = AegisDigest.obj(
                    "registry_revision_id", String.valueOf(revision.get("id")),
                    "engine", engine, "engine_version", engineVersion,
                    "checker_config_digest", checkerConfigDigest,
                    "source_digest", String.valueOf(revision.get("source_digest")),
                    "model_digest", String.valueOf(revision.get("model_digest")),
                    "input_snapshot_digest", inputSnapshotDigest,
                    "status", outcome.status(), "safety_status", outcome.safetyStatus(),
                    "liveness_status", outcome.livenessStatus(), "trace", trace,
                    "checked_properties", checkedProperties);
            String resultDigest = AegisDigest.digestJson(resultMaterial);
            String checkedBy = b.get("checked_by") != null
                    && AegisStore.isUuid(String.valueOf(b.get("checked_by")))
                    ? String.valueOf(b.get("checked_by")) : null;

            Map<String, Object> row = AegisDigest.obj(
                    "registry_id", java.util.UUID.fromString(id),
                    "registry_revision_id", java.util.UUID.fromString(String.valueOf(revision.get("id"))),
                    "property_id", b.get("property_id") != null
                            ? java.util.UUID.fromString(String.valueOf(b.get("property_id"))) : null,
                    "status", outcome.status(),
                    "trace", trace,
                    "checked_properties", checkedProperties,
                    "execution_time_ms", System.currentTimeMillis() - started,
                    "checked_by", checkedBy != null ? java.util.UUID.fromString(checkedBy) : null,
                    "engine", engine, "engine_version", engineVersion,
                    "checker_config_digest", checkerConfigDigest,
                    "source_digest", String.valueOf(revision.get("source_digest")),
                    "model_digest", String.valueOf(revision.get("model_digest")),
                    "input_snapshot_digest", inputSnapshotDigest,
                    "result_digest", resultDigest,
                    "safety_status", outcome.safetyStatus(),
                    "liveness_status", outcome.livenessStatus(),
                    "authority_level", outcome.authorityLevel(),
                    "reason", outcome.reason());
            return ResponseEntity.status(201).body(store.insertModelCheckResult(row));
        } catch (DataAccessException e) { return pgError(e); }
    }

    /** Rebuild the structured checker model from a revision snapshot. */
    private static ModelChecker.McModel modelFromRevision(Map<String, Object> revision) {
        Map<String, Object> model = revision.get("model") instanceof Map<?, ?> m
                ? castMap(m) : Map.of();
        List<ModelChecker.McState> states = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("states"))) {
            Map<?, ?> s = (Map<?, ?>) o;
            states.add(new ModelChecker.McState(
                    str(s.get("id")), str(s.get("name")),
                    Boolean.TRUE.equals(s.get("is_initial")),
                    Boolean.TRUE.equals(s.get("is_terminal"))));
        }
        List<ModelChecker.McTransition> transitions = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("transitions"))) {
            Map<?, ?> t = (Map<?, ?>) o;
            transitions.add(new ModelChecker.McTransition(
                    str(t.get("id")), str(t.get("name")),
                    strOrNull(t.get("from_state_id")), strOrNull(t.get("to_state_id")),
                    strOrNull(t.get("guard_expression")),
                    Boolean.TRUE.equals(t.get("weak_fairness")),
                    Boolean.TRUE.equals(t.get("strong_fairness")),
                    t.get("priority") instanceof Number n ? n.intValue() : null));
        }
        List<ModelChecker.McInvariant> invariants = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("invariants"))) {
            Map<?, ?> i = (Map<?, ?>) o;
            invariants.add(new ModelChecker.McInvariant(str(i.get("id")), str(i.get("name")),
                    str(i.get("expression")), Boolean.TRUE.equals(i.get("is_type_invariant"))));
        }
        List<ModelChecker.McProperty> properties = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("properties"))) {
            Map<?, ?> p = (Map<?, ?>) o;
            properties.add(new ModelChecker.McProperty(str(p.get("id")), str(p.get("name")),
                    str(p.get("type")), str(p.get("expression"))));
        }
        List<ModelChecker.McTemporalProperty> temporals = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("temporal_properties"))) {
            Map<?, ?> t = (Map<?, ?>) o;
            temporals.add(new ModelChecker.McTemporalProperty(str(t.get("id")), str(t.get("name")),
                    str(t.get("operator")), str(t.get("expression"))));
        }
        List<String> variables = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("variables"))) {
            variables.add(o instanceof Map<?, ?> v ? str(v.get("name")) : str(o));
        }
        List<String> constants = new ArrayList<>();
        for (Object o : listOrEmpty(model.get("constants"))) {
            constants.add(o instanceof Map<?, ?> c ? str(c.get("name")) : str(o));
        }
        return new ModelChecker.McModel(states, transitions, invariants, properties, temporals,
                variables, constants);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Map<?, ?> m) {
        return (Map<String, Object>) m;
    }

    private static List<Object> listOrEmpty(Object o) {
        return o instanceof List<?> l ? new ArrayList<Object>(l) : List.of();
    }

    private static String str(Object o) { return o == null ? null : String.valueOf(o); }
    private static String strOrNull(Object o) { return o == null ? null : String.valueOf(o); }

    // ── Results listings ─────────────────────────────────────────────────

    @GetMapping("/registries/{id}/validation-results")
    public ResponseEntity<?> validationResults(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(Map.of("items", store.listValidationResults(id)));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @GetMapping("/registries/{id}/model-check-results")
    public ResponseEntity<?> modelCheckResults(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(Map.of("items", store.listModelCheckResults(id)));
        } catch (DataAccessException e) { return pgError(e); }
    }

    // ── Wind compilations ────────────────────────────────────────────────

    @GetMapping("/registries/{id}/wind-compilations")
    public ResponseEntity<?> listWindCompilations(@PathVariable String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(Map.of("items", store.listWindCompilations(id)));
        } catch (DataAccessException e) { return pgError(e); }
    }

    @GetMapping("/registries/{id}/wind-compilations/{cid}")
    public ResponseEntity<?> getWindCompilation(@PathVariable String id, @PathVariable String cid) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            if (!AegisStore.isUuid(cid)) return err(400, "invalid compilation id");
            Map<String, Object> row = store.getWindCompilation(id, cid);
            if (row == null) return err(404, "Wind compilation not found");
            return ResponseEntity.ok(row);
        } catch (DataAccessException e) { return pgError(e); }
    }

    /**
     * Wind compilation creation is intentionally not exposed on the JVM
     * profile yet: the TS flow writes wind.workflows / workflow_versions /
     * nodes / edges inside one transaction and returns full lineage, which
     * crosses into the wind schema's write path — deferred under the monolith's
     * read-only posture. The pure plan builder (WindCompiler) is ported and
     * tested; flagging the gap in the completion record.
     */
    @PostMapping("/registries/{id}/wind-compilations")
    public ResponseEntity<?> createWindCompilation(@PathVariable String id,
                                                   @RequestBody(required = false) Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            String workflowId = body == null || body.get("wind_workflow_id") == null
                    ? "" : String.valueOf(body.get("wind_workflow_id"));
            if (!AegisStore.isUuid(workflowId)) {
                return err(400, "wind_workflow_id is required and must be a UUID");
            }
            return err(501, "wind compilation write path not available in this deployment profile");
        } catch (DataAccessException e) { return pgError(e); }
    }

    // ── Child resources (childHandlers × 11 tables) ──────────────────────

    private ResponseEntity<?> childList(String table, String id) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.ok(Map.of("items", store.listChildren(table, id)));
        } catch (DataAccessException e) { return pgError(e); }
    }

    private ResponseEntity<?> childCreate(String table, String id, Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            return ResponseEntity.status(201)
                    .body(store.createChild(table, id, new LinkedHashMap<>(body)));
        } catch (NoFieldsException e) {
            return err(400, "no fields provided");
        } catch (DataAccessException e) { return pgError(e); }
    }

    private ResponseEntity<?> childGet(String table, String id, String cid) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            ResponseEntity<Map<String, Object>> childGuard = requireChild(table, id, cid);
            if (childGuard != null) return childGuard;
            return ResponseEntity.ok(store.getChild(table, id, cid));
        } catch (DataAccessException e) { return pgError(e); }
    }

    private ResponseEntity<?> childUpdate(String table, String id, String cid, Map<String, Object> body) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            ResponseEntity<Map<String, Object>> childGuard = requireChild(table, id, cid);
            if (childGuard != null) return childGuard;
            return ResponseEntity.ok(store.updateChild(table, id, cid, new LinkedHashMap<>(body)));
        } catch (NoFieldsException e) {
            return err(400, "no fields provided");
        } catch (DataAccessException e) { return pgError(e); }
    }

    private ResponseEntity<?> childDelete(String table, String id, String cid) {
        try {
            ResponseEntity<Map<String, Object>> guard = requireRegistry(id);
            if (guard != null) return guard;
            ResponseEntity<Map<String, Object>> childGuard = requireChild(table, id, cid);
            if (childGuard != null) return childGuard;
            store.deleteChild(table, id, cid);
            return ResponseEntity.ok(Map.of("deleted", cid));
        } catch (DataAccessException e) { return pgError(e); }
    }

    // constants
    @GetMapping("/registries/{id}/constants")
    public ResponseEntity<?> constantsList(@PathVariable String id) { return childList("constant", id); }

    @PostMapping("/registries/{id}/constants")
    public ResponseEntity<?> constantsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("constant", id, body); }

    @GetMapping("/registries/{id}/constants/{cid}")
    public ResponseEntity<?> constantsGet(@PathVariable String id, @PathVariable String cid) { return childGet("constant", id, cid); }

    @PatchMapping("/registries/{id}/constants/{cid}")
    public ResponseEntity<?> constantsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("constant", id, cid, body); }

    @DeleteMapping("/registries/{id}/constants/{cid}")
    public ResponseEntity<?> constantsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("constant", id, cid); }

    // variables
    @GetMapping("/registries/{id}/variables")
    public ResponseEntity<?> variablesList(@PathVariable String id) { return childList("variable", id); }

    @PostMapping("/registries/{id}/variables")
    public ResponseEntity<?> variablesCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("variable", id, body); }

    @GetMapping("/registries/{id}/variables/{cid}")
    public ResponseEntity<?> variablesGet(@PathVariable String id, @PathVariable String cid) { return childGet("variable", id, cid); }

    @PatchMapping("/registries/{id}/variables/{cid}")
    public ResponseEntity<?> variablesUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("variable", id, cid, body); }

    @DeleteMapping("/registries/{id}/variables/{cid}")
    public ResponseEntity<?> variablesDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("variable", id, cid); }

    // states
    @GetMapping("/registries/{id}/states")
    public ResponseEntity<?> statesList(@PathVariable String id) { return childList("state", id); }

    @PostMapping("/registries/{id}/states")
    public ResponseEntity<?> statesCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("state", id, body); }

    @GetMapping("/registries/{id}/states/{cid}")
    public ResponseEntity<?> statesGet(@PathVariable String id, @PathVariable String cid) { return childGet("state", id, cid); }

    @PatchMapping("/registries/{id}/states/{cid}")
    public ResponseEntity<?> statesUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("state", id, cid, body); }

    @DeleteMapping("/registries/{id}/states/{cid}")
    public ResponseEntity<?> statesDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("state", id, cid); }

    // transitions
    @GetMapping("/registries/{id}/transitions")
    public ResponseEntity<?> transitionsList(@PathVariable String id) { return childList("transition", id); }

    @PostMapping("/registries/{id}/transitions")
    public ResponseEntity<?> transitionsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("transition", id, body); }

    @GetMapping("/registries/{id}/transitions/{cid}")
    public ResponseEntity<?> transitionsGet(@PathVariable String id, @PathVariable String cid) { return childGet("transition", id, cid); }

    @PatchMapping("/registries/{id}/transitions/{cid}")
    public ResponseEntity<?> transitionsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("transition", id, cid, body); }

    @DeleteMapping("/registries/{id}/transitions/{cid}")
    public ResponseEntity<?> transitionsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("transition", id, cid); }

    // invariants
    @GetMapping("/registries/{id}/invariants")
    public ResponseEntity<?> invariantsList(@PathVariable String id) { return childList("invariant", id); }

    @PostMapping("/registries/{id}/invariants")
    public ResponseEntity<?> invariantsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("invariant", id, body); }

    @GetMapping("/registries/{id}/invariants/{cid}")
    public ResponseEntity<?> invariantsGet(@PathVariable String id, @PathVariable String cid) { return childGet("invariant", id, cid); }

    @PatchMapping("/registries/{id}/invariants/{cid}")
    public ResponseEntity<?> invariantsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("invariant", id, cid, body); }

    @DeleteMapping("/registries/{id}/invariants/{cid}")
    public ResponseEntity<?> invariantsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("invariant", id, cid); }

    // properties
    @GetMapping("/registries/{id}/properties")
    public ResponseEntity<?> propertiesList(@PathVariable String id) { return childList("property", id); }

    @PostMapping("/registries/{id}/properties")
    public ResponseEntity<?> propertiesCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("property", id, body); }

    @GetMapping("/registries/{id}/properties/{cid}")
    public ResponseEntity<?> propertiesGet(@PathVariable String id, @PathVariable String cid) { return childGet("property", id, cid); }

    @PatchMapping("/registries/{id}/properties/{cid}")
    public ResponseEntity<?> propertiesUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("property", id, cid, body); }

    @DeleteMapping("/registries/{id}/properties/{cid}")
    public ResponseEntity<?> propertiesDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("property", id, cid); }

    // temporal-properties
    @GetMapping("/registries/{id}/temporal-properties")
    public ResponseEntity<?> temporalPropsList(@PathVariable String id) { return childList("temporal_property", id); }

    @PostMapping("/registries/{id}/temporal-properties")
    public ResponseEntity<?> temporalPropsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("temporal_property", id, body); }

    @GetMapping("/registries/{id}/temporal-properties/{cid}")
    public ResponseEntity<?> temporalPropsGet(@PathVariable String id, @PathVariable String cid) { return childGet("temporal_property", id, cid); }

    @PatchMapping("/registries/{id}/temporal-properties/{cid}")
    public ResponseEntity<?> temporalPropsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("temporal_property", id, cid, body); }

    @DeleteMapping("/registries/{id}/temporal-properties/{cid}")
    public ResponseEntity<?> temporalPropsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("temporal_property", id, cid); }

    // concept-mappings
    @GetMapping("/registries/{id}/concept-mappings")
    public ResponseEntity<?> conceptMappingsList(@PathVariable String id) { return childList("concept_mapping", id); }

    @PostMapping("/registries/{id}/concept-mappings")
    public ResponseEntity<?> conceptMappingsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("concept_mapping", id, body); }

    @GetMapping("/registries/{id}/concept-mappings/{cid}")
    public ResponseEntity<?> conceptMappingsGet(@PathVariable String id, @PathVariable String cid) { return childGet("concept_mapping", id, cid); }

    @PatchMapping("/registries/{id}/concept-mappings/{cid}")
    public ResponseEntity<?> conceptMappingsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("concept_mapping", id, cid, body); }

    @DeleteMapping("/registries/{id}/concept-mappings/{cid}")
    public ResponseEntity<?> conceptMappingsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("concept_mapping", id, cid); }

    // attribute-mappings
    @GetMapping("/registries/{id}/attribute-mappings")
    public ResponseEntity<?> attributeMappingsList(@PathVariable String id) { return childList("attribute_mapping", id); }

    @PostMapping("/registries/{id}/attribute-mappings")
    public ResponseEntity<?> attributeMappingsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("attribute_mapping", id, body); }

    @GetMapping("/registries/{id}/attribute-mappings/{cid}")
    public ResponseEntity<?> attributeMappingsGet(@PathVariable String id, @PathVariable String cid) { return childGet("attribute_mapping", id, cid); }

    @PatchMapping("/registries/{id}/attribute-mappings/{cid}")
    public ResponseEntity<?> attributeMappingsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("attribute_mapping", id, cid, body); }

    @DeleteMapping("/registries/{id}/attribute-mappings/{cid}")
    public ResponseEntity<?> attributeMappingsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("attribute_mapping", id, cid); }

    // relationship-mappings
    @GetMapping("/registries/{id}/relationship-mappings")
    public ResponseEntity<?> relationshipMappingsList(@PathVariable String id) { return childList("relationship_mapping", id); }

    @PostMapping("/registries/{id}/relationship-mappings")
    public ResponseEntity<?> relationshipMappingsCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("relationship_mapping", id, body); }

    @GetMapping("/registries/{id}/relationship-mappings/{cid}")
    public ResponseEntity<?> relationshipMappingsGet(@PathVariable String id, @PathVariable String cid) { return childGet("relationship_mapping", id, cid); }

    @PatchMapping("/registries/{id}/relationship-mappings/{cid}")
    public ResponseEntity<?> relationshipMappingsUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("relationship_mapping", id, cid, body); }

    @DeleteMapping("/registries/{id}/relationship-mappings/{cid}")
    public ResponseEntity<?> relationshipMappingsDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("relationship_mapping", id, cid); }

    // execution-log
    @GetMapping("/registries/{id}/execution-log")
    public ResponseEntity<?> executionLogList(@PathVariable String id) { return childList("execution_log", id); }

    @PostMapping("/registries/{id}/execution-log")
    public ResponseEntity<?> executionLogCreate(@PathVariable String id, @RequestBody(required = false) Map<String, Object> body) { return childCreate("execution_log", id, body); }

    @GetMapping("/registries/{id}/execution-log/{cid}")
    public ResponseEntity<?> executionLogGet(@PathVariable String id, @PathVariable String cid) { return childGet("execution_log", id, cid); }

    @PatchMapping("/registries/{id}/execution-log/{cid}")
    public ResponseEntity<?> executionLogUpdate(@PathVariable String id, @PathVariable String cid, @RequestBody(required = false) Map<String, Object> body) { return childUpdate("execution_log", id, cid, body); }

    @DeleteMapping("/registries/{id}/execution-log/{cid}")
    public ResponseEntity<?> executionLogDelete(@PathVariable String id, @PathVariable String cid) { return childDelete("execution_log", id, cid); }
}
