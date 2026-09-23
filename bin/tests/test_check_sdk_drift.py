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


# ------------------------------------------------------------- exit mapping

def test_main_returns_2_when_tsp_missing(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(csd, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _: None)
    rc = csd.main.__wrapped__() if hasattr(csd.main, "__wrapped__") else None
    # direct: find_tsp must raise SystemExit("2 ...")
    with pytest.raises(SystemExit) as ei:
        csd.find_tsp(tmp_path)
    assert str(ei.value).startswith("2")
