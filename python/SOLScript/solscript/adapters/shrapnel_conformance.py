"""Shrapnel polymorphism and storage-adapter conformance matrix (staged-inert).

Blueprint plan 8261656. This module proves that ONE effective Shrapnel contract
and its SolScript propositions produce identical semantic results for
equivalent PostgreSQL-JSONB and MongoDB-document instance materializations.

Design boundaries (binding):

- **Type identity is storage-independent.** A `TypeIdentity.type_id` is derived
  from stereotype identity + revision only. No backend, collection, table, or
  object id enters it.
- **Instance data lives outside type definitions.** `StereotypeCatalog` carries
  contract revisions (required/optional field declarations) and nothing else.
  `normalize_instance` rejects undeclared instance values rather than letting a
  shadow store leak into the semantic descriptor.
- **One normalizer, two materializations.** The mock adapters differ only in
  how they extract a value container from their wire shape. They share
  `_MockAdapterBase._build_raw`, `build_lineage`, and `normalize_instance`; the
  semantic path has no backend-specific branch.
- **Authority is separated.** PostgreSQL attestation/lineage records are
  authoritative. MongoDB dispatch state is explicitly reconstructable, never
  authoritative. If the authority backend is unavailable the evaluator fails
  closed and never promotes local/Mongo state.
- **Constrained install is explicit.** With neither PostgreSQL nor MongoDB the
  only supported mode is a marked minimal fallback that declares its durability
  and authority loss (`AuthorityMode.MINIMAL_FALLBACK`).
- **Staged-inert.** This module is not wired into any production loader,
  collection, or schema migration. `STAGED_INERT` and `PRODUCTION_WIRING`
  record that fact for the conformance artifact.

The companion artifact lives at `docs/shrapnel/conformance-matrix.md` with
fixtures at `docs/shrapnel/fixtures/conformance-fixtures.json`; the automated
proof is `bin/tests/test_shrapnel_storage_conformance.py`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

CONFORMANCE_REVISION = "shrapnel-storage-conformance-v1"
STAGED_INERT = True
PRODUCTION_WIRING = False

#: WorkRequest lifecycle / dispatch / attestation members. Used only to expose
#: a bounded lifecycle view over a descriptor; never to infer a contract.
WORK_REQUEST_LIFECYCLE_FIELDS = frozenset(
    {
        "request_id",
        "status",
        "created_at",
        "updated_at",
        "dispatch_state",
        "worker_id",
        "dispatch_lease_until",
        "attestation_id",
        "attested_at",
        "attestation_verdict",
    }
)

#: Fields whose only authority is MongoDB dispatch state. They become typed
#: UNAVAILABLE (not silently defaulted) when the dispatch backend is absent.
DISPATCH_FIELDS = frozenset({"dispatch_state", "worker_id", "dispatch_lease_until"})


# ── Canonical helpers ──────────────────────────────────────────────────────


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class FieldStatus(str, Enum):
    """Typed field semantics — no value/error conflation.

    ``GOVERNED``  the contract governs a materialized value (which may be a
                  JSON null: null is a value, not an absence).
    ``UNKNOWN``   a required member was not materialized; we do not invent it.
    ``UNAVAILABLE`` the authority that owns the member could not be read.
    ``ABSENT``    an optional member has no value (typed absence, not null).
    """

    GOVERNED = "governed"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    ABSENT = "absent"


@dataclass(frozen=True)
class TypedValue:
    """A contract member's typed disposition in one evaluation."""

    property_name: str
    status: FieldStatus
    value_type: str
    value: Any = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, FieldStatus):
            object.__setattr__(self, "status", FieldStatus(self.status))
        if self.status is not FieldStatus.GOVERNED and self.value is not None:
            raise ValueError(
                f"typed {self.status.value} field {self.property_name!r} must not "
                "carry a value (that would be local-state promotion)"
            )
        if self.status is FieldStatus.GOVERNED and self.reason is not None:
            raise ValueError("governed field must not carry a reason code")

    def as_dict(self) -> dict[str, Any]:
        return {
            "property_name": self.property_name,
            "status": self.status.value,
            "value_type": self.value_type,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContractField:
    """One member of an effective contract, with inherited origin provenance."""

    property_name: str
    required: bool
    value_type: str
    origin_revision: int
    origin_stereotype: str
    origin_version: int

    def is_inherited(self, revision_id: int) -> bool:
        return self.origin_revision != revision_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "property_name": self.property_name,
            "required": self.required,
            "value_type": self.value_type,
            "origin_revision": self.origin_revision,
            "origin_stereotype": self.origin_stereotype,
            "origin_version": self.origin_version,
        }


