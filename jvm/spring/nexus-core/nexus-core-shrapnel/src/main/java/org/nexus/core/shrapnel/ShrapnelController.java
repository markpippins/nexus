package org.nexus.core.shrapnel;

import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Full REST surface of the shrapnel port — mirrors the TS service's routers
 * (routes/index.js): /api/shrapnel/health, /field-types, /fields, /objects,
 * /encode, /stereotypes, with the same JSON envelopes and error mapping.
 *
 * <p>The TS service mounts everything under {@code /api/*}; here the routes
 * live under {@code /api/shrapnel/*} so they coexist inside the nexus-core
 * monolith without colliding with the aegis surface.</p>
 */
@RestController
@RequestMapping("/api/shrapnel")
public class ShrapnelController {

    private final ShrapnelStore store;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;
    private final org.springframework.transaction.support.TransactionTemplate tx;

    public ShrapnelController(ShrapnelStore store,
                              org.springframework.jdbc.core.JdbcTemplate jdbc,
                              org.springframework.transaction.support.TransactionTemplate tx) {
        this.store = store;
        this.jdbc = jdbc;
        this.tx = tx;
    }

    // ── health (routes/health.js) ───────────────────────────────────────────

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", "healthy");
        out.put("counts", store.healthCounts());
        return out;
    }

    // ── field types (routes/field-types.js) ─────────────────────────────────

    @GetMapping("/field-types")
    public Map<String, Object> listFieldTypes() {
        return Map.of("field_types", store.listFieldTypes());
    }

    @GetMapping("/field-types/{code}")
    public ResponseEntity<Object> getFieldType(@PathVariable String code) {
        Integer c = parseInt(code);
        if (c == null) {
            return ResponseEntity.badRequest().body(error("code must be an integer 1..7"));
        }
        Map<String, Object> ft = store.getFieldType(c);
        if (ft == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        return ResponseEntity.ok(Map.of("field_type", ft));
    }

    // ── fields (routes/fields.js) ───────────────────────────────────────────

    @GetMapping("/fields")
    public Map<String, Object> listFields(@RequestParam(required = false) Integer limit,
                                          @RequestParam(required = false) Integer offset,
                                          @RequestParam(name = "type_code", required = false) Integer typeCode) {
        int lim = clampLimit(limit);
        int off = Math.max(0, offset == null ? 0 : offset);
        return Map.of("fields", store.listFields(typeCode, lim, off));
    }

    @GetMapping("/fields/{id}")
    public ResponseEntity<Object> getField(@PathVariable String id) {
        Long n = parseLong(id);
        if (n == null) {
            return ResponseEntity.badRequest().body(error("id must be integer"));
        }
        Map<String, Object> f = store.getField(n);
        if (f == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        return ResponseEntity.ok(Map.of("field", f));
    }

    @PostMapping("/fields")
    public ResponseEntity<Object> createField(@RequestBody Map<String, Object> body) {
        Map<String, Object> spec = ShrapnelCodec.normaliseFieldSpec(body);
        Map<String, Object> field = store.upsertField(spec);
        return ResponseEntity.status(HttpStatus.CREATED).body(Map.of("field", field));
    }

    // ── objects (routes/objects.js) ─────────────────────────────────────────

    @GetMapping("/objects")
    public ResponseEntity<Object> listObjects(@RequestParam(required = false) Integer limit,
                                              @RequestParam(required = false) Integer offset,
                                              @RequestParam(name = "decode", defaultValue = "false") String decode) {
        int lim = clampLimit(limit);
        int off = Math.max(0, offset == null ? 0 : offset);
        List<Map<String, Object>> rows = store.listObjects(lim, off);
        if (!"true".equalsIgnoreCase(decode)) {
            return ResponseEntity.ok(Map.of("objects", rows));
        }
        List<Map<String, Object>> objects = rows.stream().map(row -> {
            long id = ((Number) row.get("id")).longValue();
            Map<String, Object> o = new LinkedHashMap<>();
            o.put("id", id);
            o.put("created_at", row.get("created_at"));
            o.put("values", ShrapnelCodec.decodeObject(jdbc, id));
            return o;
        }).toList();
        return ResponseEntity.ok(Map.of("objects", objects));
    }

    @GetMapping("/objects/{id}")
    public ResponseEntity<Object> getObject(@PathVariable String id) {
        Long n = parseLong(id);
        if (n == null) {
            return ResponseEntity.badRequest().body(error("id must be integer"));
        }
        Map<String, Object> row = store.getObjectRow(n);
        if (row == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        Map<String, Object> object = new LinkedHashMap<>();
        object.put("id", row.get("id"));
        object.put("created_at", row.get("created_at"));
        object.put("values", ShrapnelCodec.decodeObject(jdbc, n));
        return ResponseEntity.ok(Map.of("object", object));
    }

    @PostMapping("/objects")
    public ResponseEntity<Object> createObject(@RequestBody Map<String, Object> body) {
        ShrapnelCodec.EncodeResult result = ShrapnelCodec.encodePayload(jdbc, tx, body);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("object_id", result.objectId());
        out.put("fields", result.fields());
        return ResponseEntity.status(HttpStatus.CREATED).body(out);
    }

    @DeleteMapping("/objects/{id}")
    public ResponseEntity<Object> deleteObject(@PathVariable String id) {
        Long n = parseLong(id);
        if (n == null) {
            return ResponseEntity.badRequest().body(error("id must be integer"));
        }
        Long deleted = store.deleteObject(n);
        if (deleted == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        return ResponseEntity.ok(Map.of("deleted", deleted));
    }

    @GetMapping("/objects/{id}/conformance")
    public ResponseEntity<Object> objectConformance(@PathVariable String id) {
        Long n = parseLong(id);
        if (n == null) {
            throw ShrapnelApiException.badRequest("id must be integer");
        }
        if (store.getObjectRow(n) == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        return ResponseEntity.ok(store.objectConformance(n).get("conformance"));
    }

    @PostMapping("/objects/{id}/classify")
    public ResponseEntity<Object> classify(@PathVariable String id,
                                           @RequestBody(required = false) Map<String, Object> body) {
        Long n = parseLong(id);
        if (n == null) {
            throw ShrapnelApiException.badRequest("id must be integer");
        }
        Object revRaw = body == null ? null : body.get("revision_id");
        Long revisionId = null;
        if (revRaw instanceof Number num) {
            revisionId = num.longValue();
        } else if (revRaw instanceof String s) {
            try {
                revisionId = Long.parseLong(s.trim());
            } catch (NumberFormatException e) {
                // fall through to badRequest below
            }
        }
        if (revisionId == null) {
            throw ShrapnelApiException.badRequest("revision_id must be an integer");
        }
        String disposition = body.get("disposition") instanceof String s ? s : null;
        Map<String, Object> result = store.classify(n, revisionId, disposition);
        return ResponseEntity.ok(result);
    }

    @GetMapping("/objects/{id}/values")
    public ResponseEntity<Object> objectValues(@PathVariable String id) {
        Long n = parseLong(id);
        if (n == null) {
            return ResponseEntity.badRequest().body(error("id must be integer"));
        }
        List<Map<String, Object>> values = store.objectValues(n);
        if (values.isEmpty() && store.getObjectRow(n) == null) {
            return ResponseEntity.status(404).body(error("not_found"));
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("object_id", n);
        out.put("values", values);
        return ResponseEntity.ok(out);
    }

    // ── encode (routes/encode.js) ───────────────────────────────────────────

    @PostMapping("/encode")
    public ResponseEntity<Object> encode(@RequestBody Map<String, Object> body) {
        ShrapnelCodec.EncodeResult result = ShrapnelCodec.encodePayload(jdbc, tx, body);
        Map<String, Object> decoded = ShrapnelCodec.decodeObject(jdbc, result.objectId());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("object_id", result.objectId());
        out.put("fields", result.fields());
        out.put("decoded", decoded);
        return ResponseEntity.status(HttpStatus.CREATED).body(out);
    }

    // ── stereotypes (routes/stereotypes.js) ─────────────────────────────────

    @GetMapping("/stereotypes")
    public Map<String, Object> listStereotypes() {
        return Map.of("stereotypes", store.listStereotypes());
    }

    @GetMapping("/stereotypes/{name}")
    public ResponseEntity<Object> getStereotype(@PathVariable String name) {
        try {
            Map<String, Object> head = store.resolveHeadRevision(name);
            return ResponseEntity.ok(Map.of("revision", store.getRevision(
                    ((Number) head.get("head_revision_id")).longValue())));
        } catch (DataAccessException e) {
            return mapPgOrRethrow(e);
        }
    }

    @GetMapping("/stereotypes/{name}/chain")
    public ResponseEntity<Object> getStereotypeChain(@PathVariable String name) {
        try {
            Map<String, Object> head = store.resolveHeadRevision(name);
            long headId = ((Number) head.get("head_revision_id")).longValue();
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("stereotype", name);
            out.put("head_revision_id", headId);
            out.put("chain", store.stereotypeChain(headId));
            return ResponseEntity.ok(out);
        } catch (DataAccessException e) {
            return mapPgOrRethrow(e);
        }
    }

    @GetMapping("/stereotypes/{name}/contract")
    public ResponseEntity<Object> getStereotypeContract(@PathVariable String name) {
        try {
            Map<String, Object> head = store.resolveHeadRevision(name);
            long headId = ((Number) head.get("head_revision_id")).longValue();
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("stereotype", name);
            out.put("head_revision_id", headId);
            out.put("contract", store.stereotypeContract(headId));
            return ResponseEntity.ok(out);
        } catch (DataAccessException e) {
            return mapPgOrRethrow(e);
        }
    }

    @PostMapping("/stereotypes/revisions")
    public ResponseEntity<Object> createStereotypeRevision(@RequestBody(required = false) Map<String, Object> body) {
        return ResponseEntity.status(HttpStatus.CREATED).body(store.createStereotypeRevision(body));
    }

    // ── shared helpers ──────────────────────────────────────────────────────

    private static int clampLimit(Integer limit) {
        int lim = limit == null ? 100 : limit;
        return Math.min(500, Math.max(1, lim));
    }

    private static Integer parseInt(String s) {
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static Long parseLong(String s) {
        try {
            return Long.parseLong(s);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static Map<String, Object> error(String message) {
        Map<String, Object> e = new HashMap<>();
        e.put("message", message);
        Map<String, Object> out = new HashMap<>();
        out.put("error", e);
        return out;
    }

    /** PG 23514/P0001 → 409, matching the TS mapPgError; otherwise rethrow. */
    private ResponseEntity<Object> mapPgOrRethrow(DataAccessException e) {
        ShrapnelApiException mapped = ShrapnelCodec.mapPg(e);
        if (mapped != null) {
            return ResponseEntity.status(mapped.getStatus()).body(errorBody(mapped));
        }
        throw e;
    }

    private static Map<String, Object> errorBody(ShrapnelApiException e) {
        Map<String, Object> err = new HashMap<>();
        err.put("message", e.getMessage());
        if (e.getDetails() != null) err.put("details", e.getDetails());
        Map<String, Object> out = new HashMap<>();
        out.put("error", err);
        return out;
    }

    // ── error mapping (error-handler.js port) ───────────────────────────────

    @RestControllerAdvice
    static class ShrapnelErrorAdvice {

        @ExceptionHandler(ShrapnelApiException.class)
        public ResponseEntity<Object> apiError(ShrapnelApiException e) {
            return ResponseEntity.status(e.getStatus()).body(errorBody(e));
        }

        /** PG unique violation (23505) / FK violation (23503) → 409. */
        @ExceptionHandler(DataAccessException.class)
        public ResponseEntity<Object> dataAccess(DataAccessException e) {
            String code = ShrapnelCodec.pgCode(e);
            if ("23505".equals(code)) {
                Map<String, Object> err = new HashMap<>();
                err.put("message", "duplicate");
                err.put("details", e.getMostSpecificCause().getMessage());
                Map<String, Object> out = new HashMap<>();
                out.put("error", err);
                return ResponseEntity.status(409).body(out);
            }
            if ("23503".equals(code)) {
                Map<String, Object> err = new HashMap<>();
                err.put("message", "foreign_key_violation");
                err.put("details", e.getMostSpecificCause().getMessage());
                Map<String, Object> out = new HashMap<>();
                out.put("error", err);
                return ResponseEntity.status(409).body(out);
            }
            throw e; // → 500 via the container
        }
    }
}
