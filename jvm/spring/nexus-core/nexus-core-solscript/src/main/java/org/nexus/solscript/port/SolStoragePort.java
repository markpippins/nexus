package org.nexus.solscript.port;

import org.nexus.solscript.models.SolModels;

import java.util.ArrayList;
import java.util.List;

/**
 * Storage port SOLScript requires — faithful port of
 * typescript/solscript/src/port.ts. The interpreter consumes ONLY these
 * shapes; adapters convert their source rows into them, and source
 * schema/table names never leak past an adapter.
 */
public interface SolStoragePort {

    List<SolModels.Concept> listConcepts();
    List<SolModels.ConceptAttribute> listAttributes();
    List<SolModels.ConceptRelationship> listRelationships();
    List<SolModels.Entity> listSubjects(String conceptId);
    List<SolModels.Entity> listShrapnelFacts();
    List<SolModels.Proposition> listRevisions(String subjectId);
    List<SolModels.Proposition> listEvidence();
}