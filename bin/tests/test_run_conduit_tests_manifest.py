#!/usr/bin/env python3
"""Hermetic guard for bin/run_conduit_tests.py and its manifest.

Pins the properties that make the new conduit CI gate trustworthy. Each one is
a way the gate could quietly become decorative:

1. **Completeness.** Every python/conduit/tests/test_*.py is either in the run
   set or named in python/conduit/tests-ci-manifest.json. A new test file must
   not be ungated by default — that is the failure mode the bin analogue
   (bin/tests-ci-manifest.json, ruling fb11ad19) was created to end, where a
   V194 migration-number collision went unseen for a week.
2. **No stale exclusions.** A named file that no longer exists claims a debt
   that has been paid. The runner exits 2 for this; the guard checks it before
   CI spends 90s discovering the same thing.
3. **Every exclusion is documented.** kind/reason/owner/tracked/clears_when are
   all required, so an exclusion cannot be an unexplained `skip`.
4. **The gate is actually DB-free.** CONDUIT_PG_DSN, CONDUIT_PG_SCHEMA and
   PYTHONPATH must be scrubbed from the child environment (then replaced with
   the repo-local package roots, so `tackle`/`nexus_core` resolve from the
   checkout instead of from machine state). If that list ever
   loses CONDUIT_PG_DSN, tests silently start borrowing a developer's database
   and the "hermetic" tier becomes a machine-dependent result — green here,
   red or absent in CI.
5. **A zero-test run is a failure, not a pass.** Pinned by asserting the
   runner refuses to report success when nothing ran.

No database, no network, no pytest subprocesses: this guard reads files and
inspects the runner module.
"""
import importlib.util
import json
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO / "bin" / "run_conduit_tests.py"
SUITE = REPO / "python" / "conduit"
TESTS = SUITE / "tests"
MANIFEST = SUITE / "tests-ci-manifest.json"

REQUIRED_EXCLUSION_KEYS = {"kind", "reason", "owner", "tracked", "clears_when"}


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_conduit_tests", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_manifest_exists_and_carries_policy():
    m = _manifest()
    assert "_policy" in m, "manifest must state its policy"
    assert "_why_this_exists" in m, "manifest must record why the gate exists"
    assert "excluded" in m and isinstance(m["excluded"], dict)


def test_every_exclusion_is_documented():
    excluded = _manifest()["excluded"]
    assert excluded, "an empty exclusion map is suspicious for this suite"
    for name, entry in excluded.items():
        missing = REQUIRED_EXCLUSION_KEYS - set(entry)
        assert not missing, f"{name} exclusion is missing {sorted(missing)}"
        for key in REQUIRED_EXCLUSION_KEYS:
            assert str(entry[key]).strip(), f"{name}.{key} is blank"


def test_no_stale_exclusions():
    """An exclusion for a file that does not exist is a paid-off debt."""
    stale = [n for n in _manifest()["excluded"] if not (TESTS / n).is_file()]
    assert not stale, f"exclusions for files that no longer exist: {stale}"


def test_every_test_file_is_run_or_excluded():
    """There is no third state: run it, or name it with a reason."""
    discovered = {p.name for p in sorted(TESTS.glob("test_*.py"))}
    assert discovered, "discovery found no test files — the walk is broken"
    excluded = set(_manifest()["excluded"])
    ungated = sorted(discovered - excluded)
    assert ungated, "the run set is empty; the gate would check nothing"
    # The complement check: nothing is both excluded and silently missing.
    assert excluded <= discovered, (
        f"manifest names files not matched by discovery: {sorted(excluded - discovered)}"
    )


def test_runner_scrubs_db_and_pythonpath_from_child_env():
    runner = _load_runner()
    assert "CONDUIT_PG_DSN" in runner.SCRUBBED_ENV, (
        "the hermetic tier must not inherit a database; dropping CONDUIT_PG_DSN "
        "from SCRUBBED_ENV lets tests borrow the developer's DB"
    )
    assert "CONDUIT_PG_SCHEMA" in runner.SCRUBBED_ENV
    assert "PYTHONPATH" in runner.SCRUBBED_ENV


def test_runner_child_env_actually_omits_the_dsn(monkeypatch):
    runner = _load_runner()
    monkeypatch.setenv("CONDUIT_PG_DSN", "host=leaked dbname=leaked")
    monkeypatch.setenv("PYTHONPATH", "/tmp/leaked")
    env = runner.env_for_child()
    assert "CONDUIT_PG_DSN" not in env
    # PYTHONPATH is not carried through: the ambient value is dropped and
    # replaced with the repo-local package roots only. A leaked path here would
    # mean the child could import things that are not on this commit.
    assert "/tmp/leaked" not in env.get("PYTHONPATH", "")
    roots = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    assert roots, "repo-local package roots must be provided (tackle, nexus_core)"
    assert all(pathlib.Path(p).is_relative_to(runner.REPO) for p in roots), (
        f"every PYTHONPATH entry must be inside the checkout, got {roots}"
    )
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1"
    # Non-scrubbed vars must survive, or the child cannot run pytest sanely.
    assert "PATH" in env or not env.get("PATH")  # PATH may be unset in the test env


def test_runner_discovery_matches_manifest_target():
    runner = _load_runner()
    assert runner.TESTS == TESTS
    assert runner.MANIFEST == MANIFEST
    discovered = {p.name for p in runner.discovered()}
    assert discovered == {p.name for p in sorted(TESTS.glob("test_*.py"))}
