"""Ruling 32 §4 — cross-runtime framing parity guard (shadow-only).

Proves that the E8.4 framing gate agrees across three independent
implementations over one shared matrix:

* Python  — ``python/SOLScript/solscript/interpreter.py`` (type-level
  required dimensions, post-#916).
* SQL     — ``resolution.evaluate_proposition(uuid, text, jsonb)`` as created
  by ``schemas/migrations/resolution/resolution_migration_v32.sql`` (framed-only
  gate), with the later migrations v33-v37 pinned as non-altering.
* TypeScript — ``typescript/solscript/src/interpreter.ts`` (instance
  frame-count gate).

This package is **shadow-only**: it never writes to the live nexus database,
never applies a migration, and never changes admission or production
behaviour. Python and TypeScript run entirely in memory; SQL-shaped checks
run against a throwaway database that is created and dropped by the guard.

Known, intentional divergences are pinned in :mod:`solscript_framing_parity.matrix`
and referenced by the follow-up identifiers in ``FOLLOW_UPS``; they are
asserted explicitly rather than averaged away. A guard run fails if the
observed behaviour of any runtime drifts from the pinned parity contract,
or if any of the pinned interpreter/migration files change.

Entry point::

    PYTHONPATH=python python3 -m solscript_framing_parity
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
