#!/usr/bin/env python3
"""continuity.persist — step-4 pre-stage: digest → session_context_snapshot.

Wires the adoption-time digest (continuity.digest) to the V167 pre-stage
surface ``nebula.session_context_snapshots`` (merged #271, DORMANT — the
table does not exist on the live database until the roundtable answers
Q1/Q2 and V167 is applied). This module ships now and stays **inert by
table detection**: ``to_regclass('nebula.session_context_snapshots')``
returns NULL on live today, so every call short-circuits with an explicit
reason. When V167 is applied post-ratification, persistence activates with
zero further code changes — there is no separate switch to flip or forget.

Design positions (per the continuity thread and the V167 migration header):

* **Adoption-gated.** Persistence happens only for BOUND digests —
  ``lease_binding.lease_ref`` non-null and ``role_agreement`` not false.
  Leased snapshots are the attestable artifact class (scope and provenance
  ride with the content); unleased digests are context blobs and are not
  persisted in v0.1. The unbound case is skipped with an explicit reason,
  never silently.
* **Inert by table detection.** No snapshot table → ``{persisted: false,
  reason: ...}``. Missing table cannot fail the caller.
* **Manifest built FROM the digest's own sources** (refs-only): inbox
  record ids, thread ids, record ids, plus the level ceiling, surface
  list, digest version, and disposition. This satisfies V167's
  attestable-artifact rule (SNAP003: leased ⇒ manifest required)
  structurally — there is no conforming leased write this module can
  attempt without a manifest.
* **Provenance in source_records** — [{record_id, as_of}] pairs for every
  canonical record the digest drew from (inbox + recent records).
* **Liveness snapshot in the manifest** — expires_at is stamped into the
  manifest at write time, so the artifact records the scope it was
  written under even after the lease expires.
* **Never raises on the persistence path** — every failure mode returns a
  result dict; the boot shim degrades on non-persistence failures only.
* **Audit piggybacks** — V167's statement triggers write NEBULA_AUDIT
  rows on INSERT; this module adds nothing beyond the row itself.

Result shape::

    {
      "persisted": bool,
      "reason": str,                  # always present
      "snapshot_id": str | None,      # set when persisted
      "skipped" | "error" reason codes are plain strings
    }
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

SNAPSHOT_TABLE = "nebula.session_context_snapshots"
DEFAULT_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql",
                "-U", "pguser", "-d", "nexus", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"]


def _psql_args() -> list:
    """House docker-psql path (chat_store / lease_check convention),
    overridable for tests and non-docker deployments."""
    return [a for a in os.environ.get(
        "CONTINUITY_PSQL_ARGS", "").split("\x1f") if a] or list(DEFAULT_PSQL)


def table_exists(exec_fn) -> bool:
    """True when the V167 surface exists (to_regclass probe)."""
    out = exec_fn("SELECT to_regclass('nebula.session_context_snapshots');")
    return bool(out and out.strip() and out.strip() != "")


def build_manifest(digest: Dict[str, Any]) -> Dict[str, Any]:
    """Read-set manifest from the digest's own sources (refs-only).

    No record content, no thread bodies — ids and provenance metadata
    only, mirroring the digest's altitude-safety stance (the manifest
    describes WHAT was read, never WHAT IT SAID).
    """
    lb = digest.get("lease_binding") or {}
    level = digest.get("level_provenance") or {}
    inbox = digest.get("open_inbox") or []
    threads = digest.get("open_threads") or []
    records = digest.get("recent_records_metadata") or []
    return {
        "digest_version": digest.get("digest_version"),
        "disposition": digest.get("disposition"),
        "surfaces": ["nebula.agent_records(to:role)", "assembly.to-do-threads",
                     "nebula.agent_records(role-own)", "nebula.role-leases"],
        "level_filter_allowed": level.get("level_filter_allowed"),
        "level_applied_at": level.get("applied_at"),
        "sources": {
            "inbox_record_ids": [i.get("record_id") for i in inbox
                                 if i.get("record_id")],
            "thread_ids": [t.get("thread_id") for t in threads
                           if t.get("thread_id")],
            "record_ids": [r.get("record_id") for r in records
                           if r.get("record_id")],
        },
        "lease": {
            "lease_ref": lb.get("lease_ref"),
            "lease_role": lb.get("lease_role"),
            "lease_model": lb.get("lease_model"),
            "expires_at": lb.get("expires_at"),  # liveness stamped at write time
        },
    }


def build_source_records(digest: Dict[str, Any]) -> list:
    """[{record_id, as_of}] provenance rows for every canonical record used."""
    as_of = digest.get("as_of")
    out = []
    for i in (digest.get("open_inbox") or []):
        if i.get("record_id"):
            out.append({"record_id": i["record_id"], "as_of": as_of})
    for r in (digest.get("recent_records_metadata") or []):
        if r.get("record_id"):
            out.append({"record_id": r["record_id"], "as_of": as_of})
    return out


def persist_digest(
    digest: Dict[str, Any],
    exec_fn=None,
) -> Dict[str, Any]:
    """Persist a bound digest as a session_context_snapshot. Never raises.

    ``exec_fn(sql) -> str`` is injectable for tests (runs one SQL statement
    via psql, returns stdout). Defaults to the house docker-psql path.
    """
    if exec_fn is None:
        def exec_fn(sql: str) -> str:  # noqa: F811 — default over the house path
            r = subprocess.run(_psql_args(), input=sql, capture_output=True,
                               text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "psql error").strip()[:300])
            return r.stdout.strip()

    role = (digest.get("role") or "").strip()
    if not role:
        return {"persisted": False, "reason": "digest has no role — cannot attribute",
                "snapshot_id": None}

    lb = digest.get("lease_binding") or {}
    lease_ref = lb.get("lease_ref")
    if lease_ref is None or lb.get("role_agreement") is False:
        return {
            "persisted": False,
            "reason": ("lease role mismatch (binding refused) — not persisted"
                       if lb.get("role_agreement") is False else
                       "unbound digest (no live lease) — persistence is "
                       "adoption-gated; the preview remains the v0 artifact"),
            "snapshot_id": None,
        }

    try:
        if not table_exists(exec_fn):
            return {
                "persisted": False,
                "reason": ("V167 pre-stage not applied (roundtable Q1/Q2 pending) — "
                           "inert gate: no nebula.session_context_snapshots table"),
                "snapshot_id": None,
            }

        manifest = build_manifest(digest)
        source_records = build_source_records(digest)
        payload = {
            "digest_version": digest.get("digest_version"),
            "disposition": digest.get("disposition"),
            "as_of": digest.get("as_of"),
            "counts": digest.get("counts"),
            "open_inbox": digest.get("open_inbox") or [],
            "open_threads": digest.get("open_threads") or [],
            "recent_records_metadata": digest.get("recent_records_metadata") or [],
            "level_provenance": digest.get("level_provenance") or {},
        }
        level = digest.get("level_provenance") or {}

        def _j(v):
            return json.dumps(v) if v is not None else None

        snapshot_id = exec_fn(
            "WITH ins AS ("
            " INSERT INTO nebula.session_context_snapshots"
            " (role, model, lease_ref, read_set_manifest, source_records,"
            "  digest_payload, level_filter_primary, level_filter_allowed, as_of)"
            " VALUES ("
            f" '{_lit(role)}'"
            f", '{_lit(digest.get('assembled_for_model') or '')}'"
            f", '{_lit(str(lease_ref))}'::uuid"
            f", '{_lit(_j(manifest))}'::jsonb"
            f", '{_lit(_j(source_records))}'::jsonb"
            f", '{_lit(_j(payload))}'::jsonb"
            f", '{_lit(level.get('level_filter_allowed'))}'"
            f", '{_lit(level.get('level_filter_allowed'))}'"
            f", '{_lit(digest.get('as_of'))}'::timestamptz"
            " ) RETURNING snapshot_id::text"
            ") SELECT snapshot_id FROM ins;"
        )
        return {
            "persisted": True,
            "reason": "snapshot persisted (leased, manifest present — attestable artifact)",
            "snapshot_id": (snapshot_id or "").strip() or None,
        }
    except Exception as e:  # noqa: BLE001 — persistence path never raises
        text = str(e)
        if "42P01" in text or ("does not exist" in text
                                and "session_context_snapshots" in text):
            # undefined_table (SQLSTATE 42P01): the surface vanished between
            # the probe and the INSERT (or the probe raced an apply/rollback).
            # Classify as the inert gate, not a generic error.
            return {
                "persisted": False,
                "reason": ("V167 pre-stage not applied (roundtable Q1/Q2 pending) — "
                           "inert gate: nebula.session_context_snapshots absent"),
                "snapshot_id": None,
            }
        return {"persisted": False,
                "reason": f"persistence error (inert, caller continues): {e}",
                "snapshot_id": None}


def _lit(v: Optional[str]) -> str:
    """SQL literal escaping for the single-quote path (psql -c style)."""
    if v is None:
        return ""
    return str(v).replace("'", "''")