@dataclass(frozen=True)
class EffectiveContract:
    """Compiled parent ∪ child contract (nearest declaration wins)."""

    stereotype_name: str
    revision_id: int
    version: int
    depth: int
    parent_revision_id: int | None
    contract_fingerprint: str
    fields: tuple[ContractField, ...]

    def field(self, property_name: str) -> ContractField:
        for member in self.fields:
            if member.property_name == property_name:
                return member
        raise KeyError(property_name)

    def inherited_fields(self) -> tuple[ContractField, ...]:
        return tuple(f for f in self.fields if f.is_inherited(self.revision_id))

    def declared_fields(self) -> tuple[ContractField, ...]:
        return tuple(f for f in self.fields if not f.is_inherited(self.revision_id))

    def as_dict(self) -> dict[str, Any]:
        return {
            "stereotype_name": self.stereotype_name,
            "revision_id": self.revision_id,
            "version": self.version,
            "depth": self.depth,
            "parent_revision_id": self.parent_revision_id,
            "contract_fingerprint": self.contract_fingerprint,
            "fields": [f.as_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class TypeIdentity:
    """Storage-independent Shrapnel type identity."""

    type_id: str
    stereotype_name: str
    revision_id: int
    version: int
    contract_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type_id": self.type_id,
            "stereotype_name": self.stereotype_name,
            "revision_id": self.revision_id,
            "version": self.version,
            "contract_fingerprint": self.contract_fingerprint,
        }


@dataclass(frozen=True)
class LineageRef:
    """A lineage/ attestation reference with its authority classification."""

    relation: str
    ref: str
    authority: str
    reconstructable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "ref": self.ref,
            "authority": self.authority,
            "reconstructable": self.reconstructable,
        }


# ── Authority ──────────────────────────────────────────────────────────────


class AuthorityMode(str, Enum):
    AUTHORITY_SEPARATED = "authority_separated"
    DEGRADED_RECONSTRUCTABLE = "degraded_reconstructable"
    MINIMAL_FALLBACK = "minimal_fallback"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class AuthorityState:
    """Observable authority separation / degradation state."""

    mode: str
    attestation_authority: str
    lineage_authority: str
    dispatch_authority: str
    dispatch_reconstructable: bool
    durability: str
    authority_loss: tuple[str, ...] = ()
    marked_fallback: bool = False
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, AuthorityMode):
            object.__setattr__(self, "mode", AuthorityMode(self.mode).value)

    @property
    def fail_closed(self) -> bool:
        return self.mode == AuthorityMode.FAIL_CLOSED.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "attestation_authority": self.attestation_authority,
            "lineage_authority": self.lineage_authority,
            "dispatch_authority": self.dispatch_authority,
            "dispatch_reconstructable": self.dispatch_reconstructable,
            "durability": self.durability,
            "authority_loss": list(self.authority_loss),
            "marked_fallback": self.marked_fallback,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class AuthorityPolicy:
    """Which stores an installation actually has.

    The policy is the ONLY place store availability is interpreted; the
    normalizer never branches on a backend.
    """

    postgres_available: bool = True
    mongo_available: bool = True

    def assess(self) -> AuthorityState:
        if not self.postgres_available and not self.mongo_available:
            # Constrained install: explicit, marked, declared loss.
            return AuthorityState(
                mode=AuthorityMode.MINIMAL_FALLBACK.value,
                attestation_authority="none",
                lineage_authority="none",
                dispatch_authority="none",
                dispatch_reconstructable=True,
                durability="process_only",
                authority_loss=("attestation", "lineage", "dispatch"),
                marked_fallback=True,
                reason_code="minimal_fallback_no_authority_store",
            )
        if not self.postgres_available:
            # Mongo present but authority absent: never promote its state.
            return AuthorityState(
                mode=AuthorityMode.FAIL_CLOSED.value,
                attestation_authority="none",
                lineage_authority="none",
                dispatch_authority="mongo_reconstructable",
                dispatch_reconstructable=True,
                durability="unavailable",
                authority_loss=("attestation", "lineage"),
                marked_fallback=False,
                reason_code="authority_backend_unavailable",
            )
        if not self.mongo_available:
            return AuthorityState(
                mode=AuthorityMode.DEGRADED_RECONSTRUCTABLE.value,
                attestation_authority="postgres",
                lineage_authority="postgres",
                dispatch_authority="unavailable",
                dispatch_reconstructable=True,
                durability="postgres_only",
                authority_loss=("dispatch_freshness",),
                marked_fallback=False,
                reason_code="dispatch_backend_unavailable",
            )
        return AuthorityState(
            mode=AuthorityMode.AUTHORITY_SEPARATED.value,
            attestation_authority="postgres",
            lineage_authority="postgres",
            dispatch_authority="mongo_reconstructable",
            dispatch_reconstructable=True,
            durability="postgres+mongo",
            authority_loss=(),
            marked_fallback=False,
            reason_code=None,
        )


