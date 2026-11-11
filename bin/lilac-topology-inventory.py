#!/usr/bin/env python3
"""Lilac Wave 1 executable-topology inventory (plan 8261667; parent 8261639 AC2/AC4/AC5).

Produces `docs/lilac/executable-topology.json`: every producer and consumer of
the canonical ticket / receipt / execution-claim / fan-out surfaces, with the
runtime facts an operator needs (service unit, port, pid, invocation id,
language, database target, route) and a lifecycle class from
`{live, test/worktree, retired, compatibility, unknown}`.

Two sources, deliberately separated:

  * **Code topology** — derived by scanning the working tree for the SQL that
    mutates or reads each canonical surface, plus the schema each statement
    actually targets (`resolution` vs the legacy `vision`/`execution`/`wind`/
    `kernel` schemas). This is the part that drifts with the repository, so it
    is the drift contract.
  * **Runtime observations** — probed from the host (`ss`, `/proc`, systemd)
    and recorded for the operator. Pids and invocation ids change on every
    restart, so they are recorded but never compared by `--check`.

Usage:
    bin/lilac-topology-inventory.py            # regenerate the committed JSON
    bin/lilac-topology-inventory.py --check    # exit 1 if the committed JSON is stale
    bin/lilac-topology-inventory.py --print    # emit JSON to stdout

`--check` returns 0 when current, 1 on drift (with a path-level diff), and 2 on
a malformed/absent artifact. It never writes.

Read-only by construction: this tool issues SELECT-free filesystem reads, two
`ss`/`systemctl` probes, and one write to its own output path when regenerating.
It changes no service, schema, or row.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "lilac" / "executable-topology.json"
DEFAULT_MD = REPO_ROOT / "docs" / "lilac" / "executable-topology.md"

PLAN = "8261667"
PARENT_PLAN = "8261639"
GENERATOR = "bin/lilac-topology-inventory.py"

CANONICAL_SCHEMA = "resolution"
LEGACY_SCHEMAS = ("vision", "execution", "wind", "kernel")

# The one module allowed to hold the canonical implementation (parent AC1).
CANONICAL_OWNER = "python/conduit/lilac.py"

SURFACES = ("receipt", "ticket", "ticket_transition", "fanout_transition")
SURFACE_LABELS = {
    "receipt": "receipt",
    "ticket": "ticket",
    "ticket_transition": "ticket_transition",
    "fanout_transition": "fan-out",
}

# Tables in either schema that address the same canonical surface. The legacy
# stores are pluralised; canonical tables are not.
TABLE_SURFACE = {
    "receipt": "receipt",
    "receipts": "receipt",
    "ticket": "ticket",
    "tickets": "ticket",
    "ticket_transition": "ticket_transition",
    "ticket_transitions": "ticket_transition",
    "fanout_transition": "fanout_transition",
    "fanout_transitions": "fanout_transition",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "dist", "build", "coverage",
    ".venv", "venv", "site-packages", ".next", ".pytest_cache", ".mypy_cache",
}
SCAN_DIRS = ("python", "typescript", "bin")
SUFFIX_LANGUAGE = {".py": "python", ".ts": "typescript", ".js": "typescript"}

INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+"
    r"(?P<tok>%SCHEMA%|\$\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*\.\s*(?P<tbl>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
READ_RE = re.compile(
    r"(?:FROM|JOIN)\s+"
    r"(?P<tok>%SCHEMA%|\$\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*\.\s*(?P<tbl>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
PY_DEFAULT_SCHEMA_RE = re.compile(r"schema\s*:\s*str\s*=\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")
TS_CONST_RE = re.compile(r"const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"'](?P<value>[A-Za-z_][A-Za-z0-9_]*)[\"']")

# Every lifecycle class, and the ones that need an explicit disposition.
LIFECYCLES = ("live", "test/worktree", "retired", "compatibility", "unknown")

# Operators may deliberately leave a writer unclassified when the runtime owner
# is genuinely undetermined; the reason is then mandatory (parent AC2).
DISPOSITIONS: Dict[str, str] = {}

# Surfaces that were authority once and are not any more (parent AC5). Each is
# recorded as historical WITH its evidence, so nothing downstream can read a
# retired unit as live authority.
RETIRED_SURFACES: List[Dict[str, str]] = [
    {
        "token": "Temporal",
        "authority": "historical",
        "evidence": "retired target; no live producer or consumer may claim it "
                    "(plan 8261639 AC5; 8261639 AC11 keeps PEB/Wind separate)",
    },
    {
        "token": "vision-srv",
        "authority": "historical",
        "evidence": "retired by PR #414 (6082a4aef) and collapsed into losm-host "
                    "by plan 8261646; remaining vision.* tables are legacy stores, "
                    "read through dual-read projections only",
    },
    {
        "token": "kernel dual-write",
        "authority": "historical",
        "evidence": "superseded by the single canonical resolution.receipt stream; "
                    "any residual dual-write is a compatibility seam, not authority",
    },
    {
        "token": "legacy derived receipt tables",
        "authority": "historical",
        "evidence": "nebula.receipts_unified and siblings are C5 dual-read "
                    "projections over resolution.receipt (ruling 8d30e540), not a "
                    "second write authority",
    },
]

OVERLAP_CLASSES = (
    "intentional-channel",
    "semantic-duplicate",
    "stale-or-deprecated",
    "test-only",
)
OVERLAP_DISPOSITIONS = ("redirect", "adapter", "retirement")
OVERLAP_CLASSIFICATIONS = (
    "intentional-channel",
    "semantic-duplicate",
    "stale-or-deprecated",
    "test-only",
)

# Declared classification for a surface written from BOTH languages. Keyed by
# surface; anything missing is reported as `unknown` and fails the guard, so a
# new overlap cannot slip in unclassified (parent AC3).
OVERLAP_CLASSIFICATION: Dict[str, Dict[str, str]] = {
    "receipt": {
        "classification": "stale-or-deprecated",
        "disposition": "redirect",
        "replaces": "python/conduit/lilac.py",
        "reason": "TypeScript writers still target the legacy vision.receipts "
                  "store; the one canonical stream is resolution.receipt, written "
                  "by the Lilac adapter. Redirect is carried by plan 8261669 (L3).",
    },
    "ticket": {
        "classification": "stale-or-deprecated",
        "disposition": "redirect",
        "replaces": "python/conduit/lilac.py",
        "reason": "TypeScript writers still target legacy vision/wind ticket "
                  "stores; canonical ticket persistence is the Lilac adapter. "
                  "Redirect is carried by plan 8261669 (L3).",
    },
}

CANONICAL_OWNERSHIP_STATEMENT = (
    "Lilac owns ticket, receipt, execution-claim and fan-out behavior. The one "
    "canonical implementation lives in python/conduit/lilac.py; every other "
    "module that mutates those surfaces is either a declared compatibility "
    "seam, a legacy writer pending redirect, or a violation recorded below."
)


# ---------------------------------------------------------------------------
# source scanning
# ---------------------------------------------------------------------------


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
    )


def _iter_sources(root: Path) -> Iterable[Tuple[str, str]]:
    for top in SCAN_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fname in sorted(filenames):
                suffix = Path(fname).suffix
                if suffix not in SUFFIX_LANGUAGE:
                    continue
                path = Path(dirpath) / fname
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                yield str(path.relative_to(root)), text


def _resolve_token(tok: str, text: str) -> str:
    """Resolve `%SCHEMA%` / `${VAR}` / literal token to a concrete schema name."""
    if tok == "%SCHEMA%":
        match = PY_DEFAULT_SCHEMA_RE.search(text)
        return match.group(1) if match else "unknown"
    if tok.startswith("${"):
        name = tok[2:-1]
        for const in TS_CONST_RE.finditer(text):
            if const.group("name") == name:
                return const.group("value")
        return "unknown"
    return tok.lower()


IGNORE_FILE_MARKER = "topology-inventory: ignore-file"


def _scan_statement(
    rel: str, text: str, pattern: re.Pattern[str]
) -> List[Dict[str, Any]]:
    language = SUFFIX_LANGUAGE[Path(rel).suffix]
    found: List[Dict[str, Any]] = []
    if IGNORE_FILE_MARKER in text[:4000]:
        # Fixtures and generated tables legitimately contain the SQL this
        # scanner looks for; such a file opts out explicitly rather than
        # inflating the inventory with code that never runs against a surface.
        return found
    for match in pattern.finditer(text):
        table = match.group("tbl").lower()
        surface = TABLE_SURFACE.get(table)
        if surface is None:
            continue
        schema = _resolve_token(match.group("tok"), text)
        found.append(
            {
                "surface": surface,
                "path": rel,
                "line": text.count("\n", 0, match.start()) + 1,
                "language": language,
                "target_schema": schema,
                "table": table,
            }
        )
    return found


def scan_code(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Return every canonical-surface write and read found in the tree."""
    writers: List[Dict[str, Any]] = []
    readers: List[Dict[str, Any]] = []
    # `unknown` is kept, not dropped: a writer whose schema cannot be resolved is
    # exactly the case that must surface and demand a disposition (parent AC2).
    accepted = (CANONICAL_SCHEMA, "unknown") + LEGACY_SCHEMAS

    def _role(schema: str) -> str:
        if schema == CANONICAL_SCHEMA:
            return "canonical"
        if schema in LEGACY_SCHEMAS:
            return "legacy"
        return "undetermined"

    for rel, text in _iter_sources(root):
        for sql in _scan_statement(rel, text, INSERT_RE):
            if sql["target_schema"] in accepted:
                sql["role"] = _role(sql["target_schema"])
                sql["test_path"] = _is_test_path(rel)
                writers.append(sql)
        for sql in _scan_statement(rel, text, READ_RE):
            if sql["target_schema"] in accepted:
                sql["role"] = _role(sql["target_schema"])
                sql["test_path"] = _is_test_path(rel)
                readers.append(sql)
    # Deterministic order: surface, then path, then line.
    key = lambda d: (d["surface"], d["path"], d["line"])
    return {"writers": sorted(writers, key=key), "readers": sorted(readers, key=key)}


