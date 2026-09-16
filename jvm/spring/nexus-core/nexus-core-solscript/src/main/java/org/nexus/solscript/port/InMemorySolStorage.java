package org.nexus.solscript.port;

import org.nexus.solscript.models.SolModels;

import java.util.ArrayList;
import java.util.List;

/**
 * In-memory adapter (fixtures, tests, embedded use) — faithful port of the
 * TS InMemorySolStorage.
 */
public class InMemorySolStorage implements SolStoragePort {

    public final List<SolModels.Concept> concepts = new ArrayList<>();
    public final List<SolModels.ConceptAttribute> attributes = new ArrayList<>();
    public final List<SolModels.ConceptRelationship> relationships = new ArrayList<>();
    public final List<SolModels.Entity> subjects = new ArrayList<>();
    public final List<SolModels.Entity> shrapnelFacts = new ArrayList<>();
    public final List<SolModels.Proposition> revisions = new ArrayList<>();
    public final List<SolModels.Proposition> evidence = new ArrayList<>();

    public List<SolModels.Concept> listConcepts() { return new ArrayList<>(concepts); }
    public List<SolModels.ConceptAttribute> listAttributes() { return new ArrayList<>(attributes); }
    public List<SolModels.ConceptRelationship> listRelationships() { return new ArrayList<>(relationships); }
    public List<SolModels.Entity> listSubjects(String conceptId) {
        return subjects.stream().filter(s -> s.conceptId.equals(conceptId)).toList();
    }
    public List<SolModels.Entity> listShrapnelFacts() { return new ArrayList<>(shrapnelFacts); }
    public List<SolModels.Proposition> listRevisions(String subjectId) {
        return revisions.stream().filter(r -> r.subjectEntityId.equals(subjectId)).toList();
    }
    public List<SolModels.Proposition> listEvidence() { return new ArrayList<>(evidence); }
}