/**
 * aegis-srv — TypeSpec Operations
 *
 * REST surface for the `aegis` TLA+ state-machine registry schema.
 * All routes are mounted under `/api` (the Express router mounts at /api).
 *
 * Conventions (typespec/v1/typescript-conventions.md):
 *   - flat full-path @route; one op per route
 *   - path params {id} map from Express :id
 *   - LAN-bound, no authentication (repo convention)
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.aegissrv {

  // ── Health (served by index.ts at /health, not under /api) ────
  @route("/health") @get op health(): HealthResponse;

  // ── Registries ────────────────────────────────────────────────
  @route("/api/registries")              @get  op listRegistries(): { items: Registry[] };
  @route("/api/registries/name/{name}")  @get  op getRegistryByName(@path name: string): Registry | ErrorDetail;
  @route("/api/registries")              @post op createRegistry(@body body: RegistryCreate): Registry | ErrorDetail;
  @route("/api/registries/{id}")         @get  op getRegistry(@path id: string): Registry | ErrorDetail;
  @route("/api/registries/{id}")         @patch op updateRegistry(@path id: string, @body body: RegistryUpdate): Registry | ErrorDetail;
  @route("/api/registries/{id}")         @delete op deleteRegistry(@path id: string): { deleted: string } | ErrorDetail;

  // ── Registry: immutable Phase A revisions ─────────────────────
  @route("/api/registries/{id}/revisions")       @get  op listRegistryRevisions(@path id: string): { items: RegistryRevision[] };
  @route("/api/registries/{id}/revisions")       @post op createRegistryRevision(@path id: string, @body body: RegistryRevisionCreate): RegistryRevision | ErrorDetail;
  @route("/api/registries/{id}/revisions/{rid}") @get  op getRegistryRevision(@path id: string, @path rid: string): RegistryRevision | ErrorDetail;

  // ── Registry: validate / model-check actions ─────────────────
  @route("/api/registries/{id}/validate")      @post op validateRegistry(@path id: string): ValidationResult | ErrorDetail;
  @route("/api/registries/{id}/model-check")   @post op modelCheckRegistry(@path id: string): ModelCheckResult | ErrorDetail;

  // ── Registry: immutable revisions and Wind compilation ───────
  @route("/api/registries/{id}/wind-compilations") @get op listWindCompilations(@path id: string): { items: WindCompilation[] };
  @route("/api/registries/{id}/wind-compilations") @post op compileRegistryToWind(@path id: string, @body body: WindCompilationCreate): WindCompilationResponse | ErrorDetail;
  @route("/api/registries/{id}/wind-compilations/{cid}") @get op getWindCompilation(@path id: string, @path cid: string): WindCompilation | ErrorDetail;

  // ── Registry: child collections ──────────────────────────────
  @route("/api/registries/{id}/validation-results")   @get op listValidationResults(@path id: string): { items: ValidationResult[] };
  @route("/api/registries/{id}/model-check-results")  @get op listModelCheckResults(@path id: string): { items: ModelCheckResult[] };
  @route("/api/registries/{id}/execution-log")        @get  op listExecutionLog(@path id: string): { items: ExecutionLog[] };
  @route("/api/registries/{id}/execution-log")        @post op createExecutionLog(@path id: string, @body body: ExecutionLogCreate): ExecutionLog | ErrorDetail;
  @route("/api/registries/{id}/execution-log/{cid}")  @get  op getExecutionLog(@path id: string, @path cid: string): ExecutionLog | ErrorDetail;
  @route("/api/registries/{id}/execution-log/{cid}")  @patch op updateExecutionLog(@path id: string, @path cid: string, @body body: ExecutionLogCreate): ExecutionLog | ErrorDetail;
  @route("/api/registries/{id}/execution-log/{cid}")  @delete op deleteExecutionLog(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: constants ────────────────────────────────
  @route("/api/registries/{id}/constants")        @get  op listConstants(@path id: string): { items: Constant[] };
  @route("/api/registries/{id}/constants")        @post op createConstant(@path id: string, @body body: ConstantCreate): Constant | ErrorDetail;
  @route("/api/registries/{id}/constants/{cid}")  @get  op getConstant(@path id: string, @path cid: string): Constant | ErrorDetail;
  @route("/api/registries/{id}/constants/{cid}")  @patch op updateConstant(@path id: string, @path cid: string, @body body: ConstantUpdate): Constant | ErrorDetail;
  @route("/api/registries/{id}/constants/{cid}")  @delete op deleteConstant(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: variables ────────────────────────────────
  @route("/api/registries/{id}/variables")        @get  op listVariables(@path id: string): { items: Variable[] };
  @route("/api/registries/{id}/variables")        @post op createVariable(@path id: string, @body body: VariableCreate): Variable | ErrorDetail;
  @route("/api/registries/{id}/variables/{cid}")  @get  op getVariable(@path id: string, @path cid: string): Variable | ErrorDetail;
  @route("/api/registries/{id}/variables/{cid}")  @patch op updateVariable(@path id: string, @path cid: string, @body body: VariableUpdate): Variable | ErrorDetail;
  @route("/api/registries/{id}/variables/{cid}")  @delete op deleteVariable(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: states ───────────────────────────────────
  @route("/api/registries/{id}/states")        @get  op listStates(@path id: string): { items: State[] };
  @route("/api/registries/{id}/states")        @post op createState(@path id: string, @body body: StateCreate): State | ErrorDetail;
  @route("/api/registries/{id}/states/{cid}")  @get  op getState(@path id: string, @path cid: string): State | ErrorDetail;
  @route("/api/registries/{id}/states/{cid}")  @patch op updateState(@path id: string, @path cid: string, @body body: StateUpdate): State | ErrorDetail;
  @route("/api/registries/{id}/states/{cid}")  @delete op deleteState(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: transitions ──────────────────────────────
  @route("/api/registries/{id}/transitions")        @get  op listTransitions(@path id: string): { items: Transition[] };
  @route("/api/registries/{id}/transitions")        @post op createTransition(@path id: string, @body body: TransitionCreate): Transition | ErrorDetail;
  @route("/api/registries/{id}/transitions/{cid}")  @get  op getTransition(@path id: string, @path cid: string): Transition | ErrorDetail;
  @route("/api/registries/{id}/transitions/{cid}")  @patch op updateTransition(@path id: string, @path cid: string, @body body: TransitionUpdate): Transition | ErrorDetail;
  @route("/api/registries/{id}/transitions/{cid}")  @delete op deleteTransition(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: invariants ───────────────────────────────
  @route("/api/registries/{id}/invariants")        @get  op listInvariants(@path id: string): { items: Invariant[] };
  @route("/api/registries/{id}/invariants")        @post op createInvariant(@path id: string, @body body: InvariantCreate): Invariant | ErrorDetail;
  @route("/api/registries/{id}/invariants/{cid}")  @get  op getInvariant(@path id: string, @path cid: string): Invariant | ErrorDetail;
  @route("/api/registries/{id}/invariants/{cid}")  @patch op updateInvariant(@path id: string, @path cid: string, @body body: InvariantUpdate): Invariant | ErrorDetail;
  @route("/api/registries/{id}/invariants/{cid}")  @delete op deleteInvariant(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: properties ───────────────────────────────
  @route("/api/registries/{id}/properties")        @get  op listProperties(@path id: string): { items: Property[] };
  @route("/api/registries/{id}/properties")        @post op createProperty(@path id: string, @body body: PropertyCreate): Property | ErrorDetail;
  @route("/api/registries/{id}/properties/{cid}")  @get  op getProperty(@path id: string, @path cid: string): Property | ErrorDetail;
  @route("/api/registries/{id}/properties/{cid}")  @patch op updateProperty(@path id: string, @path cid: string, @body body: PropertyUpdate): Property | ErrorDetail;
  @route("/api/registries/{id}/properties/{cid}")  @delete op deleteProperty(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: temporal properties ──────────────────────
  @route("/api/registries/{id}/temporal-properties")        @get  op listTemporalProperties(@path id: string): { items: TemporalProperty[] };
  @route("/api/registries/{id}/temporal-properties")        @post op createTemporalProperty(@path id: string, @body body: TemporalPropertyCreate): TemporalProperty | ErrorDetail;
  @route("/api/registries/{id}/temporal-properties/{cid}")  @get  op getTemporalProperty(@path id: string, @path cid: string): TemporalProperty | ErrorDetail;
  @route("/api/registries/{id}/temporal-properties/{cid}")  @patch op updateTemporalProperty(@path id: string, @path cid: string, @body body: TemporalPropertyUpdate): TemporalProperty | ErrorDetail;
  @route("/api/registries/{id}/temporal-properties/{cid}")  @delete op deleteTemporalProperty(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: concept mappings ─────────────────────────
  @route("/api/registries/{id}/concept-mappings")        @get  op listConceptMappings(@path id: string): { items: ConceptMapping[] };
  @route("/api/registries/{id}/concept-mappings")        @post op createConceptMapping(@path id: string, @body body: ConceptMappingCreate): ConceptMapping | ErrorDetail;
  @route("/api/registries/{id}/concept-mappings/{cid}")  @get  op getConceptMapping(@path id: string, @path cid: string): ConceptMapping | ErrorDetail;
  @route("/api/registries/{id}/concept-mappings/{cid}")  @patch op updateConceptMapping(@path id: string, @path cid: string, @body body: ConceptMappingUpdate): ConceptMapping | ErrorDetail;
  @route("/api/registries/{id}/concept-mappings/{cid}")  @delete op deleteConceptMapping(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: attribute mappings ───────────────────────
  @route("/api/registries/{id}/attribute-mappings")        @get  op listAttributeMappings(@path id: string): { items: AttributeMapping[] };
  @route("/api/registries/{id}/attribute-mappings")        @post op createAttributeMapping(@path id: string, @body body: AttributeMappingCreate): AttributeMapping | ErrorDetail;
  @route("/api/registries/{id}/attribute-mappings/{cid}")  @get  op getAttributeMapping(@path id: string, @path cid: string): AttributeMapping | ErrorDetail;
  @route("/api/registries/{id}/attribute-mappings/{cid}")  @patch op updateAttributeMapping(@path id: string, @path cid: string, @body body: AttributeMappingUpdate): AttributeMapping | ErrorDetail;
  @route("/api/registries/{id}/attribute-mappings/{cid}")  @delete op deleteAttributeMapping(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;

  // ── Registry child: relationship mappings ────────────────────
  @route("/api/registries/{id}/relationship-mappings")        @get  op listRelationshipMappings(@path id: string): { items: RelationshipMapping[] };
  @route("/api/registries/{id}/relationship-mappings")        @post op createRelationshipMapping(@path id: string, @body body: RelationshipMappingCreate): RelationshipMapping | ErrorDetail;
  @route("/api/registries/{id}/relationship-mappings/{cid}")  @get  op getRelationshipMapping(@path id: string, @path cid: string): RelationshipMapping | ErrorDetail;
  @route("/api/registries/{id}/relationship-mappings/{cid}")  @patch op updateRelationshipMapping(@path id: string, @path cid: string, @body body: RelationshipMappingUpdate): RelationshipMapping | ErrorDetail;
  @route("/api/registries/{id}/relationship-mappings/{cid}")  @delete op deleteRelationshipMapping(@path id: string, @path cid: string): { deleted: string } | ErrorDetail;
}