# ---------------------------------------------------------------------------
# runtime probe
# ---------------------------------------------------------------------------


LiveProbe = Callable[[], Dict[str, Any]]


def _run(cmd: List[str], timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _unit_from_cgroup(pid: int) -> Optional[str]:
    try:
        cgroup = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    for line in reversed(cgroup.splitlines()):
        tail = line.rsplit("/", 1)[-1].strip()
        if tail.endswith(".service"):
            return tail
    return None


def _language_from_cmdline(pid: int) -> Optional[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace").lower()
    if "python" in cmd:
        return "python"
    if "node" in cmd:
        return "typescript"
    return None


def _cwd_of(pid: int) -> Optional[str]:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _is_project_tree(path: str) -> bool:
    """True when `path` belongs to this project family (a nexus checkout or worktree)."""
    parts = Path(path).parts
    return "nexus" in parts or "nexus-worktrees" in parts


def probe_live() -> Dict[str, Any]:
    """Probe listening sockets owned by nexus checkout/worktree processes.

    Returns `{"available": bool, "services": [...]}`. Unavailable is a normal
    state (CI, containers without `ss`), not an error: the caller records it and
    `--check` then compares code topology only.

    Services are discovered by process cwd, not by a hardcoded unit list, so
    anything running out of another checkout or worktree is reported with its
    own `working_tree` instead of being silently omitted.
    """
    listing = _run(["ss", "-ltnp"])
    if not listing.strip():
        return {"available": False, "services": []}

    by_port: Dict[int, int] = {}
    for line in listing.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        port_field = fields[3]
        port_match = re.search(r":(\d+)$", port_field)
        pid_match = re.search(r"pid=(\d+)", line)
        if not (port_match and pid_match):
            continue
        port, pid = int(port_match.group(1)), int(pid_match.group(1))
        if port not in by_port or pid < by_port[port]:
            by_port[port] = pid

    services: List[Dict[str, Any]] = []
    for port in sorted(by_port):
        pid = by_port[port]
        cwd = _cwd_of(pid)
        if not cwd or not _is_project_tree(cwd):
            continue
        root = cwd
        unit = _unit_from_cgroup(pid)
        invocation = ""
        if unit:
            invocation = _run(["systemctl", "show", unit, "-p", "InvocationID", "--value"]).strip()
        services.append(
            {
                "unit": unit,
                "port": port,
                "pid": pid,
                "invocation_id": invocation or None,
                "language": _language_from_cmdline(pid),
                "working_tree": root,
                "route": f"tcp://127.0.0.1:{port}",
                "route_note": "listening socket this unit serves",
                "lifecycle": "live",
            }
        )
    return {"available": True, "services": services}


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


def _lifecycle(rel: str) -> str:
    """Classify the CODE's shipping status.

    `live` means the module ships in the tree being inventoried (the deployed
    tree for this artifact); it does NOT claim the module executed a write —
    execution facts live in `services[]`. `test/worktree` is a test or harness
    path. `compatibility` is a declared seam. Anything else is `unknown` and
    needs an explicit disposition (parent AC2).
    """
    if _is_test_path(rel):
        return "test/worktree"
    if rel in DISPOSITIONS:
        return "compatibility"
    return "live"


def build_inventory(
    root: Path,
    live: Optional[Dict[str, Any]] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the inventory document. Pure apart from the injected probe."""
    scan = scan_code(root)
    live = live or {"available": False, "services": []}

    def decorate(entry: Dict[str, Any]) -> Dict[str, Any]:
        rel = entry["path"]
        out = dict(entry)
        lifecycle = _lifecycle(rel)
        # A writer whose target schema could not be resolved is genuinely
        # undetermined: it must be dispositioned rather than optimistically
        # assumed live (parent AC2).
        if lifecycle == "live" and entry.get("target_schema") == "unknown":
            lifecycle = "unknown"
        out["lifecycle"] = lifecycle
        out["disposition"] = DISPOSITIONS.get(rel)
        # Portable by design: the code entry belongs to the tree being
        # inventoried, and an absolute path here would make the committed
        # artifact stale on every other checkout (CI, another worktree).
        # Absolute working trees are recorded for live services, which are
        # host observations.
        out["working_tree"] = "self"
        # A library module is reached in-process, not over the network: `route`
        # is recorded as null WITH a reason rather than omitted, so a reader can
        # tell "no route" apart from "route not probed".
        out["route"] = None
        out["route_note"] = "library module — invoked in-process, no network route"
        return out

    writers = [decorate(w) for w in scan["writers"]]
    readers = [decorate(r) for r in scan["readers"]]

    # Parent AC4: any canonical mutation outside the one canonical owner is a
    # violation, recorded with file:line rather than silently tolerated.
    violations = [
        {
            "path": w["path"],
            "line": w["line"],
            "surface": w["surface"],
            "table": w["table"],
            "reason": "canonical write outside the declared canonical owner "
                      f"({CANONICAL_OWNER}); classify it as a compatibility seam "
                      "or redirect it to the owner",
        }
        for w in writers
        if w["role"] == "canonical"
        and w["path"] != CANONICAL_OWNER
        and not w["test_path"]
    ]

    # Parent AC2: coverage is fail-closed per surface. Only production writers
    # count — a test that INSERTs into a surface proves nothing about coverage.
    coverage: Dict[str, Dict[str, Any]] = {}
    for surface in SURFACES:
        production = [
            w for w in writers if w["surface"] == surface and not w["test_path"]
        ]
        undeclared = [
            w for w in production
            if w["lifecycle"] == "unknown" and not w["disposition"]
        ]
        coverage[surface] = {
            "production_writers": len(production),
            "canonical_writers": len([w for w in production if w["role"] == "canonical"]),
            "undeclared_unknown_writers": len(undeclared),
            "ok": bool(production) and not undeclared,
        }

    # Parent AC3: every Python/TypeScript overlap classified and disposed. One
    # record per surface written from both languages (the unit a reviewer must
    # decide on), not a path cross-product that buries the decision.
    overlaps: List[Dict[str, Any]] = []
    for surface in SURFACES:
        production = [
            w for w in writers if w["surface"] == surface and not w["test_path"]
        ]
        python_paths = sorted({w["path"] for w in production if w["language"] == "python"})
        ts_paths = sorted({w["path"] for w in production if w["language"] == "typescript"})
        if not (python_paths and ts_paths):
            continue
        declared = OVERLAP_CLASSIFICATION.get(surface, {})
        overlaps.append(
            {
                "surface": surface,
                "python_writers": python_paths,
                "typescript_writers": ts_paths,
                "classification": declared.get("classification", "unknown"),
                "disposition": declared.get("disposition", "unknown"),
                "replaces": declared.get("replaces"),
                "reason": declared.get("reason", "undeclared overlap — classify it"),
            }
        )

    surfaces = {
        surface: {
            "label": SURFACE_LABELS[surface],
            "writers": [w for w in writers if w["surface"] == surface],
            "readers": [r for r in readers if r["surface"] == surface],
        }
        for surface in SURFACES
    }

    return {
        "_meta": {
            "generator": GENERATOR,
            "plan": PLAN,
            "parent_plan": PARENT_PLAN,
            "canonical_schema": CANONICAL_SCHEMA,
            "canonical_owner": CANONICAL_OWNER,
            "generated_at": generated_at,
            "runtime_probe": "available" if live.get("available") else "unavailable",
            "drift_contract": (
                "`--check` compares the code-derived topology (surfaces, writers, "
                "readers, violations, overlaps, retired surfaces, coverage) and, "
                "only when the runtime probe is available, the service unit/port "
                "set. Per-service pid and invocation_id are observations and are "
                "never compared."
            ),
        },
        "canonical_ownership": {
            "statement": CANONICAL_OWNERSHIP_STATEMENT,
            "owner": CANONICAL_OWNER,
        },
        "surfaces": surfaces,
        "coverage": coverage,
        "violations": violations,
        "overlaps": overlaps,
        "retired_surfaces": copy.deepcopy(RETIRED_SURFACES),
        "services": live.get("services", []),
    }


# ---------------------------------------------------------------------------
# drift comparison
# ---------------------------------------------------------------------------

VOLATILE_KEYS = ("generated_at", "runtime_probe")


def stable_projection(
    inventory: Dict[str, Any], include_services: bool = True
) -> Dict[str, Any]:
    """Projection used for drift comparison: no timestamps, no per-process facts.

    `include_services=False` is used when the fresh scan could not probe the
    runtime (CI, a container without `ss`). Comparing a live-host artifact's
    service list against an empty probe would report drift for a fact the scan
    never observed, so the runtime block is then excluded on both sides.
    """
    projected = json.loads(json.dumps(inventory))
    for key in VOLATILE_KEYS:
        projected["_meta"].pop(key, None)
    services = projected.get("services") or []
    if not services or not include_services:
        projected["services"] = []
    else:
        # Compare the served topology (which unit on which port), not where the
        # checkout happens to live or which pid served it.
        projected["services"] = sorted(
            ({"unit": s.get("unit"), "port": s.get("port")} for s in services),
            key=lambda s: (s["unit"] or "", s["port"] or 0),
        )
    return projected


def diff_projection(committed: Dict[str, Any], fresh: Dict[str, Any]) -> List[str]:
    """Human-readable list of paths whose committed value differs from fresh."""
    problems: List[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            problems.append(path)
            return
        if isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    problems.append(f"{path}.{key}")
                else:
                    walk(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, list):
            if len(a) != len(b):
                problems.append(f"{path}[length {len(a)} != {len(b)}]")
                return
            for index, (item_a, item_b) in enumerate(zip(a, b)):
                walk(item_a, item_b, f"{path}[{index}]")
        elif a != b:
            problems.append(path)

    walk(committed, fresh, "$")
    return problems


def render(inventory: Dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"


def integrity_problems(inventory: Dict[str, Any]) -> List[str]:
    """Fail-closed properties of the document ITSELF (parent AC2/AC3).

    Drift and integrity are different questions, and conflating them left a
    hole a reviewer reproduced: `--check` compared committed against fresh,
    so an artifact that faithfully recorded a fail-open state — a surface with
    no writer, an `unknown` writer nobody dispositioned, an overlap nobody
    classified — was drift-clean and passed green. Regenerating only made the
    committed copy agree with the bad state faster.

    This gate answers the other question: is what the document records
    acceptable at all? It runs on the committed artifact before any
    comparison, so a hand-crafted or regenerated fail-open artifact cannot be
    merged, and again on the fresh scan so generation cannot quietly produce
    one that the next `--check` would bless.
    """
    problems: List[str] = []
    coverage = inventory.get("coverage") or {}
    surfaces = inventory.get("surfaces") or {}

    for surface in SURFACES:
        cov = coverage.get(surface)
        if not cov:
            problems.append(f"$coverage.{surface}: surface is absent from the coverage table")
            continue
        if not cov.get("production_writers"):
            problems.append(f"$coverage.{surface}: no production writer (coverage is fail-closed)")
        elif not cov.get("ok"):
            problems.append(
                f"$coverage.{surface}: {cov.get('undeclared_unknown_writers', 0)} "
                "writer(s) with lifecycle `unknown` and no recorded disposition"
            )
        for index, writer in enumerate((surfaces.get(surface) or {}).get("writers") or []):
            if writer.get("lifecycle") == "unknown" and not writer.get("disposition"):
                problems.append(
                    f"$surfaces.{surface}.writers[{index}] ({writer.get('path')}): "
                    "lifecycle `unknown` with no disposition"
                )

    for index, overlap in enumerate(inventory.get("overlaps") or []):
        label = overlap.get("surface") or f"[{index}]"
        if overlap.get("classification") not in OVERLAP_CLASSIFICATIONS:
            problems.append(
                f"$overlaps.{label}.classification: {overlap.get('classification')!r} "
                f"is not one of {list(OVERLAP_CLASSIFICATIONS)}"
            )
        if overlap.get("disposition") not in OVERLAP_DISPOSITIONS:
            problems.append(
                f"$overlaps.{label}.disposition: {overlap.get('disposition')!r} "
                f"is not one of {list(OVERLAP_DISPOSITIONS)}"
            )
        if not overlap.get("replaces"):
            problems.append(f"$overlaps.{label}.replaces: no replacing path named")

    return problems


def retired_authority_problems(inventory: Dict[str, Any], markdown: str) -> List[str]:
    """Parent AC5 guard: a retired surface may never read as live authority.

    Returns a list of problems (empty is healthy). Checks both the structured
    record and the rendered prose, because prose is what humans and agents read.
    """
    problems: List[str] = []
    authority_words = ("live authority", "is authoritative", "remains authoritative")
    for retired in inventory.get("retired_surfaces", []):
        token = retired.get("token", "")
        if retired.get("authority") != "historical":
            problems.append(
                f"retired surface {token!r} has authority {retired.get('authority')!r}, expected 'historical'"
            )
        for line in markdown.splitlines():
            lowered = line.lower()
            if token and token.lower() in lowered:
                for phrase in authority_words:
                    if phrase in lowered and "not" not in lowered and "never" not in lowered:
                        problems.append(
                            f"retired surface {token!r} is described as live authority: {line.strip()[:120]}"
                        )
    return problems


def render_markdown(inventory: Dict[str, Any]) -> str:
    """Render the human-readable ownership map from the same inventory.

    Generated rather than hand-maintained so the prose and the JSON cannot
    disagree about who owns what or which module is a violation.
    """
    meta = inventory["_meta"]
    owner = inventory["canonical_ownership"]
    lines: List[str] = [
        "# Lilac Wave 1 — executable topology inventory",
        "",
        f"> Generated by `{meta['generator']}` (plan {meta['plan']}, parent "
        f"{meta['parent_plan']}). Do not edit by hand — regenerate instead.",
        ">",
        "> Drift contract: `--check` compares the code-derived topology. Per-service "
        "`pid` and `invocation_id` are runtime observations and are never "
        "compared, so a restart does not stale the artifact.",
        "",
        "## Canonical ownership",
        "",
        owner["statement"],
        "",
        f"Canonical owner: `{owner['owner']}` · canonical schema: "
        f"`{meta['canonical_schema']}`.",
        "",
        "## Surface coverage",
        "",
        "| Surface | Production writers | Canonical writers | Coverage |",
        "|---|---:|---:|---|",
    ]
    for surface, cov in inventory["coverage"].items():
        lines.append(
            f"| {SURFACE_LABELS[surface]} | {cov['production_writers']} | "
            f"{cov['canonical_writers']} | {'ok' if cov['ok'] else '**FAIL-CLOSED**'} |"
        )

    lines += ["", "## Violations — canonical writes outside the owner", ""]
    if inventory["violations"]:
        for violation in inventory["violations"]:
            lines.append(
                f"- `{violation['path']}:{violation['line']}` — {violation['surface']} "
                f"(`{violation['table']}`). {violation['reason']}"
            )
    else:
        lines.append("None recorded.")

    lines += ["", "## Python ↔ TypeScript overlaps", ""]
    if inventory["overlaps"]:
        lines += [
            "| Surface | Python writers | TypeScript writers | Classification | Disposition | Replaces |",
            "|---|---|---|---|---|---|",
        ]
        for overlap in inventory["overlaps"]:
            lines.append(
                f"| {SURFACE_LABELS[overlap['surface']]} | "
                f"{', '.join(f'`{p}`' for p in overlap['python_writers'])} | "
                f"{', '.join(f'`{p}`' for p in overlap['typescript_writers'])} | "
                f"{overlap['classification']} | {overlap['disposition']} | "
                f"`{overlap['replaces']}` |"
            )
    else:
        lines.append("None: no surface is written from both languages.")

    lines += ["", "## Live consumers (code that reads the canonical surfaces)", ""]
    for surface in SURFACES:
        consumers = sorted(
            {
                reader["path"]
                for reader in inventory["surfaces"][surface]["readers"]
                if reader["lifecycle"] != "test/worktree"
            }
        )
        if consumers:
            lines.append(
                f"- **{SURFACE_LABELS[surface]}** — "
                + ", ".join(f"`{path}`" for path in consumers)
            )
        else:
            lines.append(f"- **{SURFACE_LABELS[surface]}** — no production reader recorded")
    lines += [
        "",
        "Runtime facts for the processes serving these modules — unit, port, pid, "
        "invocation id, working tree — are recorded in "
        "`executable-topology.json` under `services[]`, which is an observation of "
        "the generating host and is deliberately not part of the drift contract.",
    ]

    lines += [
        "",
        "## Retired surfaces — historical, not authority",
        "",
        "Each entry was authority once and is now historical. Nothing here may be "
        "treated as live authority; a guard fails if one is described that way.",
        "",
        "| Token | Authority | Evidence |",
        "|---|---|---|",
    ]
    for retired in inventory["retired_surfaces"]:
        lines.append(
            f"| {retired['token']} | {retired['authority']} | {retired['evidence']} |"
        )

    lines += [
        "",
        "## Regenerating",
        "",
        "```bash",
        "bin/lilac-topology-inventory.py           # rewrite JSON + this document",
        "bin/lilac-topology-inventory.py --check   # exit 1 if stale, 3 if fail-open",
        "```",
        "",
        "`--check` is enforced by `bin/tests/test_lilac_topology_inventory.py` in the "
        "`bin/tests guards` CI job, so a writer added without regenerating the "
        "inventory fails the build rather than silently widening the blind spot.",
        "",
        "Exit 3 is the other half: `--check` refuses a document that *records* a "
        "fail-open state (a surface with no production writer, an `unknown` writer "
        "with no disposition, or an overlap without a classification, disposition "
        "or replacing path), even when a fresh scan agrees with it. Regenerating "
        "cannot clear that — only declaring the missing decision can.",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None, probe: Optional[LiveProbe] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed JSON is stale")
    parser.add_argument("--print", dest="to_stdout", action="store_true", help="emit JSON to stdout")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON output path (default: %(default)s)")
    parser.add_argument("--md-out", default=str(DEFAULT_MD), help="Markdown output path (default: %(default)s)")
    parser.add_argument("--root", default=str(REPO_ROOT), help="repo root to scan")
    parser.add_argument("--no-probe", action="store_true", help="skip the runtime probe")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out)
    md_out = Path(args.md_out)
    live: Dict[str, Any] = {"available": False, "services": []}
    if not args.no_probe:
        probe_fn = probe or probe_live
        live = probe_fn()

    inventory = build_inventory(
        root=root,
        live=live,
        generated_at=datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    )

    if args.to_stdout:
        sys.stdout.write(render(inventory))
        return 0

    if args.check:
        if not out.is_file():
            print(f"topology artifact missing: {out}", file=sys.stderr)
            return 2
        try:
            committed = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"topology artifact is not valid JSON: {exc}", file=sys.stderr)
            return 2
        # Integrity first: a fail-open artifact can be perfectly drift-clean,
        # and regenerating it is what makes it so. Refuse the recorded state
        # before asking whether it matches a fresh scan.
        fail_open = integrity_problems(committed) or integrity_problems(inventory)
        if fail_open:
            print(
                f"topology artifact records a fail-open state ({len(fail_open)} problem(s)):",
                file=sys.stderr,
            )
            for path in fail_open[:40]:
                print(f"  - {path}", file=sys.stderr)
            print(
                "fix the recorded state (declare a disposition, classify the "
                "overlap, or restore a writer); regenerating alone will not clear this",
                file=sys.stderr,
            )
            return 3

        probed = bool(inventory.get("services"))
        problems = diff_projection(
            stable_projection(committed, include_services=probed),
            stable_projection(inventory, include_services=probed),
        )
        if md_out.is_file():
            if md_out.read_text(encoding="utf-8") != render_markdown(inventory):
                problems.append("$markdown.document")
        else:
            problems.append("$markdown.missing")
        if problems:
            print(f"topology artifact is stale ({len(problems)} difference(s)):", file=sys.stderr)
            for path in problems[:40]:
                print(f"  - {path}", file=sys.stderr)
            if len(problems) > 40:
                print(f"  ... and {len(problems) - 40} more", file=sys.stderr)
            print("regenerate with: bin/lilac-topology-inventory.py", file=sys.stderr)
            return 1
        print("topology artifacts are current")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(inventory), encoding="utf-8")
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render_markdown(inventory), encoding="utf-8")
    ok = sum(1 for c in inventory["coverage"].values() if c["ok"])
    print(
        f"wrote {out} + {md_out.name} — {len(inventory['services'])} live service(s), "
        f"{ok}/{len(SURFACES)} surfaces covered, "
        f"{len(inventory['violations'])} violation(s), "
        f"{len(inventory['overlaps'])} overlap(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
