"""cir_sdm_gate.py — SOL-framed admission gates for CIR-SDM (#5) + CCNF version-lock (#6).

Per RULING R-D (record 2487aef3, 2026-08-25), CIR-SDM one-way-gate and CCNF
version-lock are **SOL-framed gates**: propositions framed on the already
registered shared-vocabulary dimensions (``cir_sdm_mode``,
``enforcement_rule_family``, ``ccnf_version``), evaluated by the existing
SOLScript evaluator, with outcomes recorded to ``peb.transactions`` via
``cascade.peb_admission`` — identical to the landed ``sol_gate`` /
``watch_gate`` / ``promotion_gate`` pattern (advisory record-then-act;
recording never flips the gate).

Two propositions:

1. ``"violation is governed"`` (``cir-gate:violation-governed``) — #5.
   Framed on ``enforcement_rule_family`` + ``cir_sdm_mode`` (the family's
   active posture). Assertion: the family is architect-authorized
   (``authorized_by`` cited) — the mechanically checkable "no silent
   addition" rule of 4a57c089. A violation in an enforced, authorized
   family is a **governed decision** (blocking); shadow families are not.

2. ``"event version is governed"`` (``ccnf-gate:version-governed``) — #6.
   Framed on ``ccnf_version`` (typed scalar). Assertion: the version-lock
   family is architect-authorized (enforced). A well-formed event failing
   the version-lock rule is PEB-recorded as a violation, never a raw
   exception (R-D R4); genuinely malformed input that cannot even build a
   proposition still raises (that is a contract error, not a governance
   outcome).

Interface::

    from nexus_core.wrp.cir_sdm_gate import (
        evaluate_cir_sdm_violation, evaluate_ccnf_version_lock,
    )

    governed, reason = evaluate_cir_sdm_violation(violation, posture_rows)
    governed, reason = evaluate_ccnf_version_lock(event, posture_rows)

Both record the outcome into ``peb.transactions`` before returning
(advisory record-then-act; a recording failure never raises).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional


def _python_root() -> str:
    """Find the python/ root (dir containing cascade/ + SOLScript/)."""
    import os
    path = os.path.dirname(os.path.abspath(__file__))
    while os.path.dirname(path) != path:
        if (os.path.isdir(os.path.join(path, "cascade"))
                and os.path.isdir(os.path.join(path, "SOLScript"))):
            return path
        path = os.path.dirname(path)
    return path


_IMPORTED = False


def _import_solscript() -> None:
    global _IMPORTED
    if _IMPORTED:
        return
    import sys
    root = _python_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    _IMPORTED = True


# ── Fixed IDs (built once, reused forever) ─────────────────────────────

# #5 CIR-SDM proposition
_CIR_CONCEPT_ID       = "cir-gate:CIRViolation"
_FAMILY_ATTR_ID       = "cir-gate:enforcement_rule_family"
_MODE_ATTR_ID         = "cir-gate:cir_sdm_mode"
_AUTHORIZED_ATTR_ID   = "cir-gate:authorized_by"
_FAMILY_DIM_ID        = "cir-gate:enforcement_rule_family_dim"
_MODE_DIM_ID          = "cir-gate:cir_sdm_mode_dim"
_CIR_PROP_ID          = "cir-gate:violation-governed"
_CIR_ENTITY_ID        = "cir-gate:violation-entity"
_CIR_ASSERTION_ID     = "cir-gate:assertion:authorized"
_AUTHORIZED_NEQ_ID    = "cir-gate:expr:authorized-neq"
_AUTHORIZED_REF_ID    = "cir-gate:expr:authorized-ref"
_EMPTY_LIT_ID         = "cir-gate:expr:empty-lit"

# #6 CCNF version-lock proposition
_CCNF_CONCEPT_ID      = "ccnf-gate:CcnfEvent"
_VERSION_ATTR_ID      = "ccnf-gate:ccnf_version"
_VERSION_LOCK_ATTR_ID = "ccnf-gate:version_lock_authorized"
_VERSION_DIM_ID       = "ccnf-gate:ccnf_version_dim"
_CCNF_PROP_ID         = "ccnf-gate:version-governed"
_CCNF_ENTITY_ID       = "ccnf-gate:event-entity"
_CCNF_ASSERTION_ID    = "ccnf-gate:assertion:version-lock-authorized"
_VERSION_AUTH_NEQ_ID  = "ccnf-gate:expr:version-lock-auth-neq"
_VERSION_AUTH_REF_ID  = "ccnf-gate:expr:version-lock-auth-ref"
_VERSION_EMPTY_LIT_ID = "ccnf-gate:expr:version-lock-empty-lit"

# All 7 CIR-SDM rule families (stable ids; mirror cir_sdm.RULE_*).
_ALL_FAMILIES = (
    "cir-sdm.one-way-gate",
    "cir-sdm.audit-non-influence",
    "cir-sdm.provenance-causation",
    "cir-sdm.version-lock",
    "cir-sdm.ir-stage-separation",
    "cir-sdm.core-stage-separation",
    "cir-sdm.ir-payload-separation",
)

# ── Shared immutable state (built once, never mutated) ────────────────
#
# C2 (inspector report 5d45f921 / architect plan 8261648): the evaluation
# path must never write module-level state. _STATE is built once by
# _build() and only ever READ afterward; each evaluation deep-copies it
# and mutates only its private copy. Cached PropositionFrameValue objects
# are handed to the copy by reference — the interpreter only reads them.

_STATE: Optional[Dict[str, Any]] = None


def _family_posture(
    family: str,
    posture_rows: Optional[Iterable[Dict[str, Any]]],
) -> tuple[str, Optional[str]]:
    """(mode, authorized_by) for a family from active posture rows.

    Defaults to ``("shadow", None)`` when the family has no row (fail closed
    — an unregistered family is never enforced)."""
    for r in posture_rows or []:
        if r.get("family") == family:
            return (r.get("mode") or "shadow", r.get("authorized_by"))
    return ("shadow", None)


def _normalize_violation(violation: Any) -> Dict[str, Any]:
    """Normalize a CIRViolation (object or dict) to a plain dict."""
    if isinstance(violation, dict):
        return violation
    return {
        "violation_id": getattr(violation, "violation_id", ""),
        "rule_id": getattr(violation, "rule_id", ""),
        "severity": getattr(violation, "severity", ""),
        "event_id": getattr(violation, "event_id", ""),
        "description": getattr(violation, "description", ""),
        "blocking": getattr(violation, "blocking", False),
    }


# ── Builder ───────────────────────────────────────────────────────────

def _build() -> None:
    global _STATE

    _import_solscript()
    from SOLScript.solscript import (                   # type: ignore[import-untyped]
        Concept, ConceptAttribute, Entity,
        Expression, ExpressionKind, Operator,
        FrameDimension, FrameDimensionValue,
        Proposition, PropositionFrameValue,
        ResolutionInterpreter, Rule, RuleType, Severity,
        Disposition,
    )

    interp = ResolutionInterpreter()

    # ── #5: CIRViolation concept ──────────────────────────────────

    cir_concept = Concept(
        id=_CIR_CONCEPT_ID,
        name="CIRViolation",
        description="A CIR-SDM violation under evaluation for governance",
    )
    interp.add_concept(cir_concept)

    family_attr = ConceptAttribute(
        id=_FAMILY_ATTR_ID,
        concept_id=_CIR_CONCEPT_ID,
        name="enforcement_rule_family",
        description="The rule family that fired",
        value_type="text",
        is_state_attribute=False,
    )
    cir_concept.attributes[family_attr.id] = family_attr

    mode_attr = ConceptAttribute(
        id=_MODE_ATTR_ID,
        concept_id=_CIR_CONCEPT_ID,
        name="cir_sdm_mode",
        description="Enforcement posture mode for the family (enforced|shadow)",
        value_type="text",
        is_state_attribute=False,
    )
    cir_concept.attributes[mode_attr.id] = mode_attr

    authorized_attr = ConceptAttribute(
        id=_AUTHORIZED_ATTR_ID,
        concept_id=_CIR_CONCEPT_ID,
        name="authorized_by",
        description="Architect decision id that admitted the family ('' = not authorized)",
        value_type="text",
        is_state_attribute=False,
    )
    cir_concept.attributes[authorized_attr.id] = authorized_attr

    # enforcement_rule_family frame dimension (governed_reference)
    family_dim = FrameDimension(
        id=_FAMILY_DIM_ID,
        name="enforcement_rule_family",
        description="Which rule family is in scope",
        value_kind="governed_reference",
    )
    interp.add_frame_dimension(family_dim)
    for family in _ALL_FAMILIES:
        interp.add_frame_dimension_value(FrameDimensionValue(
            id=f"cir-gate:family:{family}",
            dimension_id=_FAMILY_DIM_ID,
            value=family,
        ))

    # cir_sdm_mode frame dimension (governed_reference)
    mode_dim = FrameDimension(
        id=_MODE_DIM_ID,
        name="cir_sdm_mode",
        description="Enforcement posture mode",
        value_kind="governed_reference",
    )
    interp.add_frame_dimension(mode_dim)
    for mode in ("enforced", "shadow"):
        interp.add_frame_dimension_value(FrameDimensionValue(
            id=f"cir-gate:mode:{mode}",
            dimension_id=_MODE_DIM_ID,
            value=mode,
        ))

    # Assertion: authorized_by <> ''  (family is architect-authorized)
    cir_assertion = Rule(
        id=_CIR_ASSERTION_ID,
        name="family is architect-authorized",
        rule_type=RuleType.INVARIANT,
        severity=Severity.HARD,
        expression=Expression(
            id=_AUTHORIZED_NEQ_ID,
            kind=ExpressionKind.OPERATOR,
            operator=Operator.NEQ,
            return_type="boolean",
            operands=[
                Expression(
                    id=_AUTHORIZED_REF_ID,
                    kind=ExpressionKind.ATTRIBUTE_REF,
                    return_type="text",
                    attribute_id=_AUTHORIZED_ATTR_ID,
                ),
                Expression(
                    id=_EMPTY_LIT_ID,
                    kind=ExpressionKind.LITERAL,
                    return_type="text",
                    literal_value="",
                ),
            ],
        ),
        concept_id=_CIR_CONCEPT_ID,
    )
    cir_concept.invariants.append(cir_assertion)
    interp.rules[cir_assertion.id] = cir_assertion

    cir_prop = Proposition(
        id=_CIR_PROP_ID,
        title="violation is governed",
        description="A violation in an enforced, architect-authorized family is a governed decision",
        asset_concept_id=_CIR_CONCEPT_ID,
        subject_entity_id=_CIR_ENTITY_ID,
        disposition=Disposition.PENDING,
        assertions=[cir_assertion],
    )
    interp.add_proposition(cir_prop)

    # Pre-built frame values per family / mode (read-only; handed to the
    # per-call clone by reference)
    family_pfv: Dict[str, PropositionFrameValue] = {}
    for family in _ALL_FAMILIES:
        family_pfv[family] = PropositionFrameValue(
            id=f"cir-gate:violation-governed:frame:family:{family}",
            proposition_id=_CIR_PROP_ID,
            dimension_id=_FAMILY_DIM_ID,
            reference_value_id=f"cir-gate:family:{family}",
            scalar_value=None,
        )
    mode_pfv: Dict[str, PropositionFrameValue] = {}
    for mode in ("enforced", "shadow"):
        mode_pfv[mode] = PropositionFrameValue(
            id=f"cir-gate:violation-governed:frame:mode:{mode}",
            proposition_id=_CIR_PROP_ID,
            dimension_id=_MODE_DIM_ID,
            reference_value_id=f"cir-gate:mode:{mode}",
            scalar_value=None,
        )

    # ── #6: CcnfEvent concept ─────────────────────────────────────

    ccnf_concept = Concept(
        id=_CCNF_CONCEPT_ID,
        name="CcnfEvent",
        description="A CCNF-compiled event under version-lock evaluation",
    )
    interp.add_concept(ccnf_concept)

    version_attr = ConceptAttribute(
        id=_VERSION_ATTR_ID,
        concept_id=_CCNF_CONCEPT_ID,
        name="ccnf_version",
        description="CCNF compiler version of the event",
        value_type="integer",
        is_state_attribute=False,
    )
    ccnf_concept.attributes[version_attr.id] = version_attr

    version_lock_attr = ConceptAttribute(
        id=_VERSION_LOCK_ATTR_ID,
        concept_id=_CCNF_CONCEPT_ID,
        name="version_lock_authorized",
        description="'' when version-lock family is not architect-authorized",
        value_type="text",
        is_state_attribute=False,
    )
    ccnf_concept.attributes[version_lock_attr.id] = version_lock_attr

    # ccnf_version frame dimension (typed_scalar integer, mirrors v34)
    version_dim = FrameDimension(
        id=_VERSION_DIM_ID,
        name="ccnf_version",
        description="CCNF compiler version in scope",
        value_kind="typed_scalar",
        scalar_type="integer",
    )
    interp.add_frame_dimension(version_dim)

    ccnf_assertion = Rule(
        id=_CCNF_ASSERTION_ID,
        name="version-lock family is architect-authorized",
        rule_type=RuleType.INVARIANT,
        severity=Severity.HARD,
        expression=Expression(
            id=_VERSION_AUTH_NEQ_ID,
            kind=ExpressionKind.OPERATOR,
            operator=Operator.NEQ,
            return_type="boolean",
            operands=[
                Expression(
                    id=_VERSION_AUTH_REF_ID,
                    kind=ExpressionKind.ATTRIBUTE_REF,
                    return_type="text",
                    attribute_id=_VERSION_LOCK_ATTR_ID,
                ),
                Expression(
                    id=_VERSION_EMPTY_LIT_ID,
                    kind=ExpressionKind.LITERAL,
                    return_type="text",
                    literal_value="",
                ),
            ],
        ),
        concept_id=_CCNF_CONCEPT_ID,
    )
    ccnf_concept.invariants.append(ccnf_assertion)
    interp.rules[ccnf_assertion.id] = ccnf_assertion

    ccnf_prop = Proposition(
        id=_CCNF_PROP_ID,
        title="event version is governed",
        description="A well-formed event failing the version-lock family is PEB-recorded, never a raw exception",
        asset_concept_id=_CCNF_CONCEPT_ID,
        subject_entity_id=_CCNF_ENTITY_ID,
        disposition=Disposition.PENDING,
        assertions=[ccnf_assertion],
    )
    interp.add_proposition(ccnf_prop)

    _STATE = {
        "interp": interp,
        "cir_prop": cir_prop,
        "ccnf_prop": ccnf_prop,
        "family_pfv": family_pfv,
        "mode_pfv": mode_pfv,
        "version_pfv_cache": {},
    }


# ── Public API ────────────────────────────────────────────────────────

def evaluate_cir_sdm_violation(
    violation: Any,
    posture_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> tuple[bool, str]:
    """#5 — is this CIR-SDM violation a governed decision (blocking)?"""

    outcome = _evaluate_cir_sdm_violation(violation, posture_rows)
    _record_admission(
        gate="cir_sdm_gate.evaluate_cir_sdm_violation",
        entity_id=str(_normalize_violation(violation).get("violation_id", "")),
        admitted=outcome[0],
        reason=outcome[1],
        payload={"violation": _normalize_violation(violation)},
    )
    return outcome


