"""Twin canary-tooling coverage guard — declared, not derived (queue item 14).

The failure this guard exists to prevent: a coverage guard whose SUBJECT SET is
computed by a predicate over the thing it is supposed to police. A guard written as

    TOOLING_TWINS = {t for t in registry if has(t, "tools/canary-run.sh")
                                      and has(t, "tools/canary-diff.py")}

covers every twin EXCEPT the ones whose tooling is incomplete — and "incomplete
tooling" is precisely what it is meant to catch. `moleculer/cascade` ships
`canary-diff.py` + `write-canary.py` and no runner, so a predicate-derived guard
covers the whole fleet except the twin that most needs covering, and cannot see
its own blind spot. A non-vacuity floor (`len(...) >= 4`) does not help: it does
not notice *one* twin dropping out.

So coverage here is DECLARED, in both directions:

  * the subject set is the registry (`moleculer/ports.yaml` `canary:` rows — the
    single source of truth per ruling 7c97ea63 §2), NOT a filter over it: every
    registry twin MUST appear in `TOOLING`;
  * each twin's expected tool set is declared exactly, and its on-disk `tools/`
    contents must equal that declaration.

Adding a twin without declaring its tooling fails. Removing a tool from a twin
fails. Both fail LOUDLY, naming the twin. No predicate can silently narrow the
subject set, because the subject set is the registry itself.

WHAT THIS GUARD DOES *NOT* DO: it does not require every twin to carry a runner.
The fleet has three honest shapes today (diff+runner, diff+write-canary,
diff-only). Requiring a runner fleet-wide is the separate Decision 36
prerequisite ("cascade canary-run.sh"), tracked there and deliberately not
smuggled in here. This guard requires present-or-absent to be DECLARED and to
match reality — which is what makes the gap visible instead of silent.

No network, no services: hermetic. Synthetic-tree tests prove the guard detects
a single dropped tool (i.e. it is not vacuous).
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

# Tooling that must be present on EVERY canary twin, whatever its shape: a twin
# without a diff tool cannot canary-diff anything and is not a canary twin.
UNIVERSAL = {"canary-diff.py"}

# DECLARED coverage inventory — the exact expected contents of each twin's
# `tools/` directory. Keyed by twin name; must equal the registry's canary set
# exactly (see TestSubjectSet). Update this when a twin is added or its tooling
# legitimately changes — that edit is the deliberate act this guard forces.
TOOLING = {
    "aegis": {"canary-diff.py", "canary-run.sh"},
    "cascade": {"canary-diff.py", "write-canary.py"},
    "conduit": {"canary-diff.py", "canary-run.sh"},
    "draft": {"canary-diff.py", "write-canary.py"},
    "execution": {"canary-diff.py", "canary-run.sh"},
    "harness": {"canary-diff.py", "canary-run.sh"},
    "kernel": {"canary-diff.py", "write-canary.py"},
    "knowledge": {"canary-diff.py", "write-canary.py"},
    "peb": {"canary-diff.py", "canary-run.sh"},
    "prompt-sync": {"canary-diff.py", "canary-run.sh"},
    "role-memory": {"canary-diff.py"},
    "semantics": {"canary-diff.py"},
    "substance": {"canary-diff.py", "canary-run.sh"},
    "tackle": {"canary-diff.py"},
    "voyager": {"canary-diff.py", "canary-run.sh"},
    "wind": {"canary-diff.py", "canary-run.sh"},
    "resolution": {"canary-diff.py", "canary-run.sh"},
    "nebula": {"canary-diff.py", "canary-run.sh"},
}


# ── pure helpers (root-parameterised so synthetic trees can exercise them) ──

def registry_twins(repo=REPO):
    """Canary twin names, straight from the single source of truth."""
    reg = yaml.safe_load((Path(repo) / "moleculer" / "ports.yaml").read_text())
    return [e["name"] for e in reg["canary"]]


def tools_on_disk(repo, twin):
    """The twin's tools/ file names, or None when the directory is absent."""
    d = Path(repo) / "moleculer" / twin / "tools"
    if not d.is_dir():
        return None
    return {p.name for p in d.iterdir() if p.is_file()}


def coverage_mismatches(repo, declared):
    """Return {twin: reason} for every coverage divergence. Empty == sound."""
    out = {}
    regset = set(registry_twins(repo))
    declaredset = set(declared)
    for t in sorted(regset - declaredset):
        out[t] = "registry twin has NO declared tooling (twin would land uncovered)"
    for t in sorted(declaredset - regset):
        out[t] = "declared name is not a registry canary twin"
    for t, decl in sorted(declared.items()):
        if t not in regset:
            continue
        actual = tools_on_disk(repo, t)
        if actual is None:
            out[t] = "tools/ directory missing"
        elif actual != decl:
            out[t] = f"declared {sorted(decl)} != on-disk {sorted(actual)}"
    return out


# ── the subject set is the registry, never a predicate ─────────────────────

