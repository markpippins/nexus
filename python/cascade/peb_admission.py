"""Advisory PEB outcome recorder for the governed resolution boundary.

PEB output is evidence only. Resolution owns lifecycle and admission effects;
this module never mutates Conduit, plans, promotion state, or other lifecycle
state. Records are deterministic and duplicate-safe from captured inputs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from typing import List, Optional

try:
    from governance_envelope import binding_idempotency_key, validate_binding_decision
except ImportError:  # legacy standalone invocation
    binding_idempotency_key = None
    validate_binding_decision = None

# Default pathway: docker-exec psql on the container host (unchanged).
_PSQL: List[str] = [
    "docker", "exec", "-i", "pgvector_db",
    "psql", "-U", "pguser", "-d", "nexus", "-t", "-A", "-q",
]
_RECORD_TIMEOUT_S = 4.0


def _psql_cmd() -> List[str]:
    """Resolve the advisory psql command (inspector G1, thread 5d45f921).

    The hardcoded docker-exec default voids the advisory record-then-act
    guarantee anywhere Docker isn't the runtime. When CONDUIT_PG_DSN is set
    (house convention: timeclock/db.py, wrp-bridge-daemon.service), psql is
    invoked directly against that DSN so the advisory insert holds on any
    host that can reach the database. The default docker pathway is
    preserved unchanged for the container host; an explicitly injected
    command list still wins over both.
    """
    dsn = os.environ.get("CONDUIT_PG_DSN", "").strip()
    if dsn:
        return ["psql", "-t", "-A", "-q", dsn]
    return list(_PSQL)


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _advisory_insert(cmd: List[str], *, transaction_id: str, idempotency_key: str,
                     entity_id: str, gate: str, admitted: bool, input_json: str) -> bool:
    disposition = "ADMITTED" if admitted else "REFUSED"
    sql = (
        "INSERT INTO peb.transactions "
        "(id, idempotency_key, entity_id, admission_result, tool_name, input, created_at) "
        "VALUES "
        f"('{transaction_id}', '{_esc(idempotency_key)}', '{_esc(entity_id)}', "
        f"'{disposition}', '{_esc(gate)}', '{_esc(input_json)}'::jsonb, CURRENT_TIMESTAMP) "
        "ON CONFLICT (idempotency_key) DO NOTHING;"
    )
    proc = subprocess.run(cmd + ["-c", sql], capture_output=True, text=True,
                          timeout=_RECORD_TIMEOUT_S)
    return proc.returncode == 0


def record_gate_outcome(*, gate: str, entity_id: str, admitted: bool, reason: str,
                        payload: dict, psql: Optional[List[str]] = None) -> bool:
    """Persist one explicit advisory outcome, without lifecycle side effects.

    Binding payloads are validated before persistence. Invalid binding output is
    refused and not silently converted into an authorized result. The stable
    key is derived from immutable identity/fingerprint or the captured payload,
    never from wall-clock time.
    """
    cmd = psql if psql is not None else _psql_cmd()
    try:
        binding = payload.get("binding_decision") if isinstance(payload, dict) else None
        if binding is not None and validate_binding_decision is not None:
            validated = validate_binding_decision(binding, expected_subject_id=str(entity_id))
            idempotency_key = binding_idempotency_key(validated.to_dict())
        else:
            captured = json.dumps(payload, sort_keys=True, default=str)
            digest = uuid.uuid5(uuid.NAMESPACE_URL, captured)
            idempotency_key = f"peb:advisory:{gate}:{entity_id}:{digest}"
        input_json = json.dumps({"payload": payload, "reason": reason,
                                 "authority_level": "advisory"},
                                sort_keys=True, default=str)
        transaction_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        return _advisory_insert(
            cmd, transaction_id=transaction_id, idempotency_key=idempotency_key,
            entity_id=str(entity_id), gate=gate, admitted=admitted,
            input_json=input_json,
        )
    except Exception as exc:  # advisory path must never raise or activate
        print(f"[peb_admission] record skipped ({gate}): {exc}", file=sys.stderr)
        return False
