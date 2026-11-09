"""SQL side of the parity guard — ``resolution.evaluate_proposition`` (v32).

The gate under test is *not* reimplemented. The guard extracts the
``CREATE OR REPLACE FUNCTION resolution.evaluate_proposition(...)`` statement
verbatim from the pinned migration (``resolution_migration_v32.sql``), installs
it into a throwaway PostgreSQL database together with the minimal table
scaffolding the function touches, runs the shared matrix, and drops the
database again.

Guarantees:

* The throwaway database name is generated here and always carries the
  ``nexus_framing_parity_`` prefix; the guard refuses to target ``nexus`` (or
  ``postgres``) and never writes to the live database.
* No migration is applied to any existing database; the only migration text
  read is the pinned file, and only its gate function is used.
* The database is dropped in a ``finally`` block, including on failure.

Connection parameters come from the standard ``PG*`` environment variables
(``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD``/``~/.pgpass``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .canonical import Outcome, classify_error
from .matrix import DIMENSIONS, FIXTURE, MATRIX, Case

RUNTIME = "sql"

GATE_MIGRATION = "schemas/migrations/resolution/resolution_migration_v32.sql"
DB_PREFIX = "nexus_framing_parity_"
_UUID_NAMESPACE = uuid.UUID("6f6f6f6f-0000-4000-8000-000000000000")

_GATE_PATTERN = re.compile(
    r"CREATE OR REPLACE FUNCTION resolution\.evaluate_proposition\(.*?\$function\$;",
    re.DOTALL,
)

#: Minimal facsimile of the resolution tables the v32 function touches.
#: Deliberately not a copy of the schema: the guard is proving the *gate*, and
#: the gate never reads assertion data in the matrix (propositions carry no
#: assertions). The tables exist only so the function body can run to the end.
_SCAFFOLD_DDL = """
CREATE SCHEMA resolution;

CREATE TABLE resolution.concept (
    id   uuid PRIMARY KEY,
    name text
);

CREATE TABLE resolution.concept_attribute (
    id         uuid PRIMARY KEY,
    name       text,
    concept_id uuid
);

CREATE TABLE resolution.concept_attribute_value (
    id           uuid PRIMARY KEY,
    attribute_id uuid,
    value        text
);

CREATE TABLE resolution.frame_dimension (
    id          uuid PRIMARY KEY,
    name        text NOT NULL,
    value_kind  text NOT NULL,
    scalar_type text
);

CREATE TABLE resolution.frame_dimension_value (
    id           uuid PRIMARY KEY,
    dimension_id uuid,
    value        text
);

CREATE TABLE resolution.proposition_frame_value (
    id                 uuid PRIMARY KEY,
    proposition_id     uuid,
    dimension_id       uuid,
    reference_value_id uuid,
    scalar_value       text
);

CREATE TABLE resolution.proposition (
    id                   uuid PRIMARY KEY,
    subject_entity_id    uuid,
    disposition_value_id uuid,
    last_evaluated_at    timestamptz
);

CREATE TABLE resolution.proposition_assertion (
    proposition_id uuid,
    rule_id        uuid
);

CREATE TABLE resolution.rule (
    id                  uuid PRIMARY KEY,
    expression_id       uuid,
    is_relational_check boolean DEFAULT false
);

CREATE TABLE resolution.assertion_evaluation (
    id             bigserial PRIMARY KEY,
    proposition_id uuid,
    rule_id        uuid,
    result         boolean,
    compiled_sql   text,
    trigger_reason text
);

