"""CanonicalEnvelope — shared event envelope for all NATS-published events.

Design goals:
  - Transport-aware (carries ``subject``) but transport-neutral (works over
    NATS, JetStream, files, or replay).
  - Governance-ready: ``classification`` + ``policy_version`` let the governance
    layer reason about events without inspecting payloads.
  - Replay-safe: ``causation_id`` + ``correlation_id`` + ``execution_id``
    form a complete provenance graph. ``source_event_ids`` captures ALL
    contributing inputs, not just the immediate parent.
  - Layered architecture: Transport → Envelope → Intent → Governance → Execution.

Refined from discussion incorporating:
  - causation_id (immediate parent) vs source_event_ids (all contributors)
  - classification over allowed_consumers (governance, not auth-in-data)
  - execution_id for retry/replay tracking
  - policy_version for governance versioning under LOSM/PGE
  - subject for transport-level routing

Usage::

    from nats.envelope import CanonicalEnvelope, Classification

    envelope = CanonicalEnvelope(
        event_type="WorkflowPlanned",
        origin_component="cascade",
        correlation_id=workflow_id,
        causation_id=idea_captured_id,
        source_event_ids=[idea_captured_id],
        execution_id=execution_id,
        classification=Classification.INTERNAL,
        subject="nexus.cascade.v1.workflow.workflow_planned",
        payload={...},
    )
"""

from __future__ import annotations

import enum
import os
import uuid
from datetime import datetime, timezone
from typing import Any

# ── Classification ──────────────────────────────────────────────────


