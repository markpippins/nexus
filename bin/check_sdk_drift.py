#!/usr/bin/env python3
"""SDK drift guard: generated TypeSpec clients must match their contract.

History (2026-09-22): a regenerated python SDK (conduit-kernel + peb) sat
stranded in a working tree / stash-pop incident because nothing compared
the generated trees against the committed TypeSpec. This guard makes that
drift loud.

Two check modes (--mode):

  regen  (authoritative; CI)  run `tsp compile` per provider with emitter
         output redirected into a scratch dir, then diff against the
         committed generated trees. Any difference is drift: modified
         files, missing files, AND stale files the fresh output no longer
         produces. Requires the TypeSpec toolchain.

  stamp  (fallback; boot-time / hosts without tsp)  sha256 over the
         provider's TypeSpec source files vs the stamp in
         bin/sdk-type-stamps/<name>.sha256. Catches "TypeSpec edited but
         SDK not regenerated". Weaker than regen; refresh the stamp (with
         --update-stamp) only when the generated tree is verified current.

Exit codes: 0 = no drift, 1 = drift detected, 2 = tool/environment error.

Usage:
  check_sdk_drift.py                      # presets, auto mode (regen if tsp, else stamp)
  check_sdk_drift.py --mode regen         # force regen
  check_sdk_drift.py --mode stamp         # force stamp
  check_sdk_drift.py --update-stamp       # refresh stamps after verifying a regen
  check_sdk_drift.py -p <spec_dir>,<generated_dir>[,<extra_dir>...]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SPEC_SUFFIXES = {".tsp", ".yaml", ".yml"}

# Emitter output redirection: (emitter, scratch subdir).
# Note: typespec/*/tsp-output/ is a gitignored artifact — the committed
# surface this guard protects is exactly the two generated SDK trees.
PRESETS: dict[str, dict] = {
    "conduit-kernel": {
        "spec": "typespec/v1/conduit-kernel/python",
        "generated": ["python/conduit/generated"],
        "extras": [],
        "emitters": [
            ("@typespec/http-client-python", "python"),
        ],
    },
    "peb-kernel": {
        "spec": "typespec/v1/peb-kernel/python",
        "generated": ["python/peb-kernel/generated"],
        "extras": [],
        "emitters": [
            ("@typespec/http-client-python", "python"),
        ],
    },
    # Java emitters. Reference staging trees are deliberately gitignored
    # (typespec/v1/.gitignore: "contents are disposable emitter output",
    # Wave-4 folder hygiene) — only staging/.gitkeep is committed. Regen
    # mode therefore diffs the ON-DISK staged tree vs fresh compile and
    # skips providers whose staged tree does not currently exist (stamp
    # mode still guards their TypeSpec contracts).
    "peb-kernel-spring": {
        "spec": "typespec/v1/peb-kernel/spring",
        "generated": ["typespec/v1/staging/jvm/spring/peb-kernel"],
        "extras": [],
        "emitters": [
            ("@typespec/http-client-java", "java"),
        ],
    },
}

# Java-emitter providers with NO staged reference tree yet (Wave-4 wiped
# their disposable output). Stamp mode guards their contracts; regen
# byte-identity activates automatically once a tree is staged again.
STAMP_ONLY = [
    "service-registry-spring",
    "service-broker-spring",
    "service-broker-file-service",
    "service-broker-helidon",
    "service-broker-quarkus",
    "core-jvm-shared",
    "terrain-spring",
]

STAMP_ONLY_PRESETS: dict[str, dict] = {
    "service-registry-spring": {"spec": "typespec/v1/service-registry/spring"},
    "service-broker-spring": {"spec": "typespec/v1/service-broker/spring"},
    "service-broker-file-service": {"spec": "typespec/v1/service-broker/spring/file-service"},
    "service-broker-helidon": {"spec": "typespec/v1/service-broker/helidon"},
    "service-broker-quarkus": {"spec": "typespec/v1/service-broker/quarkus"},
    "core-jvm-shared": {"spec": "typespec/v1/core"},
    "terrain-spring": {"spec": "typespec/v1/terrain/spring"},
}

DEFAULT_STAMP_DIR = "bin/sdk-type-stamps"
COMPILE_TIMEOUT_S = 600


def repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parent.parent


def resolve(root: Path, p: str) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else root / pp


@dataclass
class Provider:
    name: str
    spec_dir: Path
    generated_dirs: list[Path] = field(default_factory=list)
    extra_dirs: list[Path] = field(default_factory=list)
    emitters: list[tuple[str, str]] = field(default_factory=list)


def parse_provider(arg: str, root: Path) -> Provider:
    parts = [s.strip() for s in arg.split(",") if s.strip()]
    if len(parts) < 2:
        raise SystemExit(f"2 --provider expects '<spec_dir>,<generated_dir>[,<extra>...]', got: {arg}")
    return Provider(
        name=Path(parts[0]).name,
        spec_dir=resolve(root, parts[0]),
        generated_dirs=[resolve(root, parts[1])],
        extra_dirs=[resolve(root, p) for p in parts[2:]],
        # ad-hoc providers default to the python-client emitter mapping so the
        # scratch compile still redirects output (both presets use this form)
        emitters=[("@typespec/http-client-python", "python")],
    )


def provider_from_preset(name: str, root: Path) -> Provider:
    pre = PRESETS[name]
    return Provider(
        name=name,
        spec_dir=resolve(root, pre["spec"]),
        generated_dirs=[resolve(root, g) for g in pre["generated"]],
        extra_dirs=[resolve(root, e) for e in pre["extras"]],
        emitters=[(e, sub) for e, sub in pre["emitters"]],
    )


def stamp_only_provider(name: str, root: Path) -> Provider:
    pre = STAMP_ONLY_PRESETS[name]
    return Provider(name=name, spec_dir=resolve(root, pre["spec"]))


# --------------------------------------------------------------------------
# stamp mode
# --------------------------------------------------------------------------

def contract_hash(spec_dir: Path) -> tuple[str, int]:
    """Deterministic sha256 over the provider's TypeSpec source files."""
    h = hashlib.sha256()
    count = 0
    for p in sorted(spec_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SPEC_SUFFIXES:
            continue
        rel = p.relative_to(spec_dir).as_posix()
        if rel.startswith("tsp-output/"):
            continue  # generated snapshot, not contract source
        h.update(rel.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
        count += 1
    return h.hexdigest(), count


def stamp_path(stamp_dir: Path, name: str) -> Path:
    return stamp_dir / f"{name}.sha256"


def check_stamp(prov: Provider, stamp_dir: Path) -> tuple[bool, list[str]]:
    current, nfiles = contract_hash(prov.spec_dir)
    sp = stamp_path(stamp_dir, prov.name)
    if not sp.exists():
        return False, [
            f"DRIFT {prov.name}: no stamp recorded ({sp.relative_to(repo_root()) if stamp_dir.is_relative_to(repo_root()) else sp}) -- "
            f"verify the generated tree is current, then run --update-stamp"
        ]
    recorded = sp.read_text().split()[0].strip()
    if recorded != current:
        return False, [
            f"DRIFT {prov.name}: TypeSpec contract changed since stamp "
            f"({nfiles} source files; stamp {recorded[:12]} != current {current[:12]}) -- regenerate the SDK"
        ]
    return True, [f"ok {prov.name}: contract stamp matches ({nfiles} TypeSpec source files)"]


def update_stamp(prov: Provider, stamp_dir: Path) -> Path:
    stamp_dir.mkdir(parents=True, exist_ok=True)
    current, _ = contract_hash(prov.spec_dir)
    sp = stamp_path(stamp_dir, prov.name)
    sp.write_text(f"{current}\n")
    return sp


# --------------------------------------------------------------------------
# regen mode
# --------------------------------------------------------------------------

def find_tsp(root: Path) -> str:
    local = root / "typespec" / "v1" / "node_modules" / ".bin" / "tsp"
    if local.exists():
        return str(local)
    from shutil import which
    found = which("tsp")
    if found:
        return found
    raise SystemExit("2 tsp not found (install typespec/v1 toolchain or use --mode stamp)")


def snapshot_tree(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        # Local bytecode residue (a host that imported the generated SDK):
        # gitignored everywhere, never committed, and never produced by a
        # fresh tsp regen — including it made the guard report a false
        # DRIFT ("23 missing") on pristine committed trees (2026-09-23).
        rel_parts = p.relative_to(root).parts
        if "__pycache__" in rel_parts or p.suffix.lower() in {".pyc", ".pyo"}:
            continue
        snap[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def fresh_output_subdir(prov: Provider, index: int) -> str:
    """The scratch subdir where emitter #index's output lands.

    Emitter-driven, per the preset's (emitter, subdir) mapping: the java
    emitters write to out/java, the python emitters to out/python. History
    (JVM extension, 2026-09-23): check_regen once hardcoded fresh_root /
    "python" for every generated tree, so the java provider's diff read an
    empty directory and reported the entire staged tree as "missing" — a
    false DRIFT that made the JVM surface permanently red.

    Falls back to "python" for ad-hoc providers (parse_provider defaults to
    the python emitter mapping) or unpaired generated dirs.
    """
    if index < len(prov.emitters):
        return prov.emitters[index][1]
    return "python"


def diff_trees(committed: dict[str, str], fresh: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    modified = sorted(f for f in committed.keys() & fresh.keys() if committed[f] != fresh[f])
    missing = sorted(set(committed) - set(fresh))
    unexpected = sorted(set(fresh) - set(committed))
    return modified, missing, unexpected


def regenerate(prov: Provider, root: Path) -> tuple[Path, Path]:
    """Compile a scratch copy of the spec with all emitter outputs redirected
    into <scratch>/out/<subdir>. The scratch lives under typespec/v1/ so
    TypeSpec's ancestor-walk resolves the repo-local node_modules; it never
    touches the committed generated trees and is removed by the caller."""
    scratch = root / "typespec" / "v1" / f".sdkdrift-{prov.name}"
    if scratch.exists():
        shutil.rmtree(scratch)
    # Copy the spec's PARENT tree (e.g. peb python imports ../spring/main.tsp)
    # minus heavy/generated dirs, then compile the in-tree copy.
    ignore = shutil.ignore_patterns("node_modules", "tsp-output")
    shutil.copytree(prov.spec_dir.parent, scratch / "spec-parent", ignore=ignore)
    spec_copy = scratch / "spec-parent" / prov.spec_dir.relative_to(prov.spec_dir.parent)

    cmd = [find_tsp(root), "compile", str(spec_copy / "main.tsp"),
           "--config", str(spec_copy / "tspconfig.yaml")]
    for emitter, sub in prov.emitters:
        cmd += ["--option", f"{emitter}.emitter-output-dir={scratch / 'out' / sub}"]

    env = dict(os.environ)
    res = subprocess.run(cmd, cwd=str(scratch), env=env,
                         capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S)
    if res.returncode != 0:
        sys.stderr.write((res.stdout[-4000:] + "\n" + res.stderr[-4000:]) + "\n")
        raise SystemExit(f"2 tsp compile failed for {prov.name} (exit {res.returncode})")
    return scratch, scratch / "out"


def check_regen(prov: Provider, root: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    scratch = None
    try:
        scratch, fresh_root = regenerate(prov, root)

        def report(label: str, committed_dir: Path, fresh_dir: Path) -> None:
            nonlocal ok
            if not committed_dir.exists():
                # Gitignored staging never staged (or wiped by policy):
                # nothing to diff against; the stamp check guards the
                # provider's TypeSpec contract instead.
                lines.append(f"ok {prov.name}/{label}: reference tree absent "
                             f"({committed_dir}) -- skipped; stamp mode guards this provider")
                return
            committed = snapshot_tree(committed_dir)
            fresh = snapshot_tree(fresh_dir)
            if not committed and not fresh:
                lines.append(f"ok {prov.name}/{label}: both sides empty")
                return
            modified, missing, unexpected = diff_trees(committed, fresh)
            if not (modified or missing or unexpected):
                lines.append(f"ok {prov.name}/{label}: {len(committed)} files identical to fresh regen")
                return
            ok = False
            lines.append(f"DRIFT {prov.name}/{label}: committed tree != fresh TypeSpec output "
                         f"({len(modified)} modified, {len(missing)} missing, {len(unexpected)} unexpected)")
            for f in modified[:10]:
                lines.append(f"  M {f}")
            for f in missing[:10]:
                lines.append(f"  - {f}  (in committed tree, not produced by regen)")
            for f in unexpected[:10]:
                lines.append(f"  + {f}  (produced by regen, not committed)")
            for lst, mark in ((modified[10:], "~"), (missing[10:], "-"), (unexpected[10:], "+")):
                if lst:
                    lines.append(f"  {mark} ... and {len(lst)} more")

        for i, gdir in enumerate(prov.generated_dirs):
            report("generated", gdir, fresh_root / fresh_output_subdir(prov, i))
        for edir in prov.extra_dirs:
            report("schema", edir, fresh_root / "schema")
    finally:
        if scratch is not None and scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
    return ok, lines


def effective_run_mode(prov: Provider, mode: str) -> str:
    """Stamp-only providers (no reference tree at all) never run the regen
    diff — there is nothing to diff against; their TypeSpec contract is
    guarded by the stamp check."""
    if not prov.generated_dirs and not prov.extra_dirs:
        return "stamp"
    return mode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["auto", "regen", "stamp"], default="auto")
    ap.add_argument("--provider", "-p", action="append", default=[],
                    help="<spec_dir>,<generated_dir>[,<extra_dir>...] (repeatable; default: presets)")
    ap.add_argument("--stamp-dir", default=DEFAULT_STAMP_DIR)
    ap.add_argument("--update-stamp", action="store_true",
                    help="refresh stamps instead of checking (run after verifying a regen)")
    args = ap.parse_args()

    root = repo_root()
    if args.provider:
        providers = [parse_provider(p, root) for p in args.provider]
    else:
        providers = [provider_from_preset(name, root) for name in PRESETS]
        providers += [stamp_only_provider(name, root) for name in STAMP_ONLY]

    if args.update_stamp:
        for prov in providers:
            sp = update_stamp(prov, resolve(root, args.stamp_dir))
            print(f"stamp updated: {sp}")
        return 0

    mode = args.mode
    if mode == "auto":
        try:
            find_tsp(root)
            mode = "regen"
        except SystemExit:
            mode = "stamp"
        print(f"mode: {mode}")

    stamp_dir = resolve(root, args.stamp_dir)
    all_ok = True
    for prov in providers:
        # Stamp-only providers (no reference tree) never run the regen diff.
        run_mode = effective_run_mode(prov, mode)
        if run_mode == "regen":
            try:
                ok, lines = check_regen(prov, root)
            except SystemExit as e:
                print(str(e), file=sys.stderr)
                return 2
        else:
            ok, lines = check_stamp(prov, stamp_dir)
        for ln in lines:
            print(ln)
        all_ok &= ok

    if not all_ok:
        print("\nSDK DRIFT DETECTED -- regenerate with tsp and commit, or (stamp mode) re-verify and --update-stamp")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
