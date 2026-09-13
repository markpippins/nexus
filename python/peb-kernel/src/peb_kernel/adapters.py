"""HTTP adapters for PEB's external integration ports."""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from typing import Any, Mapping
from uuid import UUID, uuid4

from .domain import ExecutionClaimAdmission

log = logging.getLogger(__name__)


class AdapterError(RuntimeError):
    """Raised when an external governance integration cannot be reached."""


class JsonHttpClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AdapterError(f"{method} {path} failed: {exc}") from exc


class ConduitMcpAdapter:
    def __init__(self, base_url: str = "http://localhost:3100", timeout: float = 10.0) -> None:
        self.client = JsonHttpClient(base_url, timeout)

    def submit_work_request(self, work_request: Any) -> Any:
        return self.client.request("POST", "/wr/submit", work_request)

    def get_work_request(self, wr_id: str) -> Any:
        return self.client.request("GET", f"/wr/{quote(wr_id, safe='')}")

    def transition_work_request(self, wr_id: str, transition: Any) -> Any:
        return self.client.request("POST", f"/wr/{quote(wr_id, safe='')}/transition", transition)

    def issue_receipt(self, receipt: Any) -> Any:
        return self.client.request("POST", "/vision/receipts", receipt)

    def query_state(self) -> Any:
        return self.client.request("GET", "/state")


class LosmIrTransitionAdapter:
    def __init__(self, base_url: str = "http://localhost:8006", timeout: float = 10.0) -> None:
        self.client = JsonHttpClient(base_url, timeout)

    def transition(self, wr_id: str, to_state: str, actor: str, reason: str) -> Any:
        return self.client.request(
            "POST",
            f"/work-requests/{quote(wr_id, safe='')}/transition",
            {"to_state": to_state, "actor": actor, "reason": reason},
        )

    def get_work_request(self, wr_id: str) -> Any:
        return self.client.request("GET", f"/work-requests/{quote(wr_id, safe='')}")

    def orchestrate(self, wr_id: str) -> Any:
        return self.client.request("POST", f"/work-requests/{quote(wr_id, safe='')}/orchestrate")


class ResolutionExecutionClaimAdapter:
    """Same-database adapter for the resolution execution-admission function.

    The adapter is deliberately narrow: the current execution slice accepts
    only the Git verifier contract. Resolution owns the evidence/link checks;
    this class only extracts the correlation envelope and maps the SQL result.
    Any malformed request or unavailable resolution function fails closed.
    """

    EXPECTED_SOURCE_SYSTEM = "git-verifier"
    EXPECTED_EVIDENCE_KIND = "git_ref_commit"

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn
    def _connect(self):
        import os

        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise AdapterError("psycopg2-binary is required for ResolutionExecutionClaimAdapter") from exc

        dsn = self.dsn or os.getenv(
            "PEB_DATABASE_URL", "postgresql://pguser:pgpass@localhost:5432/nexus"
        )
        return psycopg2.connect(dsn)

    def admit_verified_execution_claim(
        self, peb_transaction_id: UUID | None, input: Any | None
    ) -> ExecutionClaimAdmission:
        if peb_transaction_id is None or not isinstance(input, Mapping):
            return ExecutionClaimAdmission.rejected("MISSING_EXECUTION_CLAIM_EVIDENCE_ENVELOPE")

        if not isinstance(input.get("execution_claim"), Mapping):
            return ExecutionClaimAdmission.rejected("MISSING_EXECUTION_CLAIM_EVIDENCE_ENVELOPE")
        if not isinstance(input.get("execution_evidence"), Mapping):
            return ExecutionClaimAdmission.rejected("MISSING_EXECUTION_CLAIM_EVIDENCE_ENVELOPE")

        claim = input["execution_claim"]
        evidence = input["execution_evidence"]
        context = input.get("execution_context")
        if not isinstance(context, Mapping):
            context = {}

        claim_id = _uuid(_first_text(claim, "resolution_claim_id", "id"))
        evidence_id = _uuid(_first_text(evidence, "resolution_evidence_id", "id"))
        policy_hash = _first_text(context, "policy_version_hash")
        if policy_hash is None:
            policy_hash = _first_text(evidence, "policy_version_hash")
        lease_id = _first_text(context, "lease_id")
        if lease_id is None:
            lease_id = _first_text(evidence, "lease_id")
        grant_id = _first_text(context, "grant_id")
        if grant_id is None:
            grant_id = _first_text(evidence, "grant_id")
        attempt_id = _first_text(context, "attempt_id")
        if attempt_id is None:
            attempt_id = _first_text(evidence, "attempt_id")

        if not all([claim_id, evidence_id, policy_hash, lease_id, grant_id, attempt_id]):
            return ExecutionClaimAdmission.rejected("INVALID_EXECUTION_CLAIM_EVIDENCE_CONTEXT")

        try:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT admitted, reason, receipt_id "
                    "FROM resolution.admit_verified_execution_claim(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(peb_transaction_id),
                        str(claim_id),
                        str(evidence_id),
                        policy_hash,
                        lease_id,
                        grant_id,
                        attempt_id,
                        self.EXPECTED_SOURCE_SYSTEM,
                        self.EXPECTED_EVIDENCE_KIND,
                    ),
                )
                row = cursor.fetchone()
                # Persist the admission receipt: psycopg2 defaults to a
                # transaction, and close() without commit ROLLS BACK the
                # INSERT inside admit_verified_execution_claim — the receipt
                # row silently vanished (ruling 6677c394: R6 unreachable by
                # construction without this commit).
                connection.commit()
            finally:
                connection.close()
        except Exception:
            # The resolution migration may not yet be applied, or the
            # resolution database may be unavailable. Neither condition may
            # turn an unverified claim into admitted authority.
            log.warning("Resolution admission query failed", exc_info=True)
            return ExecutionClaimAdmission.rejected("RESOLUTION_ADMISSION_UNAVAILABLE")

        if row is None:
            return ExecutionClaimAdmission.rejected("RESOLUTION_ADMISSION_NO_RESULT")

        admitted = bool(row[0])
        reason = row[1]
        receipt_id = _uuid(row[2]) if row[2] else None
        if admitted:
            return ExecutionClaimAdmission.admitted_claim(reason or "", receipt_id)
        return ExecutionClaimAdmission.rejected(reason or "UNKNOWN")


def _first_text(node: Mapping[str, Any] | None, *names: str) -> str | None:
    """Return the first non-blank textual value for any of *names* in *node*."""
    if not isinstance(node, Mapping):
        return None
    for name in names:
        value = node.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None