class Classification(str, enum.Enum):
    """Governance-oriented classification, not transport authorization.

    Producers tag events with their sensitivity level. Transport ACLs,
    governance engines, and audit systems interpret this — the producer
    does NOT declare who may consume.

    Replaces the earlier ``allowed_consumers`` pattern, which pushed
    authorization into the event and became brittle as consumers grew.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"


# ── Identity helpers ────────────────────────────────────────────────


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _origin_host() -> str | None:
    """Fleet host identity for emitted events (env-injected).

    NEXUS_HOST is set by deployment tooling (bin/fleet-registry-sync.py,
    systemd units, container env). No env → None: absent identity is
    honest data, a fabricated hostname would be a lie in the provenance
    graph.
    """
    return os.environ.get("NEXUS_HOST") or None


def _origin_instance() -> str | None:
    """Fleet instance identity for emitted events (env-injected).

    NEXUS_INSTANCE distinguishes multiple deployments of the same
    component (host-local, container, secondary). Defaults to the host
    name when unset so single-instance hosts still get a usable,
    terrain-compatible instance key.
    """
    return os.environ.get("NEXUS_INSTANCE") or os.environ.get("NEXUS_HOST") or None


# ── CanonicalEnvelope ───────────────────────────────────────────────


class CanonicalEnvelope:
    """Shared event envelope for the NATS bus.

    Designed to be lightweight (no Pydantic dependency at this layer)
    so that the shared ``nats`` package can be imported by any
    subsystem without pulling in a validation framework.

    All fields are plain Python types. Serialization is handled by
    the publishing layer (nats_publisher.py for cascade, Publisher
    for voyager).

    ── Identity ──
    event_id:
        Globally unique. Voyager already uses this key; cascade's
        flat dicts use ``id`` — the adapter maps it.
    event_type:
        camelCase: ``IdeaCaptured``, ``FileObservation``,
        ``StepRequested``, etc.
    event_version:
        Schema version. Increment when the envelope shape changes.

    ── Time ──
    occurred_at:
        ISO 8601 string. Voyager uses int (unix epoch); the adapter
        converts. ISO 8601 is more readable in logs and replay.

    ── Origin ──
    origin_system:
        Always "nexus" for now. Could distinguish federated instances.
    origin_component:
        "cascade", "voyager", "html-importer", "vision", "conduit".
    origin_host:
        The machine this event was emitted on (e.g. "titanium",
        "helium"). Fleet identity: with multiple nexus instances on
        the network, topology is answerable only if every event says
        where it came from. Defaults to NEXUS_HOST, else None —
        absent means "unspecified", never fabricated.
    origin_instance:
        Which deployment instance of the component emitted this
        (e.g. "titanium", "docker-cascade-1"). Defaults to
        NEXUS_INSTANCE, else None. Instance naming must keep the
        terrain.service_endpoints UNIQUE(unit, instance) key
        collision-free across hosts — hostname-encoding is the
        house convention (see bin/endpoint-register.py).

    ── Causality (the provenance graph) ──
    correlation_id:
        Links all events in a single *workflow instance*. Required.
        Example: all events from IdeaCaptured through Integrated share
        one correlation_id (= the workflow's root idea_id).
    causation_id:
        The event that *directly* caused this one (immediate parent).
        Required for non-root events. Example: WorkflowPlanned has
        causation_id = the IdeaCaptured event_id.
    source_event_ids:
        ALL contributing inputs, not just the immediate parent.
        Example: a RequirementCandidate may list the observation that
        triggered it AND the entity it was derived from.

    ── Execution ──
    execution_id:
        Distinguishes retries of the same logical work. Two executions
        of WorkRequest WR-123 share correlation_id but have different
        execution_ids. None for events that are not retryable.

    ── Governance ──
    classification:
        PUBLIC | INTERNAL | RESTRICTED | CONFIDENTIAL
    policy_version:
        Which LOSM/PGE rule set was active when this event was emitted.
        "v27" means "policy set v27 approved this transition".

    ── Transport ──
    subject:
        The NATS subject this event was (or will be) published on.
        Examples: ``nexus.cascade.v1.workflow.step_requested``.
        Carried in the envelope so consumers don't need a parallel
        routing table.

    ── Data ──
    payload:
        System-specific event body. Cascade puts its flat dict here.
        Voyager puts FileObservation, TopologySignal, etc. here.
    """

    __slots__ = (
        "event_id",
        "event_type",
        "event_version",
        "occurred_at",
        "origin_system",
        "origin_component",
        "origin_host",
        "origin_instance",
        "domain",
        "ccnf_version",
        "epoch_id",
        "actor",
        "intent",
        "correlation_id",
        "causation_id",
        "source_event_ids",
        "execution_id",
        "classification",
        "policy_version",
        "subject",
        "payload",
    )

    def __init__(
        self,
        *,
        event_type: str,
        origin_component: str,
        correlation_id: str,
        subject: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        event_version: int = 1,
        occurred_at: str | None = None,
        origin_system: str = "nexus",
        origin_host: str | None = None,
        origin_instance: str | None = None,
        domain: str | None = None,
        ccnf_version: int | None = None,
        epoch_id: str | None = None,
        actor: dict[str, str] | None = None,
        intent: dict[str, str] | None = None,
        causation_id: str | None = None,
        source_event_ids: list[str] | None = None,
        execution_id: str | None = None,
        classification: Classification = Classification.INTERNAL,
        policy_version: str | None = None,
    ) -> None:
        self.event_id = event_id or _new_id()
        self.event_type = event_type
        self.event_version = event_version
        self.occurred_at = occurred_at or _now_iso()
        self.origin_system = origin_system
        self.origin_component = origin_component
        self.origin_host = origin_host if origin_host is not None else _origin_host()
        self.origin_instance = (
            origin_instance if origin_instance is not None else _origin_instance()
        )
        self.domain = domain
        self.ccnf_version = ccnf_version
        self.epoch_id = epoch_id
        self.actor = actor
        self.intent = intent
        self.correlation_id = correlation_id
        self.causation_id = causation_id
        self.source_event_ids = source_event_ids or []
        self.execution_id = execution_id
        self.classification = classification
        self.policy_version = policy_version
        self.subject = subject
        self.payload = payload

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for NATS publish."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "occurred_at": self.occurred_at,
            "origin_system": self.origin_system,
            "origin_component": self.origin_component,
            "origin_host": self.origin_host,
            "origin_instance": self.origin_instance,
            "domain": self.domain,
            "ccnf_version": self.ccnf_version,
            "epoch_id": self.epoch_id,
            "actor": self.actor,
            "intent": self.intent,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "source_event_ids": self.source_event_ids,
            "execution_id": self.execution_id,
            "classification": self.classification.value,
            "policy_version": self.policy_version,
            "subject": self.subject,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalEnvelope:
        """Deserialize from a JSON dict (e.g., from NATS subscriber)."""
        classification_raw = data.get("classification", "internal")
        try:
            classification = Classification(classification_raw)
        except ValueError:
            classification = Classification.INTERNAL

        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            event_version=data.get("event_version", 1),
            occurred_at=data["occurred_at"],
            origin_system=data.get("origin_system", "nexus"),
            origin_component=data["origin_component"],
            origin_host=data.get("origin_host"),
            origin_instance=data.get("origin_instance"),
            domain=data.get("domain"),
            ccnf_version=data.get("ccnf_version"),
            epoch_id=data.get("epoch_id"),
            actor=data.get("actor"),
            intent=data.get("intent"),
            correlation_id=data["correlation_id"],
            causation_id=data.get("causation_id"),
            source_event_ids=data.get("source_event_ids", []),
            execution_id=data.get("execution_id"),
            classification=classification,
            policy_version=data.get("policy_version"),
            subject=data.get("subject", ""),
            payload=data["payload"],
        )

    def __repr__(self) -> str:
        return (
            f"CanonicalEnvelope("
            f"event_id={self.event_id!r}, "
            f"event_type={self.event_type!r}, "
            f"origin_component={self.origin_component!r}, "
            f"correlation_id={self.correlation_id!r})"
        )