def _evaluate_cir_sdm_violation(
    violation: Any,
    posture_rows: Optional[Iterable[Dict[str, Any]]],
) -> tuple[bool, str]:
    """Internal evaluation core (wrapped by evaluate_cir_sdm_violation)."""
    if _STATE is None:
        _build()

    from SOLScript.solscript import Entity  # type: ignore[import-untyped]

    assert _STATE is not None

    v = _normalize_violation(violation)
    family = v.get("rule_id", "")
    if not family:
        return False, "no rule family on violation"

    mode, authorized = _family_posture(family, posture_rows)
    family_pfv = _STATE["family_pfv"]
    mode_pfv = _STATE["mode_pfv"]
    if mode not in mode_pfv or family not in family_pfv:
        return False, f"family {family!r} not in frame vocabulary — not governed"

    # Frame the proposition to (family, mode) on a PER-CALL COPY (C2);
    # rebuild the entity per call. The shared _STATE is never mutated.
    try:
        state = copy.deepcopy(_STATE)
    except Exception:  # noqa: BLE001 — fail closed, never raise out of a gate
        return False, "evaluation context clone failed"
    interp = state["interp"]
    cir_prop = state["cir_prop"]
    cir_prop.frame_values = [family_pfv[family], mode_pfv[mode]]
    entity = Entity(
        id=_CIR_ENTITY_ID,
        concept_id=_CIR_CONCEPT_ID,
        attributes={
            "enforcement_rule_family": family,
            "cir_sdm_mode": mode,
            "authorized_by": authorized or "",
        },
        external_id=v.get("violation_id", ""),
    )
    interp.entities[_CIR_ENTITY_ID] = entity

    context = {"enforcement_rule_family": family, "cir_sdm_mode": mode}
    try:
        disposition, all_passed, context_status = interp.evaluate_proposition(
            cir_prop, context=context,
        )
    except Exception:
        return False, "SOL evaluation failed"

    if context_status == "context_mismatch":
        return False, f"context_mismatch (family={family}, mode={mode})"
    if context_status == "context_required":
        return False, "context_required"

    if all_passed:
        return True, f"governed (family={family} enforced, authorized={authorized})"
    return False, f"not governed (family={family} mode={mode})"


