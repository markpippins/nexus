"""Guards for bin/ci/local-pg-env.sh — the PostgreSQL bin/tests runs against.

The DB tier builds a throwaway database per fixture with `createdb -w`, and
several guards create a `test_lifecycle_*` schema on their own connection. On a
developer machine both used to land in the SHARED `nexus` database: 79 fixtures
failed with "fe_sendauth: no password supplied", three guards aborted
collection with exit 2, and successful runs left test schemas behind next to
95 live service connections. CI never does that — it seeds a throwaway
database from sql/ci-bootstrap and points both DSNs at it.

These tests drive the helper against a stub `psql`/`createdb`, so they are
hermetic: no server, no credentials, same answer on a CI runner. What they pin
is the part that matters — the helper provisions a DISPOSABLE database, seeds
it from the repo snapshot before anyone connects, never falls back to `nexus`,
and refuses to hand the guards an environment it has not proven.
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
BOOTSTRAP = REPO_ROOT / "sql" / "ci-bootstrap" / "nexus-ci-bootstrap.sql"
ROLE_SEED = REPO_ROOT / "sql" / "ci-bootstrap" / "tackle_roles_vintage_seed.sql"

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
    "NEXUS_PG_DSN",
    "PGCONNECT_TIMEOUT",
    "GUARD_PGDATABASE",
)


def _stub(tmp_path: pathlib.Path, *, createdb_right: str = "t",
          db_exists: int = 1, seeded: int = 1, probe_rc: int = 0,
          probe_err: str = "", seed_rc: int = 0) -> pathlib.Path:
    """A psql/createdb pair that answers from the flags and logs every call."""
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    marker = tmp_path / "seed-marker"
    db_present = tmp_path / "db-present"
    if db_exists:
        db_present.write_text("")

    psql = bindir / "psql"
    psql.write_text(
        "#!/usr/bin/env bash\n"
        'input="$(cat)"\n'
        f'printf \'psql %s <<<%s\\n\' "$*" "$input" >> "{log}"\n'
        f"[ {probe_rc} -ne 0 ] && {{ printf '%s\\n' '{probe_err}' >&2; exit {probe_rc}; }}\n"
        'case "$* $input" in\n'
        f"  *rolcreatedb*) printf '%s\\n' '{createdb_right}' ;;\n"
        f"  *'drop database if exists'*) rm -f '{db_present}' ;;\n"
        f"  *'from pg_database'*) [ -f '{db_present}' ] && printf '1\\n' ;;\n"
        f"  *'bin_tests_seed (source)'*) : > '{marker}' ;;\n"
        f"  *to_regclass*) if [ -f '{marker}' ] || [ {seeded} -eq 1 ]; then printf 't\\n'; else printf 'f\\n'; fi ;;\n"
        f"  *'-f '*) [ {seed_rc} -ne 0 ] && {{ printf 'psql: error: seed failed\\n' >&2; exit {seed_rc}; }} ;;\n"
        "  *) : ;;\n"
        "esac\n"
        "exit 0\n"
    )
    psql.chmod(psql.stat().st_mode | stat.S_IXUSR)

    createdb = bindir / "createdb"
    createdb.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'createdb %s\\n\' "$*" >> "{log}"\n'
        f": > '{db_present}'\n"
        "exit 0\n"
    )
    createdb.chmod(createdb.stat().st_mode | stat.S_IXUSR)
    return log


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


def test_it_exports_the_disposable_database_not_the_live_one(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "export PGDATABASE=nexus_guards" in r.stdout
    assert (
        "export CONDUIT_PG_DSN=postgresql://pguser:pgpass@127.0.0.1:5432/nexus_guards"
        in r.stdout
    )
    # Both conventions the guards read must name the same database.
    assert (
        "export NEXUS_PG_DSN=postgresql://pguser:pgpass@127.0.0.1:5432/nexus_guards"
        in r.stdout
    )
    assert "=nexus\n" not in r.stdout, "the live database must never be exported"


def test_a_stray_exported_pgdatabase_cannot_pull_guards_back_to_nexus(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path, PGDATABASE="nexus")
    assert r.returncode == 0, r.stderr
    assert "export PGDATABASE=nexus_guards" in r.stdout
    assert "PGDATABASE=nexus\n" not in r.stdout


def test_a_missing_scratch_database_is_created_and_seeded_before_use(tmp_path):
    log = _stub(tmp_path, db_exists=0, seeded=0)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "createdb -w nexus_guards" in calls
    assert f"-f {BOOTSTRAP}" in calls
    assert f"-f {ROLE_SEED}" in calls
    # The marker is written only after both seeds succeeded.
    assert "bin_tests_seed" in calls
    assert calls.index(f"-f {ROLE_SEED}") < calls.index("bin_tests_seed")


def test_an_empty_but_existing_database_is_seeded_not_reused(tmp_path):
    log = _stub(tmp_path, db_exists=1, seeded=0)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "createdb -w" not in calls, "an existing database must not be recreated"
    assert f"-f {BOOTSTRAP}" in calls


def test_a_seeded_database_is_reused_without_reseeding(tmp_path):
    log = _stub(tmp_path, db_exists=1, seeded=1)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "createdb -w" not in log.read_text()
    assert "-f " not in log.read_text()


def test_a_failed_seed_fails_loudly_instead_of_exporting_a_half_built_db(tmp_path):
    _stub(tmp_path, db_exists=0, seeded=0, seed_rc=1)
    r = _run(tmp_path)
    assert r.returncode == 5
    assert r.stdout == "", "no environment may be offered when the seed failed"
    assert "seed failed" in r.stderr
    assert "--reset" in r.stderr


def test_reset_drops_and_rebuilds_the_scratch_database(tmp_path):
    log = _stub(tmp_path, db_exists=1, seeded=1)
    r = _run(tmp_path, "--reset")
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert 'drop database if exists "nexus_guards"' in calls
    assert "createdb -w nexus_guards" in calls
    assert f"-f {BOOTSTRAP}" in calls


def test_reset_refuses_to_drop_a_database_it_does_not_own(tmp_path):
    _stub(tmp_path)
    r = _run(
        tmp_path,
        "--reset",
        CONDUIT_PG_DSN="postgresql://pguser:pgpass@127.0.0.1:5432/nexus",
    )
    assert r.returncode == 5
    assert r.stdout == ""
    assert "nothing here to drop" in r.stderr


def test_an_explicit_dsn_is_used_verbatim_and_provisions_nothing(tmp_path):
    log = _stub(tmp_path)
    dsn = "host=127.0.0.1 port=5432 user=pguser password=pgpass dbname=somewhere"
    r = _run(tmp_path, CONDUIT_PG_DSN=dsn)
    assert r.returncode == 0, r.stderr
    # %q quoting escapes the spaces; a shell would unescape them.
    unescaped = r.stdout.replace("\\ ", " ")
    assert f"export CONDUIT_PG_DSN={dsn}" in unescaped
    assert "export PGDATABASE=somewhere" in r.stdout
    calls = log.read_text()
    assert "createdb -w" not in calls
    assert "-f " not in calls


def test_a_refused_password_names_the_probe_instead_of_passing_silently(tmp_path):
    _stub(
        tmp_path,
        probe_rc=2,
        probe_err='psql: error: connection to server at "127.0.0.1", port 5432 '
                  "failed: fe_sendauth: no password supplied",
    )
    r = _run(tmp_path)
    assert r.returncode == 3
    assert r.stdout == "", "no environment may be offered when the probe failed"
    assert "fe_sendauth: no password supplied" in r.stderr
    assert "127.0.0.1:5432 as pguser" in r.stderr


def test_a_role_without_createdb_is_refused_before_the_fixtures_trip(tmp_path):
    _stub(tmp_path, createdb_right="f")
    r = _run(tmp_path)
    assert r.returncode == 4
    assert r.stdout == ""
    assert "may not CREATE DATABASE" in r.stderr
    assert "pguser" in r.stderr


def test_exec_form_runs_the_command_with_the_environment_applied(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path, "bash", "-c", 'printf %s "$CONDUIT_PG_DSN"')
    assert r.returncode == 0, r.stderr
    assert r.stdout == "postgresql://pguser:pgpass@127.0.0.1:5432/nexus_guards"


def test_an_already_exported_password_wins_and_is_verified_not_replaced(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path, PGPASSWORD="s3cret")
    assert r.returncode == 0, r.stderr
    assert "export PGPASSWORD=s3cret" in r.stdout
    assert "pgpass" not in r.stdout


def test_help_prints_the_usage_block_not_the_script_source(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path, "--help")
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "set -euo pipefail" not in r.stdout, "the comment block outgrew its range"


def test_check_mode_is_silent_on_success(tmp_path):
    _stub(tmp_path)
    r = _run(tmp_path, "--check")
    assert r.returncode == 0
    assert r.stdout == ""