# ── Contract catalog (type definitions only — never instance data) ─────────


@dataclass(frozen=True)
class FieldSpec:
    property_name: str
    value_type: str = "jsonb"

    def __post_init__(self) -> None:
        if not self.property_name:
            raise ValueError("field spec requires a property_name")


@dataclass(frozen=True)
class RevisionDef:
    """A Shrapnel stereotype revision: contract-level declaration only."""

    stereotype_name: str
    revision_id: int
    version: int = 1
    parent_revision_id: int | None = None
    depth: int = 0
    required: tuple[FieldSpec, ...] = ()
    optional: tuple[FieldSpec, ...] = ()


def _field_specs(raw: Sequence[Mapping[str, Any]] | Sequence[str] | None) -> tuple[FieldSpec, ...]:
    if not raw:
        return ()
    specs: list[FieldSpec] = []
    for entry in raw:
        if isinstance(entry, str):
            specs.append(FieldSpec(entry))
        else:
            specs.append(
                FieldSpec(
                    property_name=str(entry["property_name"]),
                    value_type=str(entry.get("value_type", "jsonb")),
                )
            )
    return tuple(specs)


def revision_defs_from_fixture(payload: Mapping[str, Any]) -> tuple[RevisionDef, ...]:
    """Build revision definitions from the bounded conformance fixture."""

    defs: list[RevisionDef] = []
    catalog = payload["catalog"]
    for stereotype in catalog["stereotypes"]:
        name = str(stereotype["name"])
        for revision in stereotype["revisions"]:
            defs.append(
                RevisionDef(
                    stereotype_name=name,
                    revision_id=int(revision["revision_id"]),
                    version=int(revision.get("version", 1)),
                    parent_revision_id=(
                        int(revision["parent_revision_id"])
                        if revision.get("parent_revision_id") is not None
                        else None
                    ),
                    depth=int(revision.get("depth", 0)),
                    required=_field_specs(revision.get("required")),
                    optional=_field_specs(revision.get("optional")),
                )
            )
    return tuple(defs)


