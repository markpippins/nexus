"""Hermetic tests for scripts/moleculer-machine-bootstrap.sh.

The bootstrap script automates the devops quickstart (per-app npm install/
test/start, standalone|mesh modes, systemd unit template, drift gates). All
ports/app names come from the single-source registry moleculer/ports.yaml —
tests run against a SYNTHETIC root (NEXUS_BOOTSTRAP_ROOT) with a 2-app
registry, so nothing here touches the real tree.

No daemons are started: --start is exercised via --dry-run (asserting the
exact composed command), and node/npm/git/python3 are stubbed through a fake
bin dir on PATH (the World pattern from test_assert_moleculer_ports.py).
NATS/PG probes are redirected via BOOTSTRAP_NATS_TARGET/BOOTSTRAP_PG_TARGET
to ports guaranteed to fail, keeping the soft-warn path hermetic too.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "moleculer-machine-bootstrap.sh"

SYNTH_REGISTRY = """# synthetic registry for tests
infra:
  - port: 4050
    name: "search"
  - port: 4060
    name: "solscript"
canary:
  - port: 4114
    name: "voyager"
    namespace: "voyager"
    incumbent: "typescript/voyager-srv"
    incumbent_port: 3114
    description: "test row"
    readme_status: "test"
    map_registry_status: "CANARY — test"
    ratified: 2026-09-22
    ratification: "test clause"
  - port: 4116
    name: "aegis"
    namespace: "aegis"
    incumbent: "typescript/aegis-srv"
    incumbent_port: 3116
    description: "test row"
    readme_status: "test"
    map_registry_status: "CANARY — test"
    ratified: null
    ratification: "test clause"
"""

UNREACHABLE = "127.0.0.1:9"  # discard port — nothing listens, probes fail fast


def _exe(path: Path, body: str):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class World:
    """Scratch repo root + fake bin; both injected through env."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "repo"
        (self.root / "moleculer" / "voyager").mkdir(parents=True)
        (self.root / "moleculer" / "aegis").mkdir()
        (self.root / "tools" / "api-docs").mkdir(parents=True)
        (self.root / "moleculer" / "ports.yaml").write_text(SYNTH_REGISTRY)
        (self.root / "moleculer" / "voyager" / "moleculer.config.standalone.js").write_text("// t")
        (self.root / "moleculer" / "aegis" / "moleculer.config.standalone.js").write_text("// t")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        # Baseline toolchain: node 24, working npm/git/python3+pyyaml.
        # (The node stub must NEVER call node again — PATH would resolve to the
        # stub itself and recurse forever. Emit the major version directly.)
        _exe(self.bin / "node", 'case "$1" in -v) echo v24.0.0;; *) echo 24;; esac')
        _exe(self.bin / "npm", "echo 11.0.0")
        _exe(self.bin / "git", "echo /tmp/fake-root")
        _exe(self.bin / "python3", "case \"$*\" in *yaml*) exit 0;; *) exit 0;; esac")
        # PATH is fakebin + system dirs ONLY — deliberately EXCLUDING the host
        # toolchain dirs (nvm etc.) so the fake npm/node are authoritative and
        # a test can remove one to simulate absence.
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "NEXUS_BOOTSTRAP_ROOT": str(self.root),
            "BOOTSTRAP_NATS_TARGET": UNREACHABLE,
            "BOOTSTRAP_PG_TARGET": UNREACHABLE,
            "HOME": str(tmp_path / "home"),
        }

    def make_app_with_pkg(self, app: str):
        (self.root / "moleculer" / app / "package.json").write_text("{}")

    def run(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**os.environ, **self.env},
            capture_output=True, text=True, timeout=60,
        )


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


class TestCheck:
    def test_passes_with_fresh_toolchain(self, world):
        r = world.run("--check")
        assert r.returncode == 0, r.stderr
        assert "CHECK: OK" in r.stdout
        assert "node: v24.0.0 OK" in r.stdout

    def test_fails_on_old_node(self, world):
        _exe(world.bin / "node", 'case "$1" in -v) echo v16.0.0;; *) echo 16;; esac')
        r = world.run("--check")
        assert r.returncode == 1
        assert "CHECK: FAILED" in r.stdout
        assert "older than" in r.stderr

    def test_fails_when_npm_missing(self, world):
        (world.bin / "npm").unlink()
        r = world.run("--check")
        assert r.returncode == 1
        assert "npm not found" in r.stderr

    def test_fails_when_node_missing(self, world):
        (world.bin / "node").unlink()
        # PATH restricted to the fake bin ONLY: this host ships a real node in
        # /usr/bin AND /bin, so both must be excluded. A bash shim inside the
        # fake bin keeps the subprocess runner resolvable.
        _exe(world.bin / "bash", "exec /bin/bash \"$@\"")
        world.env["PATH"] = str(world.bin)
        r = world.run("--check")
        assert r.returncode == 1
        assert "node not found" in r.stderr

    def test_fails_without_pyyaml(self, world):
        _exe(world.bin / "python3", "exit 1")
        r = world.run("--check")
        assert r.returncode == 1
        assert "pyyaml missing" in r.stderr

    def test_soft_warns_on_unreachable_nats_pg(self, world):
        r = world.run("--check")
        assert r.returncode == 0  # soft checks never hard-fail
        # Soft-probe failures are WARN lines — they go to stderr, and the run
        # still ends CHECK: OK on stdout.
        assert "NATS not reachable" in r.stderr
        assert "PostgreSQL not reachable" in r.stderr
        assert "CHECK: OK" in r.stdout


