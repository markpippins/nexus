"""TypeScript side of the parity guard — the instance frame-count gate.

Compiles ``typescript/solscript/src`` from source into a throwaway directory
(never touching the repo, never trusting the committed ``dist/``) and executes
the shared matrix against the resulting in-memory interpreter via
``typescript/solscript/test/framing-parity-driver.mjs``.

Requirements: ``node`` and ``tsc`` on ``PATH``. Nothing is installed and
nothing is written inside the repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .canonical import Outcome, RuntimeUnavailable, classify_error
from .matrix import MATRIX, matrix_payload

RUNTIME = "typescript"

_COMPILE_TIMEOUT_S = 180
_RUN_TIMEOUT_S = 120


class TypeScriptToolingUnavailable(RuntimeUnavailable):
    """``node``/``tsc``/driver missing — the guard cannot attest the TS side."""


class TypeScriptRuntimeError(RuntimeError):
    """The TS harness failed in a way that is not a framing-gate outcome."""


@dataclass(frozen=True)
class CompiledBundle:
    """Where the freshly compiled interpreter lives, and which sources it used."""

    interpreter_module: Path
    sources: tuple
    command: tuple


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise TypeScriptToolingUnavailable(
            f"`{tool}` not found on PATH; the TypeScript side of the framing "
            "parity guard cannot run"
        )
    return path


def compile_from_source(repo_root: Path, workdir: Path) -> CompiledBundle:
    """Copy the TypeScript sources into ``workdir`` and compile the interpreter."""
    tsc = _require("tsc")

    src_dir = workdir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    repo_src = repo_root / "typescript" / "solscript" / "src"
    if not repo_src.is_dir():
        raise TypeScriptRuntimeError(f"missing TypeScript source directory: {repo_src}")

    sources = []
    for source in sorted(repo_src.glob("*.ts")):
        shutil.copy2(source, src_dir / source.name)
        sources.append(source)

    out_dir = workdir / "out"
    command = (
        tsc,
        "--target",
        "ES2022",
        "--module",
        "ESNext",
        "--moduleResolution",
        "node",
        "--skipLibCheck",
        "--outDir",
        str(out_dir),
        str(src_dir / "interpreter.ts"),
    )
    completed = subprocess.run(
        command,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=_COMPILE_TIMEOUT_S,
    )
    if completed.returncode != 0:
        raise TypeScriptRuntimeError(
            "tsc failed to compile the solscript interpreter:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )

    interpreter_module = out_dir / "interpreter.js"
    if not interpreter_module.is_file():
        raise TypeScriptRuntimeError(
            f"tsc produced no interpreter module at {interpreter_module}"
        )

    return CompiledBundle(
        interpreter_module=interpreter_module,
        sources=tuple(str(path) for path in sources),
        command=command,
    )


def run_matrix(
    repo_root: Path,
    keep_temp: bool = False,
    cases=MATRIX,
) -> Dict[str, Outcome]:
    """Compile the TS interpreter in a temp dir and run the shared matrix."""
    node = _require("node")

    driver = repo_root / "typescript" / "solscript" / "test" / "framing-parity-driver.mjs"
    if not driver.is_file():
        raise TypeScriptRuntimeError(f"missing parity driver: {driver}")

    workdir = Path(tempfile.mkdtemp(prefix="solscript-framing-parity-ts-"))
    try:
        bundle = compile_from_source(repo_root, workdir)

        matrix_path = workdir / "matrix.json"
        payload = matrix_payload()
        payload["cases"] = [
            case.to_payload() for case in cases
        ]
        matrix_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

        completed = subprocess.run(
            (node, str(driver), str(matrix_path), bundle.interpreter_module.as_uri()),
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT_S,
        )
        if completed.returncode != 0:
            raise TypeScriptRuntimeError(
                "TypeScript framing driver failed:\n"
                f"{completed.stdout}\n{completed.stderr}"
            )

        try:
            decoded = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise TypeScriptRuntimeError(
                f"TypeScript framing driver produced non-JSON output: {completed.stdout!r}"
            ) from exc

        raw_results = decoded.get("results", {})
        outcomes: Dict[str, Outcome] = {}
        for case in cases:
            raw = raw_results.get(case.id)
            if raw is None:
                raise TypeScriptRuntimeError(
                    f"TypeScript framing driver returned no result for case {case.id!r}"
                )
            if raw.get("kind") == "error":
                outcomes[case.id] = Outcome(
                    error=classify_error(raw.get("message")),
                    detail=str(raw.get("message")),
                )
            else:
                outcomes[case.id] = Outcome(
                    disposition=raw.get("disposition"),
                    all_passed=bool(raw.get("all_passed")),
                    context_status=raw.get("context_status"),
                )
        return outcomes
    finally:
        if not keep_temp:
            shutil.rmtree(workdir, ignore_errors=True)


def runtime_fingerprint(repo_root: Path) -> Optional[dict]:
    """Best-effort versions of the tools the TS side was proven with."""
    info: Dict[str, Optional[str]] = {"node": None, "tsc": None}
    for tool in ("node", "tsc"):
        path = shutil.which(tool)
        if not path:
            continue
        try:
            completed = subprocess.run(
                (path, "--version"), capture_output=True, text=True, timeout=30
            )
            info[tool] = (completed.stdout or completed.stderr).strip() or None
        except Exception:  # pragma: no cover - version probe is advisory
            info[tool] = None
    if info["node"] is None and info["tsc"] is None:
        return None
    return info
