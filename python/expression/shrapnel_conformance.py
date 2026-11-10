"""Expression E9 — Shrapnel storage-adapter conformance bridge (staged-inert).

Blueprint plan 8261656. The Expression layer consumes the SOLScript
conformance descriptor (``solscript.adapters.shrapnel_conformance``) and
exposes the five things the acceptance criteria require of the evaluation
model:

1. **Shrapnel type identity** — storage-independent ``type_id`` + contract
   fingerprint.
2. **Stereotype revision** — the pinned revision/version the contract was
   resolved from.
3. **Effective contract** — required/optional members after parent ∪ child
   resolution.
4. **Inherited field origin** — per-field ``origin_stereotype`` /
   ``origin_revision`` / ``origin_version`` plus an explicit ``inherited`` flag.
5. **Typed unknown/unavailable semantics** — ``governed`` / ``unknown`` /
   ``unavailable`` / ``absent``, never conflated with JSON null.

The bridge is deterministic and side-effect free. It owns no storage, issues no
receipts, and is not wired into a production evaluation path
(``STAGED_INERT = True``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from solscript.adapters.shrapnel_conformance import (
    CONFORMANCE_REVISION,
    AuthorityState,
    FieldStatus,
    LineageRef,
    SemanticDescriptor,
    TypedValue,
)

BRIDGE_REVISION = "expression-shrapnel-conformance-bridge-v1"
STAGED_INERT = True
PRODUCTION_WIRING = False


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FieldOrigin:
    """Where one effective-contract member was declared."""

    property_name: str
    required: bool
    value_type: str
    origin_stereotype: str
    origin_revision: int
    origin_version: int
    inherited: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "property_name": self.property_name,
            "required": self.required,
            "value_type": self.value_type,
            "origin_stereotype": self.origin_stereotype,
            "origin_revision": self.origin_revision,
            "origin_version": self.origin_version,
            "inherited": self.inherited,
        }


@dataclass(frozen=True)
class EvaluationModel:
    """The Expression-visible evaluation model for one Shrapnel instance."""

    type_id: str
    stereotype_name: str
    stereotype_revision: int
    stereotype_version: int
    contract_fingerprint: str
    fields: tuple[FieldOrigin, ...]
    values: Mapping[str, TypedValue]
    lineage: tuple[LineageRef, ...]
    authority: AuthorityState

    def origin(self, property_name: str) -> FieldOrigin:
        for field in self.fields:
            if field.property_name == property_name:
                return field
        raise KeyError(property_name)

    def inherited_field_names(self) -> tuple[str, ...]:
        return tuple(f.property_name for f in self.fields if f.inherited)

    def typed_semantics(self) -> dict[str, str]:
        return {
            name: value.status.value
            for name, value in sorted(self.values.items())
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "bridge_revision": BRIDGE_REVISION,
            "conformance_revision": CONFORMANCE_REVISION,
            "type_identity": {
                "type_id": self.type_id,
                "stereotype_name": self.stereotype_name,
                "stereotype_revision": self.stereotype_revision,
                "stereotype_version": self.stereotype_version,
                "contract_fingerprint": self.contract_fingerprint,
            },
            "effective_contract": [field.as_dict() for field in self.fields],
            "inherited_field_names": list(self.inherited_field_names()),
            "typed_values": [
                self.values[name].as_dict() for name in sorted(self.values)
            ],
            "lineage": [ref.as_dict() for ref in self.lineage],
            "authority": self.authority.as_dict(),
        }


def model_from_descriptor(descriptor: SemanticDescriptor) -> EvaluationModel:
    """Project a semantic descriptor into the Expression evaluation model."""

    contract = descriptor.contract
    origins = tuple(
        FieldOrigin(
            property_name=field.property_name,
            required=field.required,
            value_type=field.value_type,
            origin_stereotype=field.origin_stereotype,
            origin_revision=field.origin_revision,
            origin_version=field.origin_version,
            inherited=field.is_inherited(contract.revision_id),
        )
        for field in contract.fields
    )
    return EvaluationModel(
        type_id=descriptor.identity.type_id,
        stereotype_name=descriptor.identity.stereotype_name,
        stereotype_revision=descriptor.identity.revision_id,
        stereotype_version=descriptor.identity.version,
        contract_fingerprint=descriptor.identity.contract_fingerprint,
        fields=origins,
        values=descriptor.fields,
        lineage=descriptor.lineage,
        authority=descriptor.authority,
    )


def bridge_descriptor(descriptor: SemanticDescriptor) -> dict[str, Any]:
    """Canonical bridge payload: identity, revision, contract, origin, typing."""

    return model_from_descriptor(descriptor).as_dict()


def evaluation_fingerprint(model: EvaluationModel) -> str:
    """Normalized evaluation fingerprint; identical across materializations."""

    return _sha256(model.as_dict())


def evaluate_required_fields(
    model: EvaluationModel,
    required_fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Deterministic disposition over the effective contract's required set.

    The result never invents a value. Any required member that is not
    ``governed`` makes the evaluation unevaluable and names the typed reason;
    origin attribution travels with the result so upstream consumers can see
    which revision supplied the member.
    """

    required = tuple(
        sorted(
            required_fields
            if required_fields is not None
            else (field.property_name for field in model.fields if field.required)
        )
    )
    missing = []
    for name in required:
        typed = model.values.get(name)
        if typed is None:
            missing.append((name, FieldStatus.UNKNOWN.value, "required_field_not_in_contract"))
            continue
        if typed.status is not FieldStatus.GOVERNED:
            missing.append((name, typed.status.value, typed.reason or "not_governed"))

    disposition = "asserted" if not missing else "unevaluable"
    reason_code = None if not missing else missing[0][1]
    return {
        "disposition": disposition,
        "reason_code": reason_code,
        "missing": [
            {"property_name": name, "status": status, "reason": reason}
            for name, status, reason in missing
        ],
        "origin_attribution": {
            name: model.origin(name).as_dict() for name in required if name in model.values
        },
        "typed_semantics": model.typed_semantics(),
        "fingerprint": evaluation_fingerprint(model),
    }


__all__ = [
    "BRIDGE_REVISION",
    "STAGED_INERT",
    "PRODUCTION_WIRING",
    "FieldOrigin",
    "EvaluationModel",
    "model_from_descriptor",
    "bridge_descriptor",
    "evaluation_fingerprint",
    "evaluate_required_fields",
]
