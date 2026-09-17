/**
 * wind-srv — TypeSpec Operations
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.windsrv {

  @route("/health") @get op health(): HealthResponse;

  // ── Tickets ───────────────────────────────────────────────────
  @route("/tickets")     @get  op listTickets(): { items: Ticket[] };
  @route("/tickets/{id}") @get  op getTicket(@path id: string): Ticket | ErrorDetail;

  // ── Instances ─────────────────────────────────────────────────
  @route("/instances")     @get op listInstances(): { items: WorkflowInstance[] };
  @route("/instances/{id}") @get op getInstance(@path id: string): WorkflowInstance | ErrorDetail;

  // ── Workflows ─────────────────────────────────────────────────
  @route("/workflows")     @get op listWorkflows(): { items: Workflow[] };
  @route("/workflows/{id}") @get op getWorkflow(@path id: string): Workflow | ErrorDetail;

  // ── Nodes ─────────────────────────────────────────────────────
  @route("/nodes")     @get op listNodes(): { items: WorkflowNode[] };
  @route("/nodes/{id}") @get op getNode(@path id: string): WorkflowNode | ErrorDetail;

  // ── Node requirements + capability resolution (staged inert) ──────
  //
  // STAGED INERT: contract-first. The resolver consults the LIVE V172/V174
  // capability/adapter/satisfaction surfaces via their own APIs; until the
  // operator enforce flip, resolution runs warn-mode (verdicts recorded,
  // entry never blocked). No wind-srv runtime change in this PR.
  @route("/nodes/{id}/requirements")
  @get op listNodeRequirements(@path id: string): { items: NodeRequirement[] };

  @route("/nodes/{id}/requirements")
  @post op setNodeRequirements(@path id: string, @body body: NodeRequirement[]): { items: NodeRequirement[] };

  /** Resolve every requirement on the node: capability satisfaction via
   *  V172/V174 adapters + probes, role credential via the open
   *  roles_history snapshot, context bundle as resolved references. */
  @route("/nodes/{id}/resolve")
  @post op resolveNode(@path id: string): ResolvedContextBundle | ErrorDetail;

  /** Lease-bound ticket assignment (liveness via the lease machinery). */
  @route("/tickets/{id}/binding")
  @put op bindTicket(@path id: string, @body body: LeaseBinding): Ticket | ErrorDetail;

  // ── Event Types ───────────────────────────────────────────────
  @route("/event-types")           @get  op listEventTypes(): { items: EventType[] };
  @route("/event-types")           @post op createEventType(@body body: EventType): EventType;
  @route("/event-types/{eventType}") @get  op getEventType(@path eventType: string): EventType | ErrorDetail;
  @route("/event-types/{eventType}") @delete op deleteEventType(@path eventType: string): { deleted: string };

  // ── Events ────────────────────────────────────────────────────
  @route("/events")       @get  op listEvents(): { items: unknown[] };
  @route("/events")       @post op createEvent(@body body: unknown): unknown;
  @route("/events/{id}")  @get  op getEvent(@path id: string): unknown;
  @route("/events/poll")  @post op pollEvents(@body body: PollRequest): { items: unknown[] };

  // ── Edges ─────────────────────────────────────────────────────
  @route("/edges")      @get  op listEdges(): { items: Edge[] };
  @route("/edges")      @post op createEdge(@body body: Edge): Edge;
  @route("/edges/{id}") @delete op deleteEdge(@path id: string): { deleted: string };

  // ── Offices / Titles / Roles ──────────────────────────────────
  @route("/offices")   @get op listOffices(): { items: Office[] };
  @route("/titles")    @get op listTitles(): { items: Title[] };
  @route("/versions")  @get op listVersions(): { items: Version[] };
  @route("/v-roles")   @get op listVRoles(): { items: RoleDef[] };

  // ── Receipts ──────────────────────────────────────────────────
  @route("/receipts") @get op listReceipts(): { items: unknown[] };
}