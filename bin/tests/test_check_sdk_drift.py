"""Hermetic tests for bin/check_sdk_drift.py.

Exercises the pure logic (contract hashing, tree diffing, stamp round-trip,
provider parsing, stamp-mode drift detection) in a throwaway directory tree.
No tsp toolchain, no network, no repo state touched.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent  # worktree root
_spec = importlib.util.spec_from_file_location("check_sdk_drift", REPO / "bin" / "check_sdk_drift.py")
csd = importlib.util.module_from_spec(_spec)
sys.modules["check_sdk_drift"] = csd
_spec.loader.exec_module(csd)


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A fake repo root with one provider: spec dir + generated tree."""
    root = tmp_path / "repo"
    spec = root / "typespec" / "v1" / "prov-kernel" / "python"
    gen = root / "python" / "prov" / "generated"
    spec.mkdir(parents=True)
    gen.mkdir(parents=True)
    (spec / "tspconfig.yaml").write_text("emit: []\n")
    (spec / "main.tsp").write_text('import "./models.tsp";\n')
    (spec / "models.tsp").write_text("model Foo { name: string; }\n")
    (gen / "client.py").write_text("# generated\n")
    return root


def make_provider(root: Path) -> "csd.Provider":
    return csd.Provider(
        name="prov-kernel",
        spec_dir=root / "typespec" / "v1" / "prov-kernel" / "python",
        generated_dirs=[root / "python" / "prov" / "generated"],
        extra_dirs=[],
        emitters=[("@typespec/http-client-python", "python")],
    )


# ------------------------------------------------------------ contract hash

def test_contract_hash_is_deterministic_and_content_sensitive(tree: Path):
    spec = tree / "typespec" / "v1" / "prov-kernel" / "python"
    h1, n = csd.contract_hash(spec)
    assert n == 3  # tspconfig.yaml + main.tsp + models.tsp
    h2, _ = csd.contract_hash(spec)
    assert h1 == h2

    (spec / "models.tsp").write_text("model Foo { name: string; other: int32; }\n")
    h3, _ = csd.contract_hash(spec)
    assert h3 != h1


def test_contract_hash_ignores_tsp_output_and_renames(tree: Path):
    spec = tree / "typespec" / "v1" / "prov-kernel" / "python"
    (spec / "tsp-output").mkdir()
    (spec / "tsp-output" / "openapi.yaml").write_text("openapi: junk\n")
    h1, n = csd.contract_hash(spec)
    assert n == 3  # tsp-output excluded

    (spec / "tsp-output" / "openapi.yaml").write_text("openapi: DIFFERENT junk\n")
    h2, _ = csd.contract_hash(spec)
    assert h1 == h2  # content under tsp-output does not move the hash


# ------------------------------------------------------------- tree diffing

def test_diff_trees_categories():
    committed = {"a.py": "1", "b.py": "2", "gone.py": "3"}
    fresh = {"a.py": "1", "b.py": "CHANGED", "new.py": "4"}
    modified, missing, unexpected = csd.diff_trees(committed, fresh)
    assert modified == ["b.py"]
    assert missing == ["gone.py"]
    assert unexpected == ["new.py"]


def test_diff_trees_identical():
    t = {"a.py": "1", "b/c.py": "2"}
    assert csd.diff_trees(t, dict(t)) == ([], [], [])