class TestSubjectSet:
    def test_declared_inventory_equals_registry(self):
        reg, declared = set(registry_twins()), set(TOOLING)
        assert reg == declared, (
            "registry/declaration diverged — the subject set must be the "
            "registry, not a filter over it.\n"
            f"  registry twins with no declared tooling: {sorted(reg - declared)}\n"
            f"  declared names not in the registry:      {sorted(declared - reg)}\n"
            "Add the twin to TOOLING with its exact tools/ inventory."
        )

    def test_coverage_is_not_vacuous(self):
        assert len(TOOLING) >= 15, (
            f"guard declares only {len(TOOLING)} twins — the registry has "
            f"{len(registry_twins())}; the fleet cannot have shrunk unnoticed"
        )
        assert all(v for v in TOOLING.values()), "some twin declares an empty tool set"

    def test_registry_itself_is_nonempty(self):
        assert registry_twins(), "registry has no canary rows — guard would be vacuous"


# ── declared shape must match the disk, per twin ────────────────────────────

@pytest.mark.parametrize("twin", sorted(TOOLING))
def test_declared_shape_matches_disk(twin):
    actual = tools_on_disk(REPO, twin)
    assert actual is not None, (
        f"{twin}: no tools/ directory — twin is uncovered (declared "
        f"{sorted(TOOLING[twin])})"
    )
    assert actual == TOOLING[twin], (
        f"{twin}: declared {sorted(TOOLING[twin])} != on-disk {sorted(actual)} — "
        "a canary tool was added or removed without updating the declaration "
        "(exactly the silent-coverage-loss this guard exists to catch)"
    )


@pytest.mark.parametrize("twin", sorted(TOOLING))
def test_universal_tooling_present(twin):
    missing = UNIVERSAL - TOOLING[twin]
    assert not missing, f"{twin}: missing universal canary tooling {sorted(missing)}"


# ── a tools/ dir outside the registry is invisible to every registry guard ──

def test_tools_dirs_outside_the_registry_are_flagged():
    reg = set(registry_twins())
    root = REPO / "moleculer"
    orphans = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "tools").is_dir() and d.name not in reg
    )
    assert not orphans, (
        "tools/ directories exist for non-registry twins "
        f"{orphans} — a twin absent from moleculer/ports.yaml is invisible to "
        "every registry-driven guard. Add the registry row (then this guard)."
    )


# ── meta: the guard must detect a single dropped tool, not just pass ────────

def _synthetic_repo(tmp_path, twins):
    """Build a minimal ports.yaml + tools/ tree from {twin: {files}}."""
    reg = "canary:\n"
    for i, (twin, files) in enumerate(twins.items()):
        reg += (
            f'  - port: {4100 + i}\n'
            f'    name: "{twin}"\n'
            f'    namespace: "{twin}"\n'
            f'    incumbent: "typescript/{twin}-srv"\n'
            f'    incumbent_port: {3100 + i}\n'
            f'    description: "synth"\n'
            f'    readme_status: "synth"\n'
            f'    map_registry_status: "synth"\n'
            f'    ratified: null\n'
            f'    ratification: "synth"\n'
        )
    (tmp_path / "moleculer").mkdir()
    (tmp_path / "moleculer" / "ports.yaml").write_text(reg)
    for twin, files in twins.items():
        d = tmp_path / "moleculer" / twin / "tools"
        d.mkdir(parents=True)
        for f in files:
            (d / f).write_text("# stubb\n")
    return tmp_path


class TestGuardIsNotVacuous:
    def test_clean_synthetic_tree_is_sound(self, tmp_path):
        repo = _synthetic_repo(tmp_path, {"alpha": {"canary-diff.py", "canary-run.sh"}})
        assert coverage_mismatches(repo, {"alpha": {"canary-diff.py", "canary-run.sh"}}) == {}

    def test_dropped_tool_is_detected_and_named(self, tmp_path):
        # The exact cascade-class failure: a twin silently loses its runner.
        repo = _synthetic_repo(tmp_path, {"alpha": {"canary-diff.py"}})
        mism = coverage_mismatches(
            repo, {"alpha": {"canary-diff.py", "canary-run.sh"}}
        )
        assert "alpha" in mism, f"dropped tool not detected: {mism}"
        assert "canary-run.sh" in mism["alpha"]

    def test_undeclared_registry_twin_is_detected(self, tmp_path):
        repo = _synthetic_repo(tmp_path, {"alpha": {"canary-diff.py"}})
        mism = coverage_mismatches(repo, {})
        assert "alpha" in mism and "NO declared tooling" in mism["alpha"]

    def test_stray_declaration_is_detected(self, tmp_path):
        repo = _synthetic_repo(tmp_path, {"alpha": {"canary-diff.py"}})
        mism = coverage_mismatches(
            repo, {"alpha": {"canary-diff.py"}, "ghost": {"canary-diff.py"}}
        )
        assert "ghost" in mism and "not a registry" in mism["ghost"]

    def test_missing_tools_dir_is_detected(self, tmp_path):
        repo = _synthetic_repo(tmp_path, {"alpha": {"canary-diff.py"}})
        (tmp_path / "moleculer" / "alpha" / "tools").rename(
            tmp_path / "moleculer" / "alpha" / "gone"
        )
        mism = coverage_mismatches(repo, {"alpha": {"canary-diff.py"}})
        assert mism.get("alpha") == "tools/ directory missing"
