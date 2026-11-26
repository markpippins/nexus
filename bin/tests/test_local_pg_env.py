"""Guards for bin/ci/local-pg-env.sh — the PostgreSQL bin/tests needs locally.

The DB tier builds a throwaway database per fixture with `createdb -w`. On a
developer machine whose PostgreSQL was already running, those calls failed 79
fixtures with "fe_sendauth: no password supplied" and three guards aborted
collection with exit 2, so the guard suite reported nothing about the code.

These tests drive the helper against a stub `psql`, so they are hermetic: no
server, no credentials, same answer on a CI runner. What they pin is the part
that matters — the helper refuses to hand the guards an environment it has not
proven, and it says which address and which capability failed.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

SCRIPT = REPO_ROOT / "bin" / "ci" / "local-pg-env.sh"

pytestmark = skip_unless_host_tools(
    "bash", doing="these tests execute the local-PostgreSQL helper"
)

_CLEARED = (
    "PGHOST",
    "PGPORT",
    "PGUSER",
    "PGPASSWORD",
    "PGDATABASE",
    "CONDUIT_PG_DSN",
    "PGCONNECT_TIMEOUT",
)


def _stub_psql(tmp_path: pathlib.Path, *, stdout: str = "t", stderr: str = "",
               rc: int = 0) -> pathlib.Path:
    """A psql that reports `stdout`/`stderr` and exits `rc`; never connects."""
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    emit_err = f"printf '%s\\n' '{stderr}' >&2" if stderr else ":"
    psql = bindir / "psql"
    psql.write_text(
        "#!/usr/bin/env bash\n"
        f"{emit_err}\n"
        f"printf '%s\\n' '{stdout}'\n"
        f"exit {rc}\n"
    )
    psql.chmod(psql.stat().st_mode | stat.S_IXUSR)
    return bindir


def _env(tmp_path: pathlib.Path, **overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PATH"] = f"{tmp_path / 'stub-bin'}{os.pathsep}{env['PATH']}"
    env.update(overrides)
    return env


def _run(tmp_path: pathlib.Path, *args: str, **overrides: str):
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path, **overrides),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_it_exports_the_documented_local_defaults(tmp_path):
    _stub_psql(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "export PGPASSWORD=pgpass" in r.stdout
    assert "export PGDATABASE=nexus" in r.stdout
    assert "export PGPORT=5432" in r.stdout
    assert (
        "export CONDUIT_PG_DSN=postgresql://pguser:pgpass@127.0.0.1:5432/nexus"
        in r.stdout
    )


def test_a_refused_password_names_the_probe_instead_of_passing_silently(tmp_path):
    _stub_psql(tmp_path, stdout="", rc=2,
               stderr='psql: error: connection to server at "127.0.0.1", port 5432 '
                      "failed: fe_sendauth: no password supplied")
    r = _run(tmp_path)
    assert r.returncode == 3
    assert r.stdout == "", "no environment may be offered when the probe failed"
    assert "fe_sendauth: no password supplied" in r.stderr
    assert "127.0.0.1:5432 as pguser" in r.stderr


def test_a_role_without_createdb_is_refused_before_the_fixtures_trip(tmp_path):
    _stub_psql(tmp_path, stdout="f")
    r = _run(tmp_path)
    assert r.returncode == 4
    assert r.stdout == ""
    assert "may not CREATE DATABASE" in r.stderr
    assert "pguser" in r.stderr


def test_exec_form_runs_the_command_with_the_environment_applied(tmp_path):
    _stub_psql(tmp_path)
    r = _run(tmp_path, "bash", "-c", 'printf %s "$CONDUIT_PG_DSN"')
    assert r.returncode == 0, r.stderr
    assert r.stdout == "postgresql://pguser:pgpass@127.0.0.1:5432/nexus"


def test_an_already_exported_password_wins_and_is_verified_not_replaced(tmp_path):
    _stub_psql(tmp_path)
    r = _run(tmp_path, PGPASSWORD="s3cret")
    assert r.returncode == 0, r.stderr
    assert "export PGPASSWORD=s3cret" in r.stdout
    assert "pgpass" not in r.stdout


def test_check_mode_is_silent_on_success(tmp_path):
    _stub_psql(tmp_path)
    r = _run(tmp_path, "--check")
    assert r.returncode == 0
    assert r.stdout == ""