def evaluate_ccnf_version_lock(
    event: Dict[str, Any],
    posture_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> tuple[bool, str]:
    """#6 — is a well-formed event's CCNF version governed (version-lock)?"""

    outcome = _evaluate_ccnf_version_lock(event, posture_rows)
    _record_admission(
        gate="cir_sdm_gate.evaluate_ccnf_version_lock",
        entity_id=str(event.get("event_id", "")),
        admitted=outcome[0],
        reason=outcome[1],
        payload={"event": event},
    )
    return outcome


def _evaluate_ccnf_version_lock(
    event: Dict[str, Any],
    posture_rows: Optional[Iterable[Dict[str, Any]]],
) -> tuple[bool, str]:
    """Internal evaluation core (wrapped by evaluate_ccnf_version_lock)."""
    if _STATE is None:
        _build()

    from SOLScript.solscript import Entity  # type: ignore[import-untyped]

    assert _STATE is not None

    # R-D R4: genuinely malformed input (no version to build a proposition
    # over) is a contract/programming error — raw exception remains correct.
    version = event.get("ccnf_version")
    if version is None:
        raise ValueError(
            "evaluate_ccnf_version_lock: event has no ccnf_version — "
            "genuinely malformed envelope (contract error, not a governance "
            "outcome)"
        )
    try:
        version_int = int(version)
    except (TypeError, ValueError):
        raise ValueError(
            f"evaluate_ccnf_version_lock: non-integer ccnf_version {version!r} "
            f"— genuinely malformed envelope"
        )

    # version-lock family posture (mode, authorized_by)
    mode, authorized = _family_posture("cir-sdm.version-lock", posture_rows)

    # Frame on the event's version (typed_scalar integer) on a PER-CALL
    # COPY (C2); build the entity on the copy. The shared _STATE is never
    # mutated.
    try:
        state = copy.deepcopy(_STATE)
    except Exception:  # noqa: BLE001 — fail closed, never raise out of a gate
        return False, "evaluation context clone failed"
    interp = state["interp"]
    ccnf_prop = state["ccnf_prop"]
    ccnf_prop.frame_values = [_version_pfv(version_int)]
    entity = Entity(
        id=_CCNF_ENTITY_ID,
        concept_id=_CCNF_CONCEPT_ID,
        attributes={
            "ccnf_version": str(version_int),
            "version_lock_authorized": authorized or "",
        },
        external_id=str(event.get("event_id", "")),
    )
    interp.entities[_CCNF_ENTITY_ID] = entity

    context = {"ccnf_version": version_int}
    try:
        disposition, all_passed, context_status = interp.evaluate_proposition(
            ccnf_prop, context=context,
        )
    except Exception:
        return False, "SOL evaluation failed"

    if context_status == "context_mismatch":
        return False, f"context_mismatch (ccnf_version={version_int})"
    if context_status == "context_required":
        return False, "context_required"

    if all_passed:
        return True, (
            f"version governed (version-lock enforced, authorized={authorized})"
        )
    return False, f"version not governed (version-lock mode={mode})"


def _version_pfv(version: int) -> Any:
    """Return the (cached) PropositionFrameValue for a ccnf_version scalar.

    The cache is read-after-fill and the PFV objects are only ever handed
    to per-call evaluation copies by reference (read-only), so filling it
    under concurrency is benign: worst case two threads build equivalent
    PFVs and one wins the slot.
    """
    from SOLScript.solscript import PropositionFrameValue  # type: ignore[import-untyped]
    cache = _STATE["version_pfv_cache"] if _STATE else None
    if cache is not None and version in cache:
        return cache[version]
    pfv = PropositionFrameValue(
        id=f"ccnf-gate:version-governed:frame:v{version}",
        proposition_id=_CCNF_PROP_ID,
        dimension_id=_VERSION_DIM_ID,
        reference_value_id=None,
        scalar_value=str(version),
    )
    if cache is not None:
        cache[version] = pfv
    return pfv


def _record_admission(
    *,
    gate: str,
    entity_id: str,
    admitted: bool,
    reason: str,
    payload: Dict[str, Any],
) -> None:
    """Advisory record-then-act (PEB-forward Phase 1).

    Best-effort: a recording failure never flips the gate outcome and never
    raises (see cascade.peb_admission.record_gate_outcome)."""
    try:
        from cascade.peb_admission import record_gate_outcome
    except Exception:  # noqa: BLE001 — advisory path
        return
    try:
        record_gate_outcome(
            gate=gate,
            entity_id=entity_id,
            admitted=admitted,
            reason=reason,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 — advisory path must never raise
        pass