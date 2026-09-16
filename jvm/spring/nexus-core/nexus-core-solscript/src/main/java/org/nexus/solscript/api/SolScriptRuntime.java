package org.nexus.solscript.api;

import org.nexus.solscript.interpreter.ResolutionInterpreter;
import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.port.InMemorySolStorage;
import org.nexus.solscript.port.SolStoragePort;
import org.springframework.stereotype.Service;

import jakarta.annotation.PostConstruct;

/**
 * Holds the SOLScript interpreter instance served by the read-only REST
 * facade. Loaded from a {@link SolStoragePort} at startup (default in-memory,
 * overridable with a datasource-backed port later). The REST tier exposes only
 * the read surface; state transitions are disallowed there (405) even though
 * the interpreter library fully supports them.
 */
@Service
public class SolScriptRuntime {

    private final SolStoragePort storage;
    private ResolutionInterpreter interpreter;

    public SolScriptRuntime(SolStoragePort storage) {
        this.storage = storage;
    }

    @PostConstruct
    void init() {
        this.interpreter = new ResolutionInterpreter();
        loadFromStorage();
    }

    private void loadFromStorage() {
        for (SolModels.Concept c : storage.listConcepts()) interpreter.addConcept(c);
        for (SolModels.ConceptRelationship r : storage.listRelationships()) interpreter.relationships.put(r.id, r);
        for (SolModels.ConceptAttribute a : storage.listAttributes()) { /* attributes live on concepts */ }
        for (SolModels.Concept c : interpreter.concepts.values()) {
            for (SolModels.ConceptStateTransition t : c.stateTransitions) interpreter.stateTransitions.put(t.id, t);
            for (SolModels.Rule r : c.rules) interpreter.rules.put(r.id, r);
            for (SolModels.Rule r : c.invariants) interpreter.rules.put(r.id, r);
        }
        for (SolModels.Concept c : storage.listConcepts()) {
            for (SolModels.Entity s : storage.listSubjects(c.id)) interpreter.addEntity(s);
        }
    }

    public ResolutionInterpreter interpreter() {
        return interpreter;
    }
}