class StereotypeCatalog:
    """Resolves effective contracts from revision declarations.

    The catalog holds *type* definitions. It cannot and does not store instance
    values; callers cannot smuggle payload into identity or contract
    fingerprints because neither is computed from instance input.
    """

    def __init__(self, revisions: Iterable[RevisionDef]):
        self._revisions: dict[int, RevisionDef] = {}
        for revision in revisions:
            if revision.revision_id in self._revisions:
                raise ValueError(f"duplicate revision id {revision.revision_id}")
            self._revisions[revision.revision_id] = revision
        self._validate()
        self._contract_cache: dict[tuple[str, int], EffectiveContract] = {}

    # -- introspection -----------------------------------------------------
    def revisions(self) -> tuple[RevisionDef, ...]:
        return tuple(sorted(self._revisions.values(), key=lambda r: r.revision_id))

    def revision(self, stereotype_name: str, revision_id: int) -> RevisionDef:
        revision = self._revisions.get(revision_id)
        if revision is None:
            raise KeyError(f"unknown revision {revision_id}")
        if revision.stereotype_name != stereotype_name:
            raise ValueError(
                f"revision {revision_id} belongs to {revision.stereotype_name!r}, "
                f"not {stereotype_name!r}"
            )
        return revision

    def type_definitions(self) -> dict[str, Any]:
        """The catalog's canonical, instance-free type definitions."""

        return {
            "revision": CONFORMANCE_REVISION,
            "revisions": [
                {
                    "stereotype_name": r.stereotype_name,
                    "revision_id": r.revision_id,
                    "version": r.version,
                    "parent_revision_id": r.parent_revision_id,
                    "depth": r.depth,
                    "required": [
                        {"property_name": f.property_name, "value_type": f.value_type}
                        for f in r.required
                    ],
                    "optional": [
                        {"property_name": f.property_name, "value_type": f.value_type}
                        for f in r.optional
                    ],
                }
                for r in self.revisions()
            ],
        }

    # -- contract resolution ----------------------------------------------
    def effective_contract(self, stereotype_name: str, revision_id: int) -> EffectiveContract:
        key = (stereotype_name, revision_id)
        cached = self._contract_cache.get(key)
        if cached is not None:
            return cached

        revision = self.revision(stereotype_name, revision_id)
        if revision.parent_revision_id is None:
            merged: dict[str, ContractField] = {}
            parent_fingerprint_basis: int | None = None
        else:
            parent_def = self._revisions.get(revision.parent_revision_id)
            if parent_def is None:
                raise ValueError(
                    f"revision {revision_id} pins missing parent "
                    f"{revision.parent_revision_id}"
                )
            parent = self.effective_contract(
                parent_def.stereotype_name, parent_def.revision_id
            )
            parent_fingerprint_basis = parent.revision_id
            merged = {f.property_name: f for f in parent.fields}
            parent_required = {f.property_name for f in parent.fields if f.required}
            child_required = {f.property_name for f in revision.required}
            missing = parent_required - child_required
            if missing:
                raise ValueError(
                    f"revision {revision_id} is not a monotonic upgrade: "
                    f"parent-required fields dropped {sorted(missing)}"
                )

        declared = [(spec, True) for spec in revision.required] + [
            (spec, False) for spec in revision.optional
        ]
        for spec, required in declared:
            merged[spec.property_name] = ContractField(
                property_name=spec.property_name,
                required=required,
                value_type=spec.value_type,
                origin_revision=revision.revision_id,
                origin_stereotype=revision.stereotype_name,
                origin_version=revision.version,
            )

        ordered = tuple(
            sorted(merged.values(), key=lambda f: (not f.required, f.property_name))
        )
        fingerprint = _sha256(
            {
                "stereotype_name": revision.stereotype_name,
                "revision_id": revision.revision_id,
                "version": revision.version,
                "parent_revision_id": parent_fingerprint_basis,
                "fields": [
                    {"property_name": f.property_name, "required": f.required}
                    for f in sorted(ordered, key=lambda f: f.property_name)
                ],
            }
        )
        contract = EffectiveContract(
            stereotype_name=revision.stereotype_name,
            revision_id=revision.revision_id,
            version=revision.version,
            depth=revision.depth,
            parent_revision_id=revision.parent_revision_id,
            contract_fingerprint=fingerprint,
            fields=ordered,
        )
        self._contract_cache[key] = contract
        return contract

    def identity(self, stereotype_name: str, revision_id: int) -> TypeIdentity:
        contract = self.effective_contract(stereotype_name, revision_id)
        return TypeIdentity(
            # Storage-independent: no backend / collection / table / object id.
            type_id=f"shrapnel:stereotype:{contract.stereotype_name}",
            stereotype_name=contract.stereotype_name,
            revision_id=contract.revision_id,
            version=contract.version,
            contract_fingerprint=contract.contract_fingerprint,
        )

    # -- validation --------------------------------------------------------
    def _validate(self) -> None:
        for revision in self._revisions.values():
            if revision.parent_revision_id is None:
                continue
            parent = self._revisions.get(revision.parent_revision_id)
            if parent is None:
                raise ValueError(
                    f"revision {revision.revision_id} pins unknown parent "
                    f"{revision.parent_revision_id}"
                )
            if parent.revision_id == revision.revision_id:
                raise ValueError(f"revision {revision.revision_id} extends itself")
            if revision.depth != parent.depth + 1:
                raise ValueError(
                    f"revision {revision.revision_id} depth {revision.depth} "
                    f"does not follow parent depth {parent.depth}"
                )
            if revision.depth > 3:
                raise ValueError("stereotype depth exceeds 3")
            required_names = {f.property_name for f in revision.required}
            optional_names = {f.property_name for f in revision.optional}
            overlap = required_names & optional_names
            if overlap:
                raise ValueError(
                    f"revision {revision.revision_id} declares fields as both "
                    f"required and optional: {sorted(overlap)}"
                )