class TestList:
    def test_lists_apps_and_ports_from_registry(self, world):
        r = world.run("--list")
        assert r.returncode == 0
        out = r.stdout
        assert "search" in out and "4050" in out
        assert "voyager" in out and "4114" in out
        assert "aegis" in out and "4116" in out

    def test_list_excludes_non_name_lines(self, world):
        r = world.run("--list")
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        assert len(lines) == 5  # header + 4 registry entries


class TestStart:
    def test_dry_run_composes_standalone_command(self, world):
        r = world.run("--start", "voyager", "--standalone", "--dry-run")
        assert r.returncode == 0, r.stderr
        assert "port 4114" in r.stdout
        assert "SERVICE_PORT=4114" in r.stdout
        cmd_line = [ln for ln in r.stdout.splitlines() if "cmd (dry-run)" in ln][0]
        assert "moleculer.config.standalone.js" in cmd_line
        assert "moleculer/voyager" in cmd_line
        assert "npm start" not in cmd_line

    def test_dry_run_mesh_uses_npm_start(self, world):
        r = world.run("--start", "voyager", "--mesh", "--dry-run")
        assert r.returncode == 0, r.stderr
        cmd_line = [ln for ln in r.stdout.splitlines() if "cmd (dry-run)" in ln][0]
        assert "npm start" in cmd_line
        assert "standalone" not in cmd_line

    def test_unknown_app_fails_with_rc2(self, world):
        r = world.run("--start", "ghost", "--dry-run")
        assert r.returncode == 2
        assert "no such moleculer app" in r.stderr

    def test_standalone_refused_without_standalone_config(self, world):
        (world.root / "moleculer" / "aegis" / "moleculer.config.standalone.js").unlink()
        r = world.run("--start", "aegis", "--standalone", "--dry-run")
        assert r.returncode == 2
        assert "no moleculer.config.standalone.js" in r.stderr


class TestUnit:
    def test_unit_template_carries_registry_port(self, world):
        r = world.run("--unit", "voyager")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "moleculer-voyager.service" in out
        assert "SERVICE_PORT=4114" in out
        assert "moleculer/voyager" in out
        assert "WantedBy=default.target" in out

    def test_unit_prints_never_enables(self, world):
        # No systemctl invocation anywhere in the path; template goes to stdout.
        r = world.run("--unit", "aegis")
        assert r.returncode == 0
        assert "systemctl" not in r.stdout


class TestInstallTest:
    def test_install_runs_npm_in_app_dir(self, world):
        world.make_app_with_pkg("voyager")
        calls = world.bin / "npm"
        _exe(calls, 'echo "npm-cwd=$PWD args=$*"')
        r = world.run("--install", "voyager")
        assert r.returncode == 0, r.stderr
        assert "npm install: voyager" in r.stdout
        assert f"npm-cwd={world.root}/moleculer/voyager" in r.stdout.replace("//t", "")

    def test_install_unknown_app_fails_fatal(self, world):
        # Unknown app is a usage error (die/exit 2), not a per-app install
        # failure (rc 1).
        r = world.run("--install", "ghost")
        assert r.returncode == 2
        assert "no such moleculer app" in r.stderr

    def test_test_mode_reports_failure_rc1(self, world):
        world.make_app_with_pkg("voyager")
        _exe(world.bin / "npm", "exit 1")
        r = world.run("--test", "voyager")
        assert r.returncode == 1
        assert "npm test failed for voyager" in r.stderr


class TestDrift:
    def test_drift_invokes_both_gates_from_root(self, world):
        # python3 stub records its cwd; both gates must run from repo root.
        log = world.bin / "pycwd.log"
        _exe(world.bin / "python3", f'pwd >> "{log}"')
        (world.root / "tools" / "api-docs" / "check_drift.py").write_text("#")
        (world.root / "tools" / "api-docs" / "gen_port_registry.py").write_text("#")
        r = world.run("--drift")
        assert r.returncode == 0, r.stderr
        cwds = log.read_text().split()
        assert cwds == [str(world.root), str(world.root)]
        assert "port-registry byte-identity" in r.stdout
        assert "contract drift" in r.stdout


class TestUsage:
    def test_no_args_usage_rc1(self, world):
        r = world.run()
        assert r.returncode == 1

    def test_help_rc0(self, world):
        r = world.run("--help")
        assert r.returncode == 0
        assert "usage:" in r.stdout or "usage:" in r.stderr

    def test_unknown_mode_rc1(self, world):
        r = world.run("--frobnicate")
        assert r.returncode == 1
