"""
CIR-SDM enforcement caller (T23 Step 7) — per-family enforcement gate.

This is the *enforcement* side of the shadow-mode doctrine: a thin caller
around :func:`nexus_core.wrp.cir_sdm.evaluate` that decides which rule
families are blocking-eligible. The pure ``evaluate()`` is unchanged — this
module supplies the ``enforced_rules`` set and renders the startup audit
state.

Binding contracts:

* **architect ruling 4a57c089 (2026-08-16)** — initial enforced set
  (``cir-sdm.one-way-gate``), warnings never block, per-family additions ONLY
  via explicit architect decision after a measured zero-FP window, no
  silent-addition path, reject/override only via ``type:decision``.
* **RULING R-D (record 2487aef3, 2026-08-25)** — the enforcement posture is
  CURRENT STATE and persists as a resolution-schema row
  (``resolution.enforcement_posture``, governance_threshold-style: one row
  per family ``{family, mode, authorized_by, effective_from}``). The
  ``CIR_SDM_ENFORCE`` env demotes to bootstrap default ONLY while zero rows
  exist (first boot); once rows exist the database wins. Env-vs-row
  divergence emits a warning. Composition (posture-row resolution, admission
  recording, audit logging) lives HERE in the boundary wrapper —
  ``cir_sdm.py`` stays zero-dependency.

Design (mirrors the zero-dependency rule of ``cir_sdm.py``):

* ``resolve_enforced_rules`` / ``enforce`` stay pure — the environment input
  (``CIR_SDM_ENFORCE`` value) and the posture rows are passed in explicitly
  by the dispatch caller. The module never touches ``os.environ``, keeping it
  cross-kernel-safe and trivially testable.
* ``load_posture_rows`` is the ONLY I/O surface — a thin psql read of
  ``resolution.enforcement_posture`` (active = latest ``effective_from``
  ``<= now()`` per family). Reach: injected ``psql=`` > CONDUIT_PG_DSN >
  legacy docker-exec fallback (see ``_resolve_psql``). It returns ``[]`` on
  any failure so the caller falls back to the bootstrap default rather than
  erroring (R-D R2).
* ``render_enforcement_state`` produces the startup audit line the dispatch
  service logs once at boot (shadow vs enforced + source + active rule set).

Usage (dispatch path)::

    from nexus_core.wrp.enforcement import (
        ENFORCED_RULES_KEY, enforce, load_posture_rows,
        render_enforcement_state, resolve_enforced_rules,
    )

    env_value = os.environ.get(ENFORCED_RULES_KEY)
    posture = load_posture_rows()                     # DB wins once seeded
    log.info(render_enforcement_state(env_value, posture))   # startup audit line
    violations = enforce(events, env_value=env_value, posture_rows=posture)
"""

from __future__ import annotations

import os

from typing import Any, Dict, FrozenSet, Iterable, List, Optional

from nexus_core.wrp.cir_sdm import (
    CIRViolation,
    RULE_ONE_WAY_GATE,
    evaluate,
)

__all__ = [
    "ENFORCED_RULES_KEY",
    "DEFAULT_ENFORCED_RULES",
    "resolve_enforced_rules",
    "enforce",
    "render_enforcement_state",
    "governed_decisions",
    "violation_to_row",
    "load_posture_rows",
    "posture_enforced_rules",
]

# Env flag the enforcement caller reads (never inside the pure evaluate()).
ENFORCED_RULES_KEY = "CIR_SDM_ENFORCE"

# Bootstrap enforced set (architect ruling 4a57c089): one-way-gate is the
# only family with a measured zero-FP-blocking window. Every other family
# stays shadow until its own window + explicit per-family approval. Used ONLY
# while resolution.enforcement_posture has zero rows (R-D R2: env demoted to
# bootstrap default; once rows exist the database wins).
DEFAULT_ENFORCED_RULES: FrozenSet[str] = frozenset({RULE_ONE_WAY_GATE})

# Same host-side psql pathway the promotion gate / peb_admission use to reach
# the nexus DB. Default legacy docker-exec command is a fallback only:
# CONDUIT_PG_DSN is the primary pathway (house convention — timeclock,
# wrp-bridge-daemon, lilac_drift), used when the container is absent (other
# hosts, containerized topologies). Module-level so tests can swap it.
_PSQL: List[str] = [
    "docker", "exec", "-i", "pgvector_db",
    "psql", "-U", "pguser", "-d", "nexus", "-t", "-A", "-q",
]


def _resolve_psql(psql: Optional[List[str]] = None) -> List[str]:
    """Resolve the psql command for a call.

    Order: explicit ``psql=`` injection (tests) wins, then CONDUIT_PG_DSN
    (primary house pathway), then the legacy docker-exec fallback. Pure.
    """
    if psql is not None:
        return psql
    dsn = os.environ.get("CONDUIT_PG_DSN", "").strip()
    if dsn:
        return ["psql", dsn, "-t", "-A", "-q"]
    return list(_PSQL)