# ── Instances and semantic descriptors ─────────────────────────────────────


@dataclass(frozen=True)
class RawInstance:
    """A materialized instance after adapter extraction, before normalization."""

    object_id: str
    stereotype_name: str
    revision_id: int
    values: Mapping[str, Any]
    backend: str
    materialization: str
    lineage: tuple[LineageRef, ...]
    timestamps: Mapping[str, str | None]
    authority: AuthorityState

    def as_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "stereotype_name": self.stereotype_name,
            "revision_id": self.revision_id,
            "values": dict(self.values),
            "backend": self.backend,
            "materialization": self.materialization,
            "lineage": [ref.as_dict() for ref in self.lineage],
            "timestamps": dict(self.timestamps),
            "authority": self.authority.as_dict(),
        }


@dataclass(frozen=True)
class SemanticDescriptor:
    """Backend-independent semantic result of one materialization."""

    identity: TypeIdentity
    contract: EffectiveContract
    fields: Mapping[str, TypedValue]
    lineage: tuple[LineageRef, ...]
    timestamps: Mapping[str, str | None]
    authority: AuthorityState
    object_id: str
    materialization: str

    def field(self, property_name: str) -> TypedValue:
        return self.fields[property_name]

    def inherited_field_names(self) -> tuple[str, ...]:
        return tuple(f.property_name for f in self.contract.inherited_fields())

    def missing_required(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, value in self.fields.items()
                if value.status is not FieldStatus.GOVERNED
                and self.contract.field(name).required
            )
        )

    def lifecycle(self) -> dict[str, TypedValue]:
        return {
            name: value
            for name, value in self.fields.items()
            if name in WORK_REQUEST_LIFECYCLE_FIELDS
        }

    def as_semantic_dict(self) -> dict[str, Any]:
        """Canonical semantic surface — deliberately excludes backend and id."""

        return {
            "conformance_revision": CONFORMANCE_REVISION,
            "identity": self.identity.as_dict(),
            "contract": self.contract.as_dict(),
            "fields": [
                self.fields[name].as_dict() for name in sorted(self.fields)
            ],
            "lineage": [
                ref.as_dict()
                for ref in sorted(
                    self.lineage, key=lambda r: (r.relation, r.ref, r.authority)
                )
            ],
            "timestamps": {k: self.timestamps[k] for k in sorted(self.timestamps)},
            "authority": self.authority.as_dict(),
        }


def build_lineage(entries: Sequence[Mapping[str, Any]]) -> tuple[LineageRef, ...]:
    """Classify lineage entries identically for every materialization."""

    refs: list[LineageRef] = []
    for entry in entries:
        relation = str(entry["relation"])
        ref = str(entry["ref"])
        if relation == "dispatch":
            refs.append(
                LineageRef(
                    relation=relation,
                    ref=ref,
                    authority="mongo_reconstructable",
                    reconstructable=True,
                )
            )
        else:
            refs.append(
                LineageRef(
                    relation=relation,
                    ref=ref,
                    authority="postgres",
                    reconstructable=False,
                )
            )
    return tuple(sorted(refs, key=lambda r: (r.relation, r.ref)))


