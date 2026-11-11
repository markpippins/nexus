"""Typed client for lilac-srv — the single write surface conduit may use.

Why a client rather than another adapter: conduit already knows HOW to write
the canonical Resolution tables (``lilac.py``). What it lacks is one place
where the *protocol* lives — the provenance envelope, the idempotency key, the
outcome vocabulary, the refusal codes. Putting that in a service and giving
conduit a client means the rules are enforced once, by one implementation,
rather than re-derived per caller.

STAGED-INERT (AC6). ``from_env()`` returns ``None`` unless
``CONDUIT_LILAC_URL`` is set, and nothing in this module imports, instantiates
or calls it at import time. That is deliberate: a client that activates itself
on import is not staged, it is load-bearing.

Dependency-free on purpose (stdlib ``urllib``): conduit's hermetic CI tier
installs nothing, so a client that needed ``requests`` could not be tested by
the gate that guards it.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ── Protocol constants (kept in lockstep with typescript/lilac-srv) ───────
CONTRACT_VERSION = 1

#: The route each mutation arrived on, as the envelope must declare it.
ROUTES: Dict[str, str] = {
    "receipt": "POST /v1/receipts",
    "ticket": "POST /v1/tickets",
    "ticket_transition": "POST /v1/tickets/{ticket_id}/transitions",
    "fanout_transition": "POST /v1/fanouts",
    "timeline": "GET /v1/tickets/{ticket_id}/timeline",
    "parity": "GET /v1/parity",
}

#: Read routes, for the contract test that compares this table to openapi.yaml.
HTTP_PATHS: Dict[str, str] = {
    "issue_receipt": "/v1/receipts",
    "issue_ticket": "/v1/tickets",
    "record_transition": "/v1/tickets/{ticket_id}/transitions",
    "record_fanout": "/v1/fanouts",
    "ticket_timeline": "/v1/tickets/{ticket_id}/timeline",
    "parity_diagnostics": "/v1/parity",
}

#: Outcome classes the service may return for a mutation.
ACCEPTED = "accepted"
DUPLICATE_EQUIVALENT = "duplicate-equivalent"


class LilacServiceError(RuntimeError):
    """A typed refusal or transport failure from lilac-srv.

    ``code`` is the service's machine-readable refusal code (for example
    ``CONFLICT``) or ``TRANSPORT`` when the request never completed. The
    ``detail`` mapping is carried through verbatim, because the fingerprints a
    conflict reports are evidence and must not be flattened into prose.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: Optional[int] = None,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail: Dict[str, Any] = dict(detail or {})

    @property
    def is_conflict(self) -> bool:
        return self.code == "CONFLICT"

    def fingerprints(self) -> Tuple[Optional[str], Optional[str]]:
        """(existing, incoming) for a conflict, else (None, None)."""
        if not self.is_conflict:
            return (None, None)
        return (
            self.detail.get("existing_fingerprint"),
            self.detail.get("incoming_fingerprint"),
        )


@dataclass(frozen=True)
class Outcome:
    """A successful mutation classification (accepted / duplicate-equivalent)."""

    outcome: str
    target_id: str
    surface: str

    @property
    def is_duplicate(self) -> bool:
        return self.outcome == DUPLICATE_EQUIVALENT


def derive_idempotency_key(surface: str, source_identity: str, identity: Any) -> str:
    """Deterministic key for a mutation identity.

    Determinism is the point: a retry of the SAME logical mutation must present
    the SAME key, so the service classifies it as ``duplicate-equivalent``
    instead of treating it as a new write. A random key would make every retry
    look like a fresh mutation and defeat the contract it is meant to protect.
    """
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{surface}\x00{source_identity}\x00{canonical}".encode("utf-8")).hexdigest()
    return f"lilac-{surface}-{digest[:40]}"


