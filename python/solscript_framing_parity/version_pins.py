"""Version binding for the framing parity guard.

The guard is only meaningful for the exact runtime revisions and migration
versions it was proven against. This module records those bindings as SHA-256
hashes in ``version_pins.json`` and fails loudly when any pinned file drifts.

Pinned files fall into two groups:

* the three runtime sources that implement the framing gate, and
* the SQL migration chain (v32 creates the gate; v33-v37 were verified not to
  redefine it). Editing any of them invalidates the proof and forces a human to
  re-run the guard and refresh the pins deliberately.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from .sql_runtime import GATE_MIGRATION, extract_gate_sql

PINS_PATH = Path(__file__).with_name("version_pins.json")

#: Files whose content proves the gate. ``role`` is reported with any drift.
PINNED_FILES: Mapping[str, str] = {
    "python/SOLScript/solscript/interpreter.py": "python framing gate (E8.4 type-level)",
    "python/SOLScript/solscript/models.py": "python frame/proposition model",
    "typescript/solscript/src/interpreter.ts": "typescript framing gate (instance frame count)",
    "typescript/solscript/src/models.ts": "typescript frame/proposition model",
    "typescript/solscript/src/expression-compiler.ts": "typescript expression compiler",
    "typescript/solscript/src/events.ts": "typescript events/digest surface",
    "schemas/migrations/resolution/resolution_migration_v32.sql": "SQL gate definition (v32)",
    "schemas/migrations/resolution/resolution_migration_v33_sweep_overload_defaults.sql": "later migration (v33) — verified non-altering",
    "schemas/migrations/resolution/resolution_migration_v34.sql": "later migration (v34) — verified non-altering",
    "schemas/migrations/resolution/resolution_migration_v35_frame_semantics.sql": "later migration (v35) — verified non-altering",
    "schemas/migrations/resolution/resolution_migration_v36_shrapnel_bridge.sql": "later migration (v36) — verified non-altering",
    "schemas/migrations/resolution/resolution_migration_v37_shrapnel_field_metadata_sync.sql": "later migration (v37) — verified non-altering",
}


@dataclass(frozen=True)
class PinDrift:
    """One pinned value that no longer matches the working tree."""

    path: str
    role: str
    expected: Optional[str]
    actual: Optional[str]

    def render(self) -> str:
        expected = self.expected or "<absent>"
        actual = self.actual or "<absent>"
        return (
            f"  {self.path}\n"
            f"    role:     {self.role}\n"
            f"    pinned:   {expected}\n"
            f"    observed: {actual}"
        )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_pins(path: Path = PINS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def current_pin_values(repo_root: Path) -> dict:
    """Recompute the pin payload from the working tree."""
    pinned_files: Dict[str, str] = {}
    for relative in PINNED_FILES:
        target = repo_root / relative
        pinned_files[relative] = sha256_file(target) if target.is_file() else ""

    gate_path = repo_root / GATE_MIGRATION
    gate_sha = ""
    if gate_path.is_file():
        gate_sha = sha256_bytes(
            extract_gate_sql(gate_path.read_text(encoding="utf-8")).encode("utf-8")
        )

    return {
        "pinned_files": pinned_files,
        "sql_gate_sha256": gate_sha,
    }


def verify_pins(repo_root: Path, pins_path: Path = PINS_PATH) -> List[PinDrift]:
    """Return every drift between the working tree and the pinned revisions."""
    pins = load_pins(pins_path)
    pinned = pins.get("pinned_files", {})
    drifts: List[PinDrift] = []

    for relative, role in PINNED_FILES.items():
        expected = pinned.get(relative)
        target = repo_root / relative
        actual = sha256_file(target) if target.is_file() else None
        if expected != actual:
            drifts.append(
                PinDrift(path=relative, role=role, expected=expected, actual=actual)
            )

    expected_gate = pins.get("sql_gate_sha256")
    gate_path = repo_root / GATE_MIGRATION
    if not gate_path.is_file():
        drifts.append(
            PinDrift(
                path=GATE_MIGRATION,
                role="SQL gate definition (v32)",
                expected=expected_gate,
                actual=None,
            )
        )
    else:
        actual_gate = sha256_bytes(
            extract_gate_sql(gate_path.read_text(encoding="utf-8")).encode("utf-8")
        )
        if expected_gate != actual_gate:
            drifts.append(
                PinDrift(
                    path=f"{GATE_MIGRATION}#evaluate_proposition",
                    role="SQL gate function body",
                    expected=expected_gate,
                    actual=actual_gate,
                )
            )

    return drifts


def write_pins(repo_root: Path, pins_path: Path = PINS_PATH) -> dict:
    """Refresh only the hash payload, preserving the descriptive metadata."""
    pins = load_pins(pins_path)
    pins.update(current_pin_values(repo_root))
    pins_path.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n")
    return pins


def pin_summary(pins: Mapping) -> str:
    """Human-readable statement of what the proof is bound to."""
    runtimes = pins.get("runtimes", {})
    lines = [
        "Version bindings:",
        f"  python      {runtimes.get('python', {}).get('label', 'unknown')}",
        f"  typescript  {runtimes.get('typescript', {}).get('label', 'unknown')}",
        f"  sql         {runtimes.get('sql', {}).get('label', 'unknown')}",
    ]
    later = runtimes.get("sql", {}).get("later_migrations_verified_through", [])
    if later:
        lines.append(f"  sql chain   v32 + {', '.join(later)} verified non-altering")
    return "\n".join(lines)
