/**
 * SOLScript TypeScript core — storage port (cutover 05 contract).
 *
 * Ported from python/SOLScript/solscript/adapters/contract.py. The
 * interpreter consumes ONLY these shapes; adapters convert their source
 * rows into them, and source schema/table names never leak past an
 * adapter. DatabaseLoader (asyncpg) is replaced by this port in TS.
 */
/** In-memory adapter (fixtures, tests, embedded use). */
export class InMemorySolStorage {
    concepts = [];
    attributes = [];
    relationships = [];
    subjects = [];
    shrapnelFacts = [];
    revisions = [];
    evidence = [];
    listConcepts() {
        return Promise.resolve([...this.concepts]);
    }
    listAttributes() {
        return Promise.resolve([...this.attributes]);
    }
    listRelationships() {
        return Promise.resolve([...this.relationships]);
    }
    listSubjects(conceptId) {
        return Promise.resolve(this.subjects.filter((s) => s.conceptId === conceptId));
    }
    listShrapnelFacts() {
        return Promise.resolve([...this.shrapnelFacts]);
    }
    listRevisions(subjectId) {
        return Promise.resolve(this.revisions.filter((r) => r.subjectId === subjectId));
    }
    listEvidence() {
        return Promise.resolve([...this.evidence]);
    }
}
//# sourceMappingURL=port.js.map