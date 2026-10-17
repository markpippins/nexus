"""Every wrapper that files records must pass a role the recorder accepts.

Why this guard exists: on 2026-10-05 the canonical role vocabulary was
normalized to lowercase (`dba`, per Ruling 8 / V203 / V204). Five guard
wrappers in bin/ still passed `"-r", "DBA"`, and `bin/post-agent-record.py`
validates that argument against `config/roles/roles.json` case-sensitively.

The wrappers therefore filed nothing from that day on. The damage is not the
records that went missing — it is that a guard which *cannot file* is
indistinguishable from a guard with *nothing to say*:

  - `pg-logging-check.service` failed on 2026-10-17 (ExecMainStatus=2,
    journal: "findings record filed: False") — it had findings and could not
    report them, after 12 days of the same.
  - `record-durability` and `users-bcrypt-drift` exited 0 throughout, because
    they were in a green phase and never exercised the filing path. Their
    guards looked healthy while their alerting was dead.

A green heartbeat is only evidence of health if the heartbeat can be written.
This guard makes the role argument itself checkable, using the recorder's own
validator as the single source of truth rather than a copied list.

Run:
  python3 -m pytest bin/tests/test_guard_wrapper_roles.py -v
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BIN = REPO_ROOT / "bin"
POSTER = BIN / "post-agent-record.py"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


POSTER_MOD = _load("post_agent_record_for_role_guard", POSTER)

# `-r X` / `--role X` with X either a quoted literal or an identifier.
_ROLE_ARG = re.compile(
    r"""["'](?:-r|--role)["']\s*,\s*(?P<value>"[^"]*"|'[^']*'|[A-Za-z_][A-Za-z0-9_]*)""")
_ASSIGN = re.compile(
    r"""^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>"[^"]*"|'[^']*')""",
    re.MULTILINE)


def _candidate_files() -> list[pathlib.Path]:
    """bin/ scripts that actually invoke the recorder (tests excluded).

    The recorder itself is excluded: it defines `-r/--role` for argparse and
    never passes a role to a subprocess, so scanning it would flag its own
    option registration as a bad role.
    """
    out = []
    for path in sorted(BIN.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == POSTER.name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "post-agent-record" in text or "post_agent_record" in text:
            out.append(path)
    return out


def _roles_in(path: pathlib.Path) -> list[tuple[int, str]]:
    """(line number, role) for every -r/--role argument in the file.

    Quoted literals are returned directly. An identifier is resolved against
    the module-level constants of the same file, because the wrappers keep
    their role in a named constant.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    constants = {m.group("name"): m.group("value").strip("\"'")
                 for m in _ASSIGN.finditer(text)}
    found = []
    for m in _ROLE_ARG.finditer(text):
        raw = m.group("value")
        if raw.startswith(("'", '"')):
            role = raw.strip("'\"")
        else:
            role = constants.get(raw, "")
        # `-r "--role"` is an argparse option registration, not a role.
        if role and not role.startswith("-"):
            found.append((text[:m.start()].count("\n") + 1, role))
    return found


def test_candidates_are_found():
    """Guard against a vacuous pass: if discovery breaks, fail loudly."""
    files = _candidate_files()
    assert len(files) >= 3, f"expected several recorder callers, got {files}"
    names = {p.name for p in files}
    assert "pg_logging_check_wrap.py" in names
    assert "pg_wal_guard_wrap.py" in names


def test_validator_source_of_truth_is_lowercase():
    """The recorder's accepted set is the canonical vocabulary; if it ever
    contains a mixed-case role, this guard's premise is wrong."""
    roles = POSTER_MOD._writable_roles()
    assert roles, "recorder returned an empty role set"
    assert all(r == r.lower() for r in roles), sorted(roles)
    assert "dba" in roles


@pytest.mark.parametrize("path", _candidate_files(),
                         ids=lambda p: p.name)
def test_every_recorder_role_argument_is_accepted(path):
    """Each role passed must survive the recorder's own validation."""
    valid = POSTER_MOD._writable_roles()
    roles = _roles_in(path)
    assert roles, (f"{path.name} mentions the recorder but passes no "
                   "resolvable -r/--role; the check cannot see it")
    for line, role in roles:
        assert role in valid, (
            f"{path.name}:{line} passes role {role!r}, which "
            f"bin/post-agent-record.py rejects (valid: {sorted(valid)}). "
            "A wrapper whose record is refused files NOTHING, and a guard "
            "that cannot file looks identical to a guard with nothing to "
            "say — this is the 2026-10-05..17 outage.")


def test_no_uppercase_role_anywhere_in_the_wrappers():
    """The specific regression, stated directly so the failure is legible."""
    offenders = []
    for path in _candidate_files():
        for line, role in _roles_in(path):
            if role != role.lower():
                offenders.append(f"{path.name}:{line} -> {role!r}")
    assert offenders == [], (
        "role vocabulary is lowercase end to end; mixed-case role arguments "
        f"silently disable record filing: {offenders}")
