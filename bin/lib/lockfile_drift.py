#!/usr/bin/env python3
"""Detect lockfile-vs-installed version drift.

Why this exists
---------------
Ruling 38L/38M made lockfiles tracked and per-service, which fixes *what gets
resolved at install time*. It does not notice when a tree's `node_modules` and
its lockfile have silently parted ways. That is exactly what happened to the
primary checkout: `adonisjs/nexus-control-edge/package-lock.json` sat untracked
and ignored, pinning 7.5.0 while the service ran 7.5.0 from a tree nobody could
audit -- and because the file was gitignored, a fast-forward overwrote it with
*no error at all*. The declared version and the running version then disagreed
with no signal anywhere.

Design rule
-----------
A check that cannot run must never look like a check that passed. Every service
lands in exactly one of MATCHED / DRIFTED / SKIPPED, and SKIPPED is reported as
its own count rather than folded into the passing total. A CI run with no
`node_modules` reports "20 skipped", not "20 ok".

Lockfile key model
------------------
lockfileVersion 3 keys `packages` by install path, e.g.

    ""                                     -> the root project
    "node_modules/pg"                      -> hoisted
    "node_modules/foo/node_modules/bar"    -> nested/conflicting

Those keys are relative paths, so they map directly onto the filesystem
relative to the lockfile's own directory. That handles hoisting, nesting and
scoped names without special cases.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Iterator

__all__ = [
    "Pin",
    "Drift",
    "ServiceResult",
    "read_lock_pins",
    "read_installed",
    "compare",
    "check_service",
    "iter_lockfiles",
]

# Outcome vocabulary. Kept explicit so callers cannot conflate them.
MATCHED = "MATCHED"
DRIFTED = "DRIFTED"
SKIPPED = "SKIPPED"  # lockfile readable, but node_modules absent/unreadable


@dataclass(frozen=True)
class Pin:
    """A single lockfile declaration.

    `optional` matters: npm lockfiles list optionalDependencies for EVERY
    platform, but `npm ci` only materialises the ones matching the current
    os/cpu. A lockfile pinning `@esbuild/darwin-arm64` is not drift on
    linux-x64 -- that package is *supposed* to be absent. Treating its absence
    as drift flagged 12 of 20 services in the first live run, which is how a
    guard gets switched off and ignored.
    """

    version: str
    optional: bool = False


@dataclass(frozen=True)
class Drift:
    """One package whose installed version differs from the lockfile pin."""

    path: str  # install path, e.g. "node_modules/pg"
    pinned: str  # version the lockfile declares
    installed: str | None  # version actually on disk, or None if absent
    optional: bool = False

    @property
    def kind(self) -> str:
        if self.installed is None:
            return "ABSENT-OPTIONAL" if self.optional else "MISSING"
        return "MISMATCH"


@dataclass
class ServiceResult:
    lockfile: pathlib.Path
    status: str
    drifts: list[Drift] = field(default_factory=list)
    reason: str = ""  # populated when status is SKIPPED
    checked: int = 0  # packages actually compared

    @property
    def service_dir(self) -> pathlib.Path:
        return self.lockfile.parent


def read_lock_pins(lockfile: pathlib.Path) -> dict[str, Pin]:
    """Return {install_path: Pin} from a v2/v3 lockfile.

    Raises ValueError if the file is absent, not JSON, or has no `packages`.
    """
    with open(lockfile, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("lockfile has no `packages` map (v1 lockfiles are unsupported)")
    pins: dict[str, Pin] = {}
    for key, meta in packages.items():
        if not key.startswith("node_modules/"):
            continue  # "" is the root project; workspace/link entries have no version
        version = meta.get("version") if isinstance(meta, dict) else None
        if version:
            pins[key] = Pin(version=version, optional=bool(meta.get("optional", False)))
    return pins


def read_installed(service_dir: pathlib.Path, key: str) -> str | None:
    """Version recorded by the package actually installed at `key`, or None.

    Reads node_modules/<key>/package.json. Returns None when the directory or
    its manifest is absent, which the caller reports as MISSING rather than
    guessing.
    """
    manifest = service_dir / key / "package.json"
    try:
        with open(manifest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def compare(pins: dict[str, Pin], installed_lookup) -> list[Drift]:
    """Diff declared pins against `installed_lookup(key) -> version | None`.

    Only packages the lockfile DECLARES are considered. An extra package on
    disk is not drift: hoisting and optional/platform-specific installs
    legitimately produce packages the lock does not pin at that path.

    Absence rules, which are the whole subtlety here:
      * non-optional + absent  -> MISSING. The tree is not what the lock says.
      * optional    + absent  -> expected. npm only materialises the
                                  platform-matching subset of optional deps.
      * present but wrong ver -> MISMATCH, optional or not. If a platform-
                                  specific package IS present it must be the
                                  pinned version.
    """
    drifts: list[Drift] = []
    for key, pin in pins.items():
        got = installed_lookup(key)
        if got == pin.version:
            continue
        if got is None and pin.optional:
            continue  # expected absence, not drift
        drifts.append(Drift(path=key, pinned=pin.version, installed=got, optional=pin.optional))
    return drifts


def check_service(lockfile: pathlib.Path) -> ServiceResult:
    """Compare one service's lockfile against its installed tree."""
    try:
        pins = read_lock_pins(lockfile)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ServiceResult(lockfile=lockfile, status=SKIPPED, reason=f"unreadable lockfile: {exc}")

    service_dir = lockfile.parent
    node_modules = service_dir / "node_modules"
    if not node_modules.is_dir():
        return ServiceResult(
            lockfile=lockfile,
            status=SKIPPED,
            reason="no node_modules -- nothing to compare against",
        )

    drifts = compare(pins, lambda key: read_installed(service_dir, key))
    return ServiceResult(
        lockfile=lockfile,
        status=DRIFTED if drifts else MATCHED,
        drifts=drifts,
        checked=len(pins),
    )


def iter_lockfiles(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield tracked-style lockfiles under `root`, skipping install trees.

    `git ls-files` is preferred when available so the population matches what
    is actually committed rather than whatever happens to be on disk; the
    filesystem walk is the fallback for non-repo contexts.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "*package-lock.json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        for line in out.splitlines():
            if line.strip():
                yield root / line.strip()
        return
    except (OSError, subprocess.SubprocessError):
        pass

    skip = {"node_modules", ".git", "build", "dist", ".next"}
    for path in sorted(root.rglob("package-lock.json")):
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        yield path