def normalize_instance(
    raw: RawInstance,
    catalog: StereotypeCatalog,
    *,
    unavailable_fields: Iterable[str] = (),
) -> SemanticDescriptor:
    """Normalize any extracted instance into the one semantic descriptor.

    This is the single semantic path. It has no backend, collection, or
    materialization branch: those differences stop at the adapter boundary.
    """

    contract = catalog.effective_contract(raw.stereotype_name, raw.revision_id)
    identity = catalog.identity(raw.stereotype_name, raw.revision_id)
    declared = {f.property_name for f in contract.fields}
    undeclared = sorted(set(raw.values) - declared)
    if undeclared:
        raise ValueError(
            "instance carries values not declared by the effective contract "
            f"({raw.stereotype_name}@{raw.revision_id}): {undeclared}; "
            "a shadow semantic store is not permitted"
        )

    unavailable = frozenset(unavailable_fields)
    typed: dict[str, TypedValue] = {}
    for member in contract.fields:
        name = member.property_name
        if name in unavailable:
            typed[name] = TypedValue(
                property_name=name,
                status=FieldStatus.UNAVAILABLE,
                value_type=member.value_type,
                reason="authority_backend_unavailable",
            )
        elif name in raw.values:
            typed[name] = TypedValue(
                property_name=name,
                status=FieldStatus.GOVERNED,
                value_type=member.value_type,
                value=raw.values[name],
            )
        elif member.required:
            typed[name] = TypedValue(
                property_name=name,
                status=FieldStatus.UNKNOWN,
                value_type=member.value_type,
                reason="required_value_not_materialized",
            )
        else:
            typed[name] = TypedValue(
                property_name=name,
                status=FieldStatus.ABSENT,
                value_type=member.value_type,
                reason="optional_value_not_materialized",
            )

    return SemanticDescriptor(
        identity=identity,
        contract=contract,
        fields=typed,
        lineage=raw.lineage,
        timestamps={k: raw.timestamps[k] for k in sorted(raw.timestamps)},
        authority=raw.authority,
        object_id=raw.object_id,
        materialization=raw.materialization,
    )


def semantic_fingerprint(descriptor: SemanticDescriptor) -> str:
    """Normalized evaluation fingerprint: storage-independent and lineage-exact."""

    return _sha256(descriptor.as_semantic_dict())


# ── Mock materialization adapters ──────────────────────────────────────────