CREATE FUNCTION resolution.evaluate_relationship_guard(
    p_expression_id uuid,
    p_subject_entity_id uuid
)
RETURNS TABLE(result boolean, compiled_sql text)
LANGUAGE sql
AS $guard$ SELECT true, NULL::text $guard$;
"""


class SqlHarnessError(RuntimeError):
    """The SQL harness could not be set up or produced an unexpected failure."""


class SqlToolingUnavailable(SqlHarnessError):
    """``psql`` missing or the server unreachable — SQL cannot be attested."""


def extract_gate_sql(migration_text: str) -> str:
    """Return the verbatim v32 ``evaluate_proposition`` definition."""
    matches = _GATE_PATTERN.findall(migration_text)
    if len(matches) != 1:
        raise SqlHarnessError(
            "expected exactly one CREATE OR REPLACE FUNCTION "
            f"resolution.evaluate_proposition in {GATE_MIGRATION}, got {len(matches)}"
        )
    return matches[0]


def load_gate_sql(repo_root: Path) -> str:
    migration = repo_root / GATE_MIGRATION
    if not migration.is_file():
        raise SqlHarnessError(f"missing pinned SQL migration: {migration}")
    return extract_gate_sql(migration.read_text(encoding="utf-8"))


def _psql_env() -> Dict[str, str]:
    """Standard ``PG*`` environment with the repo's local-dev defaults.

    ``PGHOST``/``PGPORT``/``PGUSER`` default to the documented local development
    endpoint (``localhost:5432``/``pguser``) so the guard does not silently try
    the Unix socket. Credentials are never defaulted: supply ``PGPASSWORD`` (or
    ``~/.pgpass``) the same way the repo's migration runbooks do.
    """
    env = dict(os.environ)
    env.setdefault("PGHOST", "localhost")
    env.setdefault("PGPORT", "5432")
    env.setdefault("PGUSER", "pguser")
    env.setdefault("PGCONNECT_TIMEOUT", "5")
    return env


def _psql(
    args: Iterable[str],
    *,
    stdin_text: Optional[str] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ("psql", *args),
        input=stdin_text,
        capture_output=True,
        text=True,
        env=_psql_env(),
        timeout=timeout,
    )
    return completed


def _seed_sql() -> str:
    statements: List[str] = []
    statements.append(
        "INSERT INTO resolution.frame_dimension(id, name, value_kind, scalar_type) VALUES\n"
        + ",\n".join(
            "('{}', '{}', '{}', {})".format(
                spec["id"],
                name.replace("'", "''"),
                spec["value_kind"],
                "NULL" if spec["scalar_type"] is None else f"'{spec['scalar_type']}'",
            )
            for name, spec in DIMENSIONS.items()
        )
        + ";"
    )
    values = []
    for spec in DIMENSIONS.values():
        for value_name, value_id in spec["values"].items():
            values.append(
                "('{}', '{}', '{}')".format(
                    value_id, spec["id"], value_name.replace("'", "''")
                )
            )
    statements.append(
        "INSERT INTO resolution.frame_dimension_value(id, dimension_id, value) VALUES\n"
        + ",\n".join(values)
        + ";"
    )
    return "\n".join(statements)


def _case_proposition_id(case_id: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, case_id))


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class ThrowawayDatabase:
    """A throwaway PostgreSQL database that always drops itself."""

    def __init__(self, gate_sql: str, keep_database: bool = False) -> None:
        self.name = f"{DB_PREFIX}{uuid.uuid4().hex[:12]}"
        self._gate_sql = gate_sql
        self._keep = keep_database
        self._created = False

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "ThrowawayDatabase":
        if not self.name.startswith(DB_PREFIX):  # pragma: no cover - defensive
            raise SqlHarnessError(f"refusing unsafe throwaway database name: {self.name}")
        completed = _psql(("-d", "postgres", "-q", "-c", f'CREATE DATABASE "{self.name}"'))
        if completed.returncode != 0:
            raise SqlToolingUnavailable(
                "could not create the throwaway parity database — is PostgreSQL "
                f"reachable via the PG* environment? psql said: {completed.stderr.strip()}"
            )
        self._created = True
        try:
            self._install()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._created:
            return
        if self._keep:
            return
        _psql(("-d", "postgres", "-q", "-c", f'DROP DATABASE IF EXISTS "{self.name}"'))
        self._created = False

    # -- setup -------------------------------------------------------------
    def _install(self) -> None:
        ddl = _SCAFFOLD_DDL + "\n" + self._gate_sql + "\n" + _seed_sql() + "\n"
        completed = _psql(
            ("-d", self.name, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"),
            stdin_text=ddl,
        )
        if completed.returncode != 0:
            raise SqlHarnessError(
                "failed to install the pinned v32 gate in the throwaway database:\n"
                f"{completed.stderr.strip()}"
            )

    # -- execution ---------------------------------------------------------
    def run_case(self, case: Case) -> Outcome:
        proposition_id = _case_proposition_id(case.id)

        setup = [
            "DELETE FROM resolution.assertion_evaluation;",
            "DELETE FROM resolution.proposition_frame_value;",
            "DELETE FROM resolution.proposition;",
            "INSERT INTO resolution.proposition(id, subject_entity_id) VALUES "
            f"({_sql_literal(proposition_id)}::uuid, {_sql_literal(FIXTURE['entity_id'])}::uuid);",
        ]
        for index, frame in enumerate(case.instance_frames):
            spec = DIMENSIONS[frame.dimension]
            reference_value_id = spec["values"][frame.reference_value]
            row_id = str(uuid.uuid5(_UUID_NAMESPACE, f"{case.id}:pfv:{index}"))
            setup.append(
                "INSERT INTO resolution.proposition_frame_value"
                "(id, proposition_id, dimension_id, reference_value_id) VALUES "
                f"({_sql_literal(row_id)}::uuid, {_sql_literal(proposition_id)}::uuid, "
                f"{_sql_literal(spec['id'])}::uuid, {_sql_literal(reference_value_id)}::uuid);"
            )
        prepared = _psql(
            ("-d", self.name, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"),
            stdin_text="\n".join(setup) + "\n",
        )
        if prepared.returncode != 0:
            raise SqlHarnessError(
                f"could not stage matrix case {case.id!r} in the throwaway database:\n"
                f"{prepared.stderr.strip()}"
            )

        context_sql = (
            "NULL"
            if case.context is None
            else _sql_literal(json.dumps(dict(case.context), sort_keys=True)) + "::jsonb"
        )
        call = (
            "SELECT * FROM resolution.evaluate_proposition("
            f"p_proposition_id => {_sql_literal(proposition_id)}::uuid, "
            "p_trigger_reason => 'framing_parity', "
            f"p_context => {context_sql});"
        )
        completed = _psql(("-d", self.name, "-tA", "-F", "|", "-c", call))
        if completed.returncode != 0:
            error_class = classify_error(completed.stderr)
            if error_class != "unclassified_error":
                return Outcome(
                    error=error_class, detail=completed.stderr.strip().splitlines()[0]
                )
            raise SqlHarnessError(
                f"unexpected SQL failure for matrix case {case.id!r}:\n"
                f"{completed.stderr.strip()}"
            )

        lines = [line for line in completed.stdout.splitlines() if line]
        if len(lines) != 1:
            raise SqlHarnessError(
                f"expected exactly one row from evaluate_proposition for case "
                f"{case.id!r}, got {completed.stdout!r}"
            )
        columns = lines[0].split("|")
        if len(columns) != 3:
            raise SqlHarnessError(
                f"expected 3 columns from evaluate_proposition for case "
                f"{case.id!r}, got {columns!r}"
            )
        disposition, all_passed, context_status = columns
        return Outcome(
            disposition=disposition or None,
            all_passed=all_passed == "t",
            context_status=context_status or None,
        )


def run_matrix(
    repo_root: Path,
    cases=MATRIX,
    keep_database: bool = False,
) -> Dict[str, Outcome]:
    """Run the shared matrix against the pinned v32 gate in a throwaway DB."""
    if not shutil.which("psql"):
        raise SqlToolingUnavailable("`psql` not found on PATH")

    gate_sql = load_gate_sql(repo_root)
    with ThrowawayDatabase(gate_sql, keep_database=keep_database) as database:
        return {case.id: database.run_case(case) for case in cases}
