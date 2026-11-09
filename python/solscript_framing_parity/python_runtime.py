"""Python side of the parity guard — the E8.4 type-level framing gate.

Runs the shared matrix against the in-memory reference interpreter
(``python/SOLScript/solscript/interpreter.py``). No database, no filesystem
writes, no network: strictly in memory, one fresh interpreter per case.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

from .canonical import Outcome, classify_error, outcome_from_python
from .matrix import DIMENSIONS, FIXTURE, MATRIX, Case

RUNTIME = "python"

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_solscript(repo_root: Path):
    """Import the reference interpreter without pulling the bridge modules.

    ``solscript/__init__.py`` eagerly imports the database loader and bridge
    (unnecessary here, and it drags in asyncpg). Import the two modules that
    actually own the gate and the models instead.
    """
    solscript_dir = str(repo_root / "python" / "SOLScript")
    if solscript_dir not in sys.path:
        sys.path.insert(0, solscript_dir)

    from solscript.interpreter import ResolutionInterpreter  # noqa: PLC0415
    from solscript.models import (  # noqa: PLC0415
        Disposition,
        Entity,
        FrameDimension,
        FrameDimensionValue,
        Proposition,
        PropositionFrameValue,
    )

    return {
        "ResolutionInterpreter": ResolutionInterpreter,
        "Disposition": Disposition,
        "Entity": Entity,
        "FrameDimension": FrameDimension,
        "FrameDimensionValue": FrameDimensionValue,
        "Proposition": Proposition,
        "PropositionFrameValue": PropositionFrameValue,
    }


def run_case(case: Case, repo_root: Optional[Path] = None) -> Outcome:
    root = Path(repo_root) if repo_root is not None else DEFAULT_REPO_ROOT
    api = _load_solscript(root)

    interp = api["ResolutionInterpreter"]()
    interp.add_entity(
        api["Entity"](
            id=FIXTURE["entity_id"],
            concept_id=FIXTURE["asset_concept_id"],
            attributes={},
        )
    )

    for name, spec in DIMENSIONS.items():
        interp.add_frame_dimension(
            api["FrameDimension"](
                id=spec["id"],
                name=name,
                description=None,
                value_kind=spec["value_kind"],
                scalar_type=spec["scalar_type"],
            )
        )
        for value_name, value_id in spec["values"].items():
            interp.add_frame_dimension_value(
                api["FrameDimensionValue"](
                    id=value_id,
                    dimension_id=spec["id"],
                    value=value_name,
                )
            )

    prop = api["Proposition"](
        id=FIXTURE["proposition_id"],
        title=case.id,
        description=None,
        asset_concept_id=FIXTURE["asset_concept_id"],
        subject_entity_id=FIXTURE["entity_id"],
        disposition=api["Disposition"].PENDING,
        semantic_type_id=(
            FIXTURE["semantic_type_id"] if case.required_dimensions else None
        ),
    )
    interp.add_proposition(prop)

    for dimension_name in case.required_dimensions:
        interp.register_semantic_type_required_dimension(
            FIXTURE["semantic_type_id"], DIMENSIONS[dimension_name]["id"]
        )

    for index, frame in enumerate(case.instance_frames):
        spec = DIMENSIONS[frame.dimension]
        interp.add_proposition_frame_value(
            api["PropositionFrameValue"](
                id=f"{case.id}:pfv:{index}",
                proposition_id=prop.id,
                dimension_id=spec["id"],
                reference_value_id=spec["values"][frame.reference_value],
            )
        )

    try:
        if case.context is None:
            result = interp.evaluate_proposition(prop)
        else:
            result = interp.evaluate_proposition(prop, context=dict(case.context))
    except Exception as exc:  # noqa: BLE001 - the error class is the compared value
        return Outcome(error=classify_error(exc), detail=f"{type(exc).__name__}: {exc}")

    return outcome_from_python(result)


def run_matrix(cases=MATRIX, repo_root: Optional[Path] = None) -> Dict[str, Outcome]:
    """Run every case and return ``{case_id: Outcome}``."""
    return {case.id: run_case(case, repo_root) for case in cases}
