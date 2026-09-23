"""Hermetic tests for bin/assert_moleculer_ports.sh.

No live host state is touched: PATH is front-loaded with a fake bin dir
supplying ss/ps/systemctl fixtures, and HOME points at a scratch tree for
the systemctl --user calls. Each test builds its own fixture world.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "assert_moleculer_ports.sh"


def _write(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class World:
    """A scratch fake-bin + fake-HOME fixture world."""

    def __init__(self, tmp_path: Path):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.home = tmp_path / "home"
        (self.home / ".config" / "systemd" / "user").mkdir(parents=True)
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
        }

    def write_fake(self, name: str, body: str):
        _write(self.bin / name, f"#!/bin/sh\n{body}\n")

    def run(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**self.env},
            capture_output=True, text=True, timeout=30,
        )


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    # Baseline fakes: nothing bound, no runner, one disabled unit.
    w.write_fake("ss", "#!/bin/sh\nexit 0\n")   # emits nothing = nothing bound
    w.write_fake("ps", "#!/bin/sh\nexit 0\n")
    w.write_fake("systemctl",
                 "#!/bin/sh\n"
                 "if [ \"$1\" = \"--user\" ] && [ \"$2\" = \"list-unit-files\" ]; then\n"
                 "  printf 'UNIT FILE STATE PRESET\\nmoleculer-search.service disabled enabled\\n'\n"
                 "  exit 0\n"
                 "fi\n"
                 "if [ \"$1\" = \"--user\" ] && [ \"$2\" = \"is-enabled\" ]; then\n"
                 "  echo disabled; exit 1\n"
                 "fi\n"
                 "exit 0\n")
    return w


class TestPass:
    def test_clean_host_passes(self, world):
        r = world.run()
        assert r.returncode == 0
        assert "MOLECULER PORT GATE: OK" in r.stdout

    def test_notice_when_ss_missing(self, world):
        (world.bin / "ss").unlink()
        world.env["SS_BIN"] = ""   # dash-form ${SS_BIN-default}: empty = forced absence
        r = world.run()
        assert r.returncode == 0
        assert "ss not found" in r.stderr


class TestFailures:
    def test_bound_mapped_port_fails(self, world):
        world.write_fake(
            "ss",
            "if [ \"$1\" = \"-ltn\" ]; then "
            "printf 'LISTEN 0 511 0.0.0.0:4050 0.0.0.0:*\\n'; fi")
        r = world.run()
        assert r.returncode == 1
        assert "port 4050" in r.stderr and "BOUND" in r.stderr

    def test_host_runner_process_fails(self, world):
        world.write_fake(
            "ps",
            "if [ \"$1\" = \"-eo\" ]; then "
            "printf 'node /app/moleculer-runner --mask dist/services\\n'; fi")
        r = world.run()
        assert r.returncode == 1
        assert "moleculer runner process" in r.stderr

    def test_enabled_unit_fails(self, world):
        world.write_fake(
            "systemctl",
            "#!/bin/sh\n"
            "if [ \"$2\" = \"is-enabled\" ]; then echo enabled; exit 0; fi\n"
            "printf 'UNIT FILE STATE PRESET\\nmoleculer-search.service enabled enabled\\n'\n"
            "exit 0\n")
        r = world.run()
        assert r.returncode == 1
        assert "moleculer-search.service is ENABLED" in r.stderr


class TestExceptionWindow:
    def _doc(self, world, until):
        doc = world.home / "exception.doc"
        doc.write_text(f"EXCEPTION-UNTIL: {until}\n")
        return str(doc)

    def test_future_exception_allows_bound_port_with_notice(self, world):
        world.write_fake(
            "ss",
            "if [ \"$1\" = \"-ltn\" ]; then "
            "printf 'LISTEN 0 511 0.0.0.0:4050 0.0.0.0:*\\n'; fi")
        world.env["NEXUS_MOLECULER_EXCEPTION_DOC"] = self._doc(world, "2999-01-01")
        r = world.run()
        assert r.returncode == 0
        assert "EXCEPTION ACTIVE" in r.stderr

    def test_expired_exception_fails_again(self, world):
        world.write_fake(
            "ss",
            "if [ \"$1\" = \"-ltn\" ]; then "
            "printf 'LISTEN 0 511 0.0.0.0:4050 0.0.0.0:*\\n'; fi")
        world.env["NEXUS_MOLECULER_EXCEPTION_DOC"] = self._doc(world, "2020-01-01")
        r = world.run()
        assert r.returncode == 1

    def test_exception_doc_with_no_date_fails(self, world):
        doc = world.home / "exception.doc"
        doc.write_text("no marker here\n")
        world.env["NEXUS_MOLECULER_EXCEPTION_DOC"] = str(doc)
        world.write_fake(
            "ss",
            "if [ \"$1\" = \"-ltn\" ]; then "
            "printf 'LISTEN 0 511 0.0.0.0:4050 0.0.0.0:*\\n'; fi")
        r = world.run()
        assert r.returncode == 1


class TestJson:
    def test_json_report_wellformed(self, world):
        r = world.run("--json")
        import json
        data = json.loads(r.stdout)
        assert data["failures"] == []
        assert data["ports"] == [
            "4050", "4060", "4080", "4100", "4106", "4109", "4114", "4170"]