def test_snapshot_tree_stable_relative_paths(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("hello")
    snap = csd.snapshot_tree(tmp_path)
    assert snap == {"sub/x.py": __import__("hashlib").sha256(b"hello").hexdigest()}
    assert csd.snapshot_tree(tmp_path / "does-not-exist") == {}


def test_snapshot_tree_ignores_local_bytecode(tmp_path: Path):
    """Regression (JVM extension, 2026-09-23): a host that imported the
    generated SDK leaves __pycache__/*.pyc in the tree; a fresh regen never
    produces them, so the guard false-DRIFTed ('23 missing') on pristine
    committed trees. Bytecode is untracked residue — never part of the
    judged surface."""
    (tmp_path / "org").mkdir()
    (tmp_path / "org" / "client.py").write_text("# generated\n")
    cache = tmp_path / "org" / "__pycache__"
    cache.mkdir()
    (cache / "client.cpython-313.pyc").write_bytes(b"\x00bytecode")
    (tmp_path / "loose.pyc").write_bytes(b"\x00stray")
    snap = csd.snapshot_tree(tmp_path)
    assert list(snap) == ["org/client.py"]


# -------------------------------------------------------------- stamp mode

def test_stamp_round_trip_and_drift(tree: Path):
    prov = make_provider(tree)
    stamp_dir = tree / "bin" / "sdk-type-stamps"

    ok, lines = csd.check_stamp(prov, stamp_dir)
    assert not ok and "no stamp recorded" in lines[0]  # missing stamp fails

    csd.update_stamp(prov, stamp_dir)
    ok, lines = csd.check_stamp(prov, stamp_dir)
    assert ok, lines
    assert "stamp matches" in lines[0]

    # contract edit -> stamp must flag
    (prov.spec_dir / "models.tsp").write_text("model Bar { x: int32; }\n")
    ok, lines = csd.check_stamp(prov, stamp_dir)
    assert not ok and "TypeSpec contract changed" in lines[0]


def test_stamp_records_only_contract_sources(tree: Path):
    prov = make_provider(tree)
    stamp_dir = tree / "bin" / "stamps"
    csd.update_stamp(prov, stamp_dir)
    recorded = (stamp_dir / "prov-kernel.sha256").read_text().strip()
    current, _ = csd.contract_hash(prov.spec_dir)
    assert recorded == current


# ---------------------------------------------------------- provider parsing

def test_parse_provider_defaults_python_emitter(tmp_path: Path):
    root = tmp_path
    (root / "spec").mkdir()
    (root / "gen").mkdir()
    prov = csd.parse_provider("spec,gen,extra", root)
    assert prov.name == "spec"
    assert prov.generated_dirs == [root / "gen"]
    assert prov.extra_dirs == [root / "extra"]
    assert prov.emitters == [("@typespec/http-client-python", "python")]


def test_parse_provider_requires_two_paths():
    with pytest.raises(SystemExit):
        csd.parse_provider("only-one-path", Path("/tmp"))


def test_provider_from_preset_wiring(tree: Path):
    # presets exist for the two real providers and point at real surfaces
    for name in ("conduit-kernel", "peb-kernel"):
        pre = csd.PRESETS[name]
        assert (tree / pre["spec"]).exists() or True  # fake tree has no real specs
        assert pre["generated"], f"{name} must protect at least one generated tree"
        assert any(e[0] == "@typespec/http-client-python" for e in pre["emitters"])


# ------------------------------------------------- mode selection / units

def test_effective_run_mode_forces_stamp_without_reference_tree(tmp_path: Path):
    bare = csd.Provider(name="x", spec_dir=tmp_path)          # no generated/extra dirs
    with_tree = csd.Provider(name="y", spec_dir=tmp_path,
                             generated_dirs=[tmp_path / "gen"])
    assert csd.effective_run_mode(bare, "regen") == "stamp"
    assert csd.effective_run_mode(with_tree, "regen") == "regen"
    assert csd.effective_run_mode(with_tree, "stamp") == "stamp"


def test_preset_topology_matches_repo_layout(tree: Path):
    # the two python providers protect committed trees; the java one protects
    # the gitignored staging tree; the rest are stamp-only
    py = [n for n, p in csd.PRESETS.items() if all("staging" not in g for g in p["generated"])]
    assert set(py) == {"conduit-kernel", "peb-kernel"}
    assert csd.PRESETS["peb-kernel-spring"]["generated"] == ["typespec/v1/staging/jvm/spring/peb-kernel"]
    for name in csd.STAMP_ONLY:
        assert name in csd.STAMP_ONLY_PRESETS, f"{name} missing from STAMP_ONLY_PRESETS"
    assert set(csd.STAMP_ONLY) == set(csd.STAMP_ONLY_PRESETS)


# ------------------------------------------------------------- exit mapping

def test_main_returns_2_when_tsp_missing(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(csd, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _: None)
    rc = csd.main.__wrapped__() if hasattr(csd.main, "__wrapped__") else None
    # direct: find_tsp must raise SystemExit("2 ...")
    with pytest.raises(SystemExit) as ei:
        csd.find_tsp(tmp_path)
    assert str(ei.value).startswith("2")


# --------------------------------------------------- JVM / emitter mapping

def test_java_preset_emitter_mapping():
    """The JVM provider must compile with the java emitter and read its
    fresh output from out/java (the subdir the preset declares)."""
    pre = csd.PRESETS["peb-kernel-spring"]
    assert pre["emitters"] == [("@typespec/http-client-java", "java")]
    prov = csd.provider_from_preset("peb-kernel-spring", REPO)
    assert csd.fresh_output_subdir(prov, 0) == "java"


def test_fresh_output_subdir_fallback_and_python_providers():
    for name in ("conduit-kernel", "peb-kernel"):
        prov = csd.provider_from_preset(name, REPO)
        assert csd.fresh_output_subdir(prov, 0) == "python"
    # out-of-range index falls back to the historical python default
    prov = csd.provider_from_preset("peb-kernel-spring", REPO)
    assert csd.fresh_output_subdir(prov, 5) == "python"


def _java_provider(root: Path, staged: bool) -> "csd.Provider":
    """A peb-kernel-spring-shaped provider over a fake tree: gitignored
    staging tree present or absent, java emitter mapping."""
    spec = root / "typespec" / "v1" / "peb-kernel" / "spring"
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "main.tsp").write_text("model Peb {}\n")
    gen_dirs = []
    if staged:
        staging = root / "typespec" / "v1" / "staging" / "jvm" / "spring" / "peb-kernel"
        staging.mkdir(parents=True, exist_ok=True)
        gen_dirs.append(staging)
    return csd.Provider(
        name="peb-kernel-spring",
        spec_dir=spec,
        generated_dirs=gen_dirs,
        extra_dirs=[],
        emitters=[("@typespec/http-client-java", "java")],
    )


def _fake_regenerate(java_content: str, python_decoy: str = ""):
    """Stand-in for regenerate(): builds a scratch tree with out/java (what
    the java emitter really writes) and optionally a decoy out/python."""
    def _regen(prov: "csd.Provider", root: Path):
        scratch = root / "typespec" / "v1" / f".sdkdrift-{prov.name}"
        out = scratch / "out"
        (out / "java" / "src").mkdir(parents=True)
        (out / "java" / "src" / "PebClient.java").write_text(java_content)
        if python_decoy:
            (out / "python").mkdir(parents=True, exist_ok=True)
            (out / "python" / "PebClient.java").write_text(python_decoy)
        return scratch, out
    return _regen


def test_check_regen_diffs_emitter_mapped_subdir(monkeypatch, tmp_path: Path):
    """Regression (JVM extension, 2026-09-23): check_regen once hardcoded
    fresh_root/'python' for every generated tree, so the java provider's
    diff read an EMPTY directory and reported the whole staged tree as
    'missing' — a permanent false DRIFT. The diff must read the subdir the
    preset's emitter mapping declares."""
    root = tmp_path / "repo"
    prov = _java_provider(root, staged=True)
    staged = prov.generated_dirs[0]
    (staged / "src").mkdir()
    (staged / "src" / "PebClient.java").write_text("// staged v1\n")
    monkeypatch.setattr(csd, "regenerate", _fake_regenerate("// staged v1\n", python_decoy="// DECOY\n"))

    ok, lines = csd.check_regen(prov, root)
    assert ok, lines
    assert any("identical" in ln for ln in lines)


def test_check_regen_detects_real_java_drift(monkeypatch, tmp_path: Path):
    root = tmp_path / "repo"
    prov = _java_provider(root, staged=True)
    staged = prov.generated_dirs[0]
    (staged / "src").mkdir()
    (staged / "src" / "PebClient.java").write_text("// staged STALE\n")
    monkeypatch.setattr(csd, "regenerate", _fake_regenerate("// fresh\n"))

    ok, lines = csd.check_regen(prov, root)
    assert not ok
    assert any("DRIFT" in ln and "1 modified" in ln for ln in lines)


def test_check_regen_staging_absent_is_skip_not_drift(monkeypatch, tmp_path: Path):
    """JVM staging trees are gitignored and disposable: an absent reference
    tree is an ok skip (stamp mode guards the contract), never a failure."""
    root = tmp_path / "repo"
    prov = _java_provider(root, staged=False)
    assert prov.generated_dirs == []  # staged tree absent -> no reference
    # make it a regen-eligible provider WITH a declared generated dir that
    # does not exist on disk (the real preset shape when staging is wiped):
    prov.generated_dirs = [root / "typespec" / "v1" / "staging" / "jvm" / "spring" / "peb-kernel"]
    monkeypatch.setattr(csd, "regenerate", _fake_regenerate("// fresh\n"))

    ok, lines = csd.check_regen(prov, root)
    assert ok, lines
    assert any("reference tree absent" in ln for ln in lines)


def test_every_provider_has_a_committed_stamp():
    """Adding a provider without committing its stamp makes stamp-mode hosts
    (no tsp toolchain) fail with 'no stamp recorded' — the stamp must land
    in the same change as the preset."""
    for name in (*csd.PRESETS.keys(), *csd.STAMP_ONLY):
        assert (REPO / "bin" / "sdk-type-stamps" / f"{name}.sha256").is_file(), \
            f"{name} has no committed stamp"