def load_posture_rows(
    psql: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Load the active enforcement posture rows from the DB.

    Active = latest ``effective_from <= now()`` per family (immutable-temporal
    semantics, governance_threshold-style). Returns a list of
    ``{"family", "mode", "authorized_by", "effective_from"}`` dicts, or
    ``[]`` on any failure (DB unreachable / table missing) so the caller
    degrades to the bootstrap default rather than erroring (R-D R2). This is
    the ONLY I/O surface in the enforcement layer.
    """
    import subprocess
    cmd = _resolve_psql(psql)
    sql = (
        "SELECT DISTINCT ON (family) family, mode, authorized_by, effective_from "
        "FROM resolution.enforcement_posture "
        "WHERE effective_from <= now() "
        "ORDER BY family, effective_from DESC;"
    )
    try:
        proc = subprocess.run(
            cmd + ["-c", sql], capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL,   # never eat the caller's stdin pipe
        )
        if proc.returncode != 0:
            return []
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not parts[0]:
            continue
        rows.append({
            "family": parts[0],
            "mode": parts[1],
            "authorized_by": parts[2] or None,
            "effective_from": parts[3] if len(parts) > 3 else None,
        })
    return rows


def posture_enforced_rules(
    posture_rows: Iterable[Dict[str, Any]],
) -> FrozenSet[str]:
    """The enforced families from a set of posture rows (DB wins semantics).

    A family is enforced when its active posture row has ``mode='enforced'``
    AND an ``authorized_by`` decision is cited (no silent addition — a row
    without authorization never enforces). Pure.
    """
    return frozenset(
        r["family"] for r in posture_rows
        if r.get("mode") == "enforced" and r.get("authorized_by")
    )


def resolve_enforced_rules(
    env_value: Optional[str],
    posture_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> FrozenSet[str]:
    """Map env + optional posture rows to the enforced-rule set.

    R-D R2 precedence:

    1. **DB wins** — when ``posture_rows`` is non-empty, the enforced set is
       the enforced families from the posture (``authorized_by`` cited).
    2. **Bootstrap** — when zero posture rows exist (first boot), fall back
       to the env/bootstrap default: ``"0"`` → empty (full shadow),
       unset/``"1"``/other → :data:`DEFAULT_ENFORCED_RULES`.

    The dispatch caller reads the env and passes both values in; this
    function stays pure.
    """
    rows = list(posture_rows) if posture_rows else []
    if rows:
        return posture_enforced_rules(rows)
    if env_value is not None and env_value.strip() == "0":
        return frozenset()
    return DEFAULT_ENFORCED_RULES


def enforce(
    events: Iterable[Any],
    *,
    env_value: Optional[str] = None,
    posture_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[CIRViolation]:
    """Evaluate the stream under the resolved per-family enforcement set.

    Thin caller over the pure ``evaluate()``: resolves ``enforced_rules``
    from the supplied env value + posture rows and delegates. Warnings never
    block (``evaluate`` applies ``blocking=True`` only to blocking-severity
    violations whose rule is in the enforced set), so cold-start and
    IR-payload warnings surface non-blocking even in enforced mode.
    """
    return evaluate(
        events,
        enforced_rules=resolve_enforced_rules(env_value, posture_rows),
    )


def render_enforcement_state(
    env_value: Optional[str],
    posture_rows: Optional[Iterable[Dict[str, Any]]] = None,
) -> str:
    """Startup audit line — logs shadow vs enforced, the source, and rules.

    The dispatch service logs this once at boot so the enforcement posture is
    auditable (4a57c089 / R-D R2). Reports ``database`` when posture rows
    exist, ``bootstrap`` otherwise, and emits a WARNING when ``CIR_SDM_ENFORCE``
    is set and diverges from the database posture (R-D R2: DB wins; divergence
    is surfaced, never silently resolved). Pure — no I/O, no wall clock.
    """
    rules = resolve_enforced_rules(env_value, posture_rows)
    rows = list(posture_rows) if posture_rows else []

    if rows:
        divergence = ""
        if env_value is not None:
            env_rules = (
                frozenset() if env_value.strip() == "0"
                else DEFAULT_ENFORCED_RULES
            )
            if env_rules != rules:
                divergence = (
                    f"; WARNING: CIR_SDM_ENFORCE={env_value!r} diverges from "
                    "posture rows — database wins"
                )
        if not rules:
            return (
                "CIR-SDM enforcement: shadow "
                f"(database: resolution.enforcement_posture — nothing blocks){divergence}"
            )
        return (
            "CIR-SDM enforcement: enforced "
            f"(database: resolution.enforcement_posture; "
            f"rules: {', '.join(sorted(rules))}){divergence}"
        )

    if not rules:
        return (
            "CIR-SDM enforcement: shadow "
            "(bootstrap; CIR_SDM_ENFORCE=0 — nothing blocks)"
        )
    return (
        "CIR-SDM enforcement: enforced "
        f"(bootstrap; rules: {', '.join(sorted(rules))})"
    )


def governed_decisions(violations: Iterable[CIRViolation]) -> List[CIRViolation]:
    """The blocking violations an enforcement outcome must reject or record.

    A "governed decision" is a blocking violation (``blocking=True``) — the
    dispatch path either rejects the transition or records the decision in
    ``peb.cir_violations``; it never silently alters canonical state (T23
    Step 8). Warnings and shadow-mode violations are excluded: they surface
    for review at dispatch, never auto-block.
    """
    return [v for v in violations if v.blocking]


def violation_to_row(v: CIRViolation) -> Dict[str, Any]:
    """Map a :class:`CIRViolation` to a ``peb.cir_violations`` row dict.

    Pure and deterministic — no DB, no wall clock. ``detected_at`` is the
    offending event's epoch seconds (``0`` → ``None``, the "unknown"
    sentinel); the persistence caller converts it to a ``TIMESTAMPTZ``.
    ``created_at`` is deliberately unset (housekeeping — DB default ``now()``).
    """
    return {
        "violation_id": v.violation_id,
        "cer_id": v.cer_id,
        "event_id": v.event_id,
        "rule_id": v.rule_id,
        "rule_version": v.rule_version,
        "severity": v.severity,
        "description": v.description,
        "detected_at": v.detected_at or None,
        "blocking": v.blocking,
    }