class _MockAdapterBase:
    """Shared adapter machinery; wire-shape extraction is the only difference."""

    backend = ""
    materialization = ""

    def __init__(self, catalog: StereotypeCatalog, policy: AuthorityPolicy | None = None):
        self.catalog = catalog
        self.policy = policy or AuthorityPolicy()
        self._instances: dict[str, RawInstance] = {}
        self._sequence = 0

    # -- store -------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    def _build_raw(
        self,
        *,
        object_id: str,
        stereotype_name: str,
        revision_id: int,
        values: Mapping[str, Any],
        lineage_entries: Sequence[Mapping[str, Any]],
        timestamps: Mapping[str, str | None],
    ) -> RawInstance:
        return RawInstance(
            object_id=object_id,
            stereotype_name=stereotype_name,
            revision_id=revision_id,
            values=dict(values),
            backend=self.backend,
            materialization=self.materialization,
            lineage=build_lineage(lineage_entries),
            timestamps=dict(timestamps),
            authority=self.policy.assess(),
        )

    def read(self, object_id: str) -> RawInstance:
        try:
            return self._instances[object_id]
        except KeyError as exc:
            raise KeyError(f"unknown materialized instance {object_id!r}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._instances))

    def _unavailable_fields(self) -> frozenset[str]:
        state = self.policy.assess()
        if (
            state.mode == AuthorityMode.DEGRADED_RECONSTRUCTABLE.value
            or state.dispatch_authority == "unavailable"
        ):
            return DISPATCH_FIELDS
        return frozenset()

    def descriptor(
        self,
        object_id: str,
        *,
        unavailable_fields: Iterable[str] | None = None,
    ) -> SemanticDescriptor:
        unavailable = (
            self._unavailable_fields()
            if unavailable_fields is None
            else frozenset(unavailable_fields)
        )
        return normalize_instance(
            self.read(object_id), self.catalog, unavailable_fields=unavailable
        )

    def fingerprint(self, object_id: str) -> str:
        return semantic_fingerprint(self.descriptor(object_id))

    # -- interface ---------------------------------------------------------
    def write(self, document: Mapping[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError

    def encode(self, raw: RawInstance) -> dict[str, Any]:  # pragma: no cover
        """Re-emit a raw instance in this adapter's wire shape (round trips)."""
        raise NotImplementedError


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _extract_common(document: Mapping[str, Any], *, stereotype_key: str, revision_key: str, values_key: str) -> tuple[
    str | None, str, int, dict[str, Any], list[Mapping[str, Any]], dict[str, str | None]
]:
    doc = _require_mapping(document, "instance document")
    stereotype_name = str(doc[stereotype_key])
    revision_id = int(doc[revision_key])
    values = dict(_require_mapping(doc.get(values_key) or {}, f"{values_key} values"))
    lineage = list(doc.get("lineage") or [])
    timestamps = dict(doc.get("timestamps") or {})
    object_id = doc.get("object_id") if "object_id" in doc else doc.get("_id")
    return (
        str(object_id) if object_id is not None else None,
        stereotype_name,
        revision_id,
        values,
        lineage,
        timestamps,
    )


class MockJsonbAdapter(_MockAdapterBase):
    """In-memory PostgreSQL-JSONB-shaped materialization (proof layer only).

    Shape::

        {
          "object_id": "4105",
          "stereotype": "work_request_tester_attestation",
          "stereotype_revision_id": 3,
          "values": {...},            # decoded OAV values
          "jsonb": {...},             # optional nested JSONB payload
          "lineage": [{"relation": "attestation", "ref": "..."}],
          "timestamps": {"created_at": "...", "updated_at": "..."}
        }
    """

    backend = "postgres_jsonb"
    materialization = "postgres_jsonb"

    def write(self, document: Mapping[str, Any]) -> str:
        object_id, stereotype, revision, values, lineage, timestamps = _extract_common(
            document,
            stereotype_key="stereotype",
            revision_key="stereotype_revision_id",
            values_key="values",
        )
        payload = document.get("jsonb")
        if payload is not None:
            values = {**dict(_require_mapping(payload, "jsonb payload")), **values}
        object_id = object_id or self._next_id("pg")
        raw = self._build_raw(
            object_id=object_id,
            stereotype_name=stereotype,
            revision_id=revision,
            values=values,
            lineage_entries=lineage,
            timestamps=timestamps,
        )
        self._instances[object_id] = raw
        return object_id

    def encode(self, raw: RawInstance) -> dict[str, Any]:
        return {
            "object_id": raw.object_id,
            "stereotype": raw.stereotype_name,
            "stereotype_revision_id": raw.revision_id,
            "values": dict(raw.values),
            "lineage": [ref.as_dict() for ref in raw.lineage],
            "timestamps": dict(raw.timestamps),
        }


class MockMongoAdapter(_MockAdapterBase):
    """In-memory MongoDB-document-shaped materialization (ephemeral proof layer).

    Shape::

        {
          "_id": "6a...",
          "type": "work_request_tester_attestation",
          "revision": 3,
          "doc": {...},               # document body
          "dispatch": {...},          # dispatch state (reconstructable)
          "lineage": [{"relation": "dispatch", "ref": "..."}],
          "timestamps": {"created_at": "...", "updated_at": "..."}
        }
    """

    backend = "mongo_document"
    materialization = "mongo_document"

    def write(self, document: Mapping[str, Any]) -> str:
        object_id, stereotype, revision, values, lineage, timestamps = _extract_common(
            document,
            stereotype_key="type",
            revision_key="revision",
            values_key="doc",
        )
        dispatch = document.get("dispatch")
        if dispatch is not None:
            values = {**values, **dict(_require_mapping(dispatch, "dispatch state"))}
        object_id = object_id or self._next_id("mongo")
        raw = self._build_raw(
            object_id=object_id,
            stereotype_name=stereotype,
            revision_id=revision,
            values=values,
            lineage_entries=lineage,
            timestamps=timestamps,
        )
        self._instances[object_id] = raw
        return object_id

    def encode(self, raw: RawInstance) -> dict[str, Any]:
        return {
            "_id": raw.object_id,
            "type": raw.stereotype_name,
            "revision": raw.revision_id,
            "doc": dict(raw.values),
            "lineage": [ref.as_dict() for ref in raw.lineage],
            "timestamps": dict(raw.timestamps),
        }


# ── Evaluation result (authority boundary) ─────────────────────────────────


@dataclass(frozen=True)
class EvaluationResult:
    mode: str
    disposition: str
    reason_code: str | None
    authority: AuthorityState
    descriptor: SemanticDescriptor | None
    fingerprint: str | None

    @property
    def usable(self) -> bool:
        return self.descriptor is not None and self.disposition == "asserted"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "disposition": self.disposition,
            "reason_code": self.reason_code,
            "authority": self.authority.as_dict(),
            "fingerprint": self.fingerprint,
            "descriptor": (
                self.descriptor.as_semantic_dict() if self.descriptor is not None else None
            ),
        }


def evaluate_materialization(
    adapter: _MockAdapterBase,
    document: Mapping[str, Any],
    *,
    unavailable_fields: Iterable[str] | None = None,
) -> EvaluationResult:
    """Evaluate one materialized instance through the authority boundary.

    FAIL_CLOSED: no descriptor is produced and no fingerprint is minted. Local
    or Mongo state is never promoted to authority just because it is readable.
    """

    state = adapter.policy.assess()
    if state.mode == AuthorityMode.FAIL_CLOSED.value:
        return EvaluationResult(
            mode=state.mode,
            disposition="refused",
            reason_code=state.reason_code or "authority_backend_unavailable",
            authority=state,
            descriptor=None,
            fingerprint=None,
        )

    object_id = adapter.write(document)
    descriptor = adapter.descriptor(object_id, unavailable_fields=unavailable_fields)

    if state.mode == AuthorityMode.MINIMAL_FALLBACK.value:
        return EvaluationResult(
            mode=state.mode,
            disposition="unevaluable",
            reason_code=state.reason_code,
            authority=state,
            descriptor=descriptor,
            fingerprint=semantic_fingerprint(descriptor),
        )

    return EvaluationResult(
        mode=state.mode,
        disposition="asserted",
        reason_code=state.reason_code,
        authority=state,
        descriptor=descriptor,
        fingerprint=semantic_fingerprint(descriptor),
    )


#: Enumeration point for the conformance matrix — add a materialization here
#: and the parity tests pick it up; never branch on it in the normalizer.
MOCK_CONFORMANCE_ADAPTERS = (MockJsonbAdapter, MockMongoAdapter)


def minimal_fallback_descriptor(
    catalog: StereotypeCatalog,
    *,
    stereotype_name: str,
    revision_id: int,
    values: Mapping[str, Any],
    timestamps: Mapping[str, str | None] | None = None,
    lineage: Sequence[Mapping[str, Any]] = (),
) -> SemanticDescriptor:
    """Build the marked constrained-install descriptor (no authority store)."""

    state = AuthorityPolicy(postgres_available=False, mongo_available=False).assess()
    raw = RawInstance(
        object_id="process-only",
        stereotype_name=stereotype_name,
        revision_id=revision_id,
        values=dict(values),
        backend="none",
        materialization="minimal_fallback",
        lineage=build_lineage(lineage),
        timestamps=dict(timestamps or {}),
        authority=state,
    )
    return normalize_instance(raw, catalog)


__all__ = [
    "CONFORMANCE_REVISION",
    "STAGED_INERT",
    "PRODUCTION_WIRING",
    "WORK_REQUEST_LIFECYCLE_FIELDS",
    "DISPATCH_FIELDS",
    "FieldStatus",
    "TypedValue",
    "ContractField",
    "EffectiveContract",
    "TypeIdentity",
    "LineageRef",
    "AuthorityMode",
    "AuthorityState",
    "AuthorityPolicy",
    "FieldSpec",
    "RevisionDef",
    "revision_defs_from_fixture",
    "StereotypeCatalog",
    "RawInstance",
    "SemanticDescriptor",
    "build_lineage",
    "normalize_instance",
    "semantic_fingerprint",
    "MockJsonbAdapter",
    "MockMongoAdapter",
    "MOCK_CONFORMANCE_ADAPTERS",
    "EvaluationResult",
    "evaluate_materialization",
    "minimal_fallback_descriptor",
]