class LilacClient:
    """HTTP client for the typed Lilac surface.

    ``opener`` is injectable so the contract test can exercise request shaping
    and refusal mapping without a network or a running service.
    """

    def __init__(
        self,
        base_url: str,
        *,
        source_implementation: str = "python/conduit/lilac_client.py",
        source_identity: str = "conduit",
        contract_version: int = CONTRACT_VERSION,
        timeout: float = 10.0,
        opener: Optional[Any] = None,
    ) -> None:
        if not base_url:
            raise ValueError("LilacClient requires a base_url")
        self.base_url = base_url.rstrip("/")
        self.source_implementation = source_implementation
        self.source_identity = source_identity
        self.contract_version = contract_version
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    # ── construction ────────────────────────────────────────────────
    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> Optional["LilacClient"]:
        """Return a client only when a base URL is configured.

        Staged-inert default: with ``CONDUIT_LILAC_URL`` unset this returns
        ``None`` and nothing about the live path changes (AC6).
        """
        env = environ if environ is not None else os.environ
        base = (env.get("CONDUIT_LILAC_URL") or "").strip()
        if not base:
            return None
        identity = (env.get("CONDUIT_LILAC_SOURCE_IDENTITY") or "conduit").strip()
        return cls(base, source_identity=identity, **kwargs)

    # ── envelope ────────────────────────────────────────────────────
    def envelope(self, route: str, idempotency_key: str) -> Dict[str, Any]:
        """The five fields the service requires; never partial."""
        if not idempotency_key:
            raise ValueError("idempotency_key is required: a mutation without one is unattributable")
        return {
            "source_implementation": self.source_implementation,
            "runtime_route": route,
            "contract_version": self.contract_version,
            "source_identity": self.source_identity,
            "idempotency_key": idempotency_key,
        }

    # ── mutations ───────────────────────────────────────────────────
    def issue_receipt(
        self,
        *,
        producer_id: str,
        kind: str,
        source_system: str,
        source_receipt_id: str,
        payload: Mapping[str, Any],
        refs: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Outcome:
        identity = {
            "producer_id": producer_id,
            "kind": kind,
            "source_system": source_system,
            "source_receipt_id": source_receipt_id,
            "payload": payload,
        }
        key = idempotency_key or derive_idempotency_key("receipt", self.source_identity, identity)
        body = {
            "provenance": self.envelope(ROUTES["receipt"], key),
            **identity,
            "refs": dict(refs or {}),
        }
        return self._post(HTTP_PATHS["issue_receipt"], body, "receipt")

    def issue_ticket(
        self,
        *,
        workflow_ref: str,
        role: str,
        position: int,
        generation: int = 0,
        objective: str = "",
        predecessor_receipt_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Outcome:
        identity = {
            "workflow_ref": workflow_ref,
            "role": role,
            "position": position,
            "generation": generation,
            "objective": objective,
        }
        key = idempotency_key or derive_idempotency_key("ticket", self.source_identity, identity)
        body = {
            "provenance": self.envelope(ROUTES["ticket"], key),
            **identity,
            "predecessor_receipt_id": predecessor_receipt_id,
        }
        return self._post(HTTP_PATHS["issue_ticket"], body, "ticket")

    def record_transition(
        self,
        *,
        ticket_id: str,
        input_receipt_id: str,
        from_status: str,
        to_status: str,
        fanout_policy_version: int = 1,
        outcome_class: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Outcome:
        identity = {
            "ticket_id": ticket_id,
            "input_receipt_id": input_receipt_id,
            "from_status": from_status,
            "to_status": to_status,
            "fanout_policy_version": fanout_policy_version,
            "payload": dict(payload or {}),
        }
        key = idempotency_key or derive_idempotency_key(
            "ticket_transition", self.source_identity, identity
        )
        body = {
            "provenance": self.envelope(ROUTES["ticket_transition"], key),
            **identity,
            "outcome_class": outcome_class,
        }
        path = HTTP_PATHS["record_transition"].format(ticket_id=ticket_id)
        return self._post(path, body, "ticket_transition")

    def record_fanout(
        self,
        *,
        input_receipt_id: str,
        kind: str,
        produced: Sequence[Mapping[str, Any]],
        fan_out_policy_version: int = 1,
        outcome: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Outcome:
        identity = {
            "input_receipt_id": input_receipt_id,
            "kind": kind,
            "fan_out_policy_version": fan_out_policy_version,
            "produced": [dict(p) for p in produced],
        }
        key = idempotency_key or derive_idempotency_key(
            "fanout_transition", self.source_identity, identity
        )
        body = {
            "provenance": self.envelope(ROUTES["fanout_transition"], key),
            **identity,
            "outcome": outcome,
        }
        return self._post(HTTP_PATHS["record_fanout"], body, "fanout_transition")

    # ── reads ───────────────────────────────────────────────────────
    def ticket_timeline(self, ticket_id: str) -> Dict[str, Any]:
        path = HTTP_PATHS["ticket_timeline"].format(ticket_id=ticket_id)
        status, payload = self._request("GET", path)
        if status >= 400:
            raise self._error(status, payload)
        return payload

    def parity_diagnostics(self, limit: int = 5) -> Dict[str, Any]:
        status, payload = self._request("GET", f"{HTTP_PATHS['parity_diagnostics']}?limit={int(limit)}")
        if status >= 400:
            raise self._error(status, payload)
        return payload

    # ── transport ───────────────────────────────────────────────────
    def _post(self, path: str, body: Mapping[str, Any], surface: str) -> Outcome:
        status, payload = self._request("POST", path, body)
        if status >= 400:
            raise self._error(status, payload)
        outcome = str(payload.get("outcome") or "")
        if outcome not in (ACCEPTED, DUPLICATE_EQUIVALENT):
            # A success status with an unrecognised outcome is a protocol
            # violation, not a success: fail closed rather than guess.
            raise LilacServiceError(
                "PROTOCOL",
                f"lilac-srv returned status {status} with outcome {outcome!r}",
                status=status,
                detail=payload,
            )
        return Outcome(
            outcome=outcome,
            target_id=str(payload.get("target_id") or ""),
            surface=str(payload.get("surface") or surface),
        )

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener(req, timeout=self.timeout) as resp:  # type: ignore[union-attr]
                raw = resp.read().decode("utf-8")
                return int(getattr(resp, "status", 200)), (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:  # a refusal is an answer, not a crash
            raw = exc.read().decode("utf-8") if exc.fp else ""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"message": raw}
            return int(exc.code), payload
        except (urllib.error.URLError, OSError) as exc:
            raise LilacServiceError(
                "TRANSPORT",
                f"lilac-srv unreachable at {self.base_url}: {exc}",
            ) from exc

    @staticmethod
    def _error(status: int, payload: Mapping[str, Any]) -> LilacServiceError:
        code = str(payload.get("code") or "UNKNOWN")
        message = str(payload.get("message") or f"lilac-srv refused the mutation (status {status})")
        detail = payload.get("detail")
        return LilacServiceError(
            code,
            message,
            status=status,
            detail=detail if isinstance(detail, Mapping) else {},
        )


def mutation_routes() -> Dict[str, str]:
    """surface → route, for callers that must declare where they wrote."""
    return {
        "receipt": ROUTES["receipt"],
        "ticket": ROUTES["ticket"],
        "ticket_transition": ROUTES["ticket_transition"],
        "fanout_transition": ROUTES["fanout_transition"],
    }


def documented_paths() -> List[str]:
    """Everything the client can call — compared against openapi.yaml."""
    return sorted(HTTP_PATHS.values())
