"""Nebula-side reconciliation tooling for the Resolution envelope.

This package holds the *builder-owned* side of the promotion identity work:
the deterministic inventory of duplicate-history sets that require
reconciliation, and the append-only reconciliation log that records the
disposition of every member of every set.

It is deliberately DB-shape agnostic. ``duplicate_history`` operates on
plain Python structures so the detectors are unit-testable without a
database; ``collect`` is the only module that talks to PostgreSQL, and it
does so read-only. Nothing in this package writes to a canonical table —
the reconciliation ledger lives in ``resolution`` (V205) and is written
through the DBA lane.

Scope (plan 8261653, docket thread c7c26160-5b22-463f-9ee0-6e4784bae0e8):

  * W-A — duplicate-history reconciliation.
  * The PC7 JSONB classification (DBA record e8faa405) and the envelope
    freeze conditions C1-C4 are the *sources* of the inventory: each
    condition names a surface whose identity/lineage discipline is what
    makes a duplicate set detectable (or not) in the first place.

Layers:
    duplicate_history  — pure detectors + the set/member model
    collect            — read-only PostgreSQL + filesystem collectors
    reconciliation_log — append-only, hash-chained disposition log
    cli                — `python3 -m nebula.cli inventory|reconcile`
"""

__all__ = ["duplicate_history", "collect", "reconciliation_log", "cli"]

__version__ = "0.1.0"
