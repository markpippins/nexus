"""PEB adapter for the governed Keychains trigger boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .doctrine import DoctrineSnapshot, build_doctrine_snapshot


class OutboxConnection(Protocol):
    def cursor(self) -> Any: ...


class PebKeychainsAdapter:
    """Write one source-scoped PEB decision event into Resolution outbox.

    The caller must invoke this while the PEB store transaction is open. No
    commit is performed here, so the Keychains event cannot survive a rolled
    back PEB transaction.

    Binding decisions are deliberately narrow: only the ratified
    ``deny_contract_promotion`` class is accepted. Other PEB admissions retain
    the legacy generic event shape and never become binding by omission.
    """

    source_namespace = "peb"
    schema_version = 1
    binding_decision_class = "deny_contract_promotion"
    negative_dispositions = frozenset({
        "refused", "unknown", "stale", "drift", "quarantined", "superseded", "rolled_back",
    })

    def emit_transaction(self, connection: OutboxConnection, transaction: Any) -> None:
        binding = self._binding_decision(transaction)
        outcome = self._outcome(transaction, binding)
        source_event_id = self._source_event_id(transaction, binding)
        event_kind = self._event_kind(outcome, binding)
        now = datetime.now(timezone.utc).isoformat()
        read_set = self._read_set(transaction, binding)
        payload = self._payload(transaction, binding, outcome)

        connection.cursor().execute(
            """
            INSERT INTO resolution.keychain_event_outbox (
                source_namespace, source_event_id, event_kind, outcome,
                schema_version, aggregate_id, causation_id, correlation_id,
                actor, contract_id, evaluator_id, law_id, effective_at, recorded_at,
                read_set, payload, checkpoint_status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s
            )
            ON CONFLICT (source_namespace, source_event_id) DO NOTHING
            """,
            (
                self.source_namespace,
                source_event_id,
                event_kind,
                outcome,
                self.schema_version,
                str(transaction.entity_id),
                str(transaction.id),
                transaction.idempotency_key,
                "peb-kernel",
                self._contract_id(binding),
                self._evaluator_id(binding),
                self._law_id(binding),
                self._effective_at(binding, transaction),
                now,
                json.dumps(read_set, sort_keys=True, default=str),
                json.dumps(payload, sort_keys=True, default=str),
                # Every ratified deny_contract_promotion disposition is a
                # governed decision point, including negative outcomes. The
                # broker must consume those rows to create an immutable
                # checkpoint/read-set record. Generic PEB outcomes remain
                # archive-only unless they are committed.
                "pending" if binding is not None or outcome == "committed" else "not_applicable",
            ),
        )

    @classmethod
    def _binding_decision(cls, transaction: Any) -> Mapping[str, Any] | None:
        input_payload = getattr(transaction, "input", None)
        if not isinstance(input_payload, Mapping):
            return None
        candidate = input_payload.get("binding_decision")
        if candidate is None:
            return None
        if not isinstance(candidate, Mapping):
            raise ValueError("binding_decision must be an object")
        decision_class = candidate.get("decision_class")
        if decision_class != cls.binding_decision_class:
            raise ValueError(f"unauthorized Keychains decision class: {decision_class!r}")
        required = (
            "decision_id", "disposition", "authority_level", "decision_class",
            "evaluation_fingerprint", "replay_context", "evidence_ids",
            "contract", "evaluator", "doctrine_ids",
        )
        missing = [field for field in required if candidate.get(field) in (None, "")]
        if missing:
            raise ValueError("binding decision missing provenance: " + ", ".join(missing))
        if not isinstance(candidate["contract"], Mapping) or not candidate["contract"].get("version"):
            raise ValueError("binding decision missing provenance: contract.version")
        if not isinstance(candidate["evaluator"], Mapping) or not candidate["evaluator"].get("version"):
            raise ValueError("binding decision missing provenance: evaluator.version")
        if not (
            candidate.get("authorization_ref")
            or candidate.get("authority_ref")
            or candidate.get("grant_id")
        ):
            raise ValueError("binding decision missing provenance: authorization_ref")
        observation = candidate.get("observation_window")
        if observation is not None:
            if not isinstance(observation, Mapping):
                raise ValueError("observation_window must be an object")
            observation_required = (
                "activation_ref", "activated_at", "window_start", "window_end",
                "binding_owner", "authority_ref",
            )
            missing_observation = [
                field for field in observation_required if observation.get(field) in (None, "")
            ]
            if missing_observation:
                raise ValueError(
                    "observation window missing provenance: " + ", ".join(missing_observation)
                )
            try:
                activated_at = datetime.fromisoformat(str(observation["activated_at"]).replace("Z", "+00:00"))
                window_start = datetime.fromisoformat(str(observation["window_start"]).replace("Z", "+00:00"))
                window_end = datetime.fromisoformat(str(observation["window_end"]).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise ValueError("observation window bounds must be ISO-8601 timestamps") from exc
            if any(value.tzinfo is None for value in (activated_at, window_start, window_end)):
                raise ValueError("observation window bounds must include a timezone")
            if window_start < activated_at or window_start > window_end:
                raise ValueError("observation window bounds must start at/after activation and end after start")
        return candidate

    @classmethod
    def _outcome(cls, transaction: Any, binding: Mapping[str, Any] | None = None) -> str:
        if binding is not None:
            disposition = binding.get("disposition")
            if disposition == "allow":
                return "committed"
            if disposition in cls.negative_dispositions:
                return str(disposition)
            raise ValueError(f"unsupported binding disposition: {disposition!r}")
        value = getattr(getattr(transaction, "admission_result", None), "value", None)
        if value == "ALLOWED":
            return "committed"
        if value == "REJECTED":
            return "rejected"
        return "unknown"

    @classmethod
    def _source_event_id(cls, transaction: Any, binding: Mapping[str, Any] | None) -> str:
        if binding is None:
            return f"transaction:{transaction.id}"
        decision_id = binding.get("decision_id")
        fingerprint = binding.get("evaluation_fingerprint")
        if not decision_id or not fingerprint:
            raise ValueError("binding decision requires decision_id and evaluation_fingerprint")
        return f"binding:{decision_id}:{fingerprint}"

    @classmethod
    def _event_kind(cls, outcome: str, binding: Mapping[str, Any] | None) -> str:
        prefix = "peb.deny_contract_promotion" if binding is not None else "peb.admission"
        return f"{prefix}.{outcome}"

    @staticmethod
    def _nested(binding: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
        value = binding.get(key) if binding is not None else None
        return value if isinstance(value, Mapping) else {}

    @classmethod
    def _contract_id(cls, binding: Mapping[str, Any] | None) -> str:
        contract = cls._nested(binding, "contract")
        return str(contract.get("id") or "governed-trigger.v1")

    @classmethod
    def _evaluator_id(cls, binding: Mapping[str, Any] | None) -> str | None:
        evaluator = cls._nested(binding, "evaluator")
        return str(evaluator["id"]) if evaluator.get("id") is not None else None

    @classmethod
    def _law_id(cls, binding: Mapping[str, Any] | None) -> str | None:
        if binding is None:
            return None
        doctrine_ids = binding.get("doctrine_ids")
        if isinstance(doctrine_ids, list) and doctrine_ids:
            return ",".join(str(item) for item in doctrine_ids)
        return str(binding["law_id"]) if binding.get("law_id") is not None else None

    @classmethod
    def _effective_at(cls, binding: Mapping[str, Any] | None, transaction: Any) -> Any:
        return binding.get("as_of") if binding is not None and binding.get("as_of") else getattr(transaction, "created_at", None)

    @classmethod
    def _observation(cls, binding: Mapping[str, Any] | None) -> Mapping[str, Any]:
        value = binding.get("observation_window") if binding is not None else None
        return value if isinstance(value, Mapping) else {}

    @classmethod
    def _authorization_ref(cls, transaction: Any, binding: Mapping[str, Any] | None) -> Any:
        input_payload = getattr(transaction, "input", None)
        outer = input_payload if isinstance(input_payload, Mapping) else {}
        observation = cls._observation(binding)
        if binding is not None:
            return (
                observation.get("authorization_ref")
                or binding.get("authorization_ref")
                or binding.get("authority_ref")
                or binding.get("grant_id")
                or outer.get("authorization_ref")
                or outer.get("authority_ref")
                or outer.get("grant_id")
            )
        return outer.get("authorization_ref") or outer.get("authority_ref") or outer.get("grant_id")

    @staticmethod
    def _doctrine_snapshot(
        transaction: Any,
        binding: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Resolve an explicitly supplied doctrine snapshot without mutating it.

        A binding may carry either the normalized snapshot object or only its
        content address. Component inputs are also accepted so callers can use
        the same builder as the PEB producer. The existing normative
        ``doctrine_ids`` field is intentionally not overloaded.
        """
        input_payload = getattr(transaction, "input", None)
        sources = [
            source for source in (binding, input_payload)
            if isinstance(source, Mapping)
        ]
        for source in sources:
            candidate = source.get("doctrine_snapshot")
            if candidate is not None:
                if not isinstance(candidate, Mapping):
                    raise ValueError("doctrine_snapshot must be an object")
                if "snapshot_id" in candidate:
                    return DoctrineSnapshot.from_dict(candidate).to_dict()
                try:
                    return build_doctrine_snapshot(
                        system_prompt=candidate["system_prompt"],
                        bootstrap=candidate["bootstrap"],
                        active_procedure_cards=candidate["active_procedure_cards"],
                    ).to_dict()
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("doctrine_snapshot component inputs are invalid") from exc

            snapshot_id = source.get("doctrine_snapshot_id")
            if snapshot_id:
                return {"doctrine_snapshot_id": str(snapshot_id)}
        return None

    @staticmethod
    def _add_doctrine_provenance(target: dict[str, Any], snapshot: Mapping[str, Any] | None) -> None:
        if not snapshot:
            return
        if "snapshot_id" in snapshot:
            target["doctrine_snapshot"] = dict(snapshot)
            target["doctrine_snapshot_id"] = snapshot["snapshot_id"]
        elif snapshot.get("doctrine_snapshot_id"):
            target["doctrine_snapshot_id"] = snapshot["doctrine_snapshot_id"]

    @classmethod
    def _read_set(cls, transaction: Any, binding: Mapping[str, Any] | None) -> dict[str, Any]:
        read_set: dict[str, Any] = {
            "transaction_id": str(transaction.id),
            "entity_id": transaction.entity_id,
            "tool_name": transaction.tool_name,
            "admission_result": transaction.admission_result.value
            if transaction.admission_result
            else None,
            "before_hash": transaction.before_hash,
            "after_hash": transaction.after_hash,
        }
        doctrine_snapshot = cls._doctrine_snapshot(transaction, binding)
        if binding is None:
            cls._add_doctrine_provenance(read_set, doctrine_snapshot)
            return read_set

        contract = cls._nested(binding, "contract")
        evaluator = cls._nested(binding, "evaluator")
        observation = cls._observation(binding)
        input_payload = getattr(transaction, "input", None)
        outer = input_payload if isinstance(input_payload, Mapping) else {}
        read_set.update({
            "decision_class": binding["decision_class"],
            "binding_owner": (
                observation.get("binding_owner")
                or binding.get("binding_owner")
                or binding.get("owner")
                or outer.get("binding_owner")
            ),
            "authority_level": binding.get("authority_level"),
            "authorization_ref": cls._authorization_ref(transaction, binding),
            "authority_ref": observation.get("authority_ref") or binding.get("authority_ref") or outer.get("authority_ref"),
            "grant_id": observation.get("grant_id") or binding.get("grant_id") or outer.get("grant_id"),
            "binding_contract_version": binding.get("binding_contract_version"),
            "decision_id": binding.get("decision_id"),
            "proposition_id": binding.get("proposition_id"),
            "subject_id": binding.get("subject_id"),
            "work_item_id": binding.get("work_item_id"),
            "evidence_ids": binding.get("evidence_ids"),
            "replay_context": binding.get("replay_context"),
            "as_of": binding.get("as_of"),
            "disposition": binding.get("disposition"),
            "evidence_fresh": binding.get("evidence_fresh"),
            "evaluation_fingerprint": binding.get("evaluation_fingerprint"),
            "lineage_fingerprint": binding.get("lineage_fingerprint"),
            "observation_window": dict(observation) if observation else None,
            "contract_id": contract.get("id"),
            "contract_version": contract.get("version"),
            "evaluator_id": evaluator.get("id"),
            "evaluator_version": evaluator.get("version"),
            "law_id": cls._law_id(binding),
            "doctrine_ids": binding.get("doctrine_ids"),
            "law_version": binding.get("law_version"),
            "bridge_id": binding.get("bridge_id") or "peb-keychains-outbox",
            "bridge_version": binding.get("bridge_version") or "1",
            "activation_ref": observation.get("activation_ref") or binding.get("activation_ref") or outer.get("activation_ref"),
            "activated_at": observation.get("activated_at") or binding.get("activated_at") or outer.get("activated_at"),
            "rollback_ref": observation.get("rollback_ref") or binding.get("rollback_ref") or outer.get("rollback_ref"),
            "rollback_of": observation.get("rollback_of") or binding.get("rollback_of") or outer.get("rollback_of"),
            "rollback_status": observation.get("rollback_status") or binding.get("rollback_status") or outer.get("rollback_status"),
            "rollback_evidence_ids": observation.get("rollback_evidence_ids") or binding.get("rollback_evidence_ids") or outer.get("rollback_evidence_ids"),
            "read_set_manifest": binding.get("read_set_manifest") or outer.get("read_set_manifest"),
        })
        cls._add_doctrine_provenance(read_set, doctrine_snapshot)
        return read_set

    @classmethod
    def _payload(cls, transaction: Any, binding: Mapping[str, Any] | None, outcome: str) -> dict[str, Any]:
        payload = {
            "transaction_id": str(transaction.id),
            "idempotency_key": transaction.idempotency_key,
            "kernel_event_id": str(transaction.kernel_event_id)
            if transaction.kernel_event_id
            else None,
            "kernel_event_type": transaction.kernel_event_type,
        }
        doctrine_snapshot = cls._doctrine_snapshot(transaction, binding)
        if binding is not None:
            observation = cls._observation(binding)
            payload.update({
                "decision_class": binding["decision_class"],
                "decision_id": binding.get("decision_id"),
                "disposition": binding.get("disposition"),
                "authority_level": binding.get("authority_level"),
                "binding_owner": observation.get("binding_owner") or binding.get("binding_owner"),
                "authorization_ref": cls._authorization_ref(transaction, binding),
                "authority_ref": observation.get("authority_ref") or binding.get("authority_ref"),
                "grant_id": observation.get("grant_id") or binding.get("grant_id"),
                "activation_ref": observation.get("activation_ref") or binding.get("activation_ref"),
                "activated_at": observation.get("activated_at") or binding.get("activated_at"),
                "proposition_id": binding.get("proposition_id"),
                "subject_id": binding.get("subject_id"),
                "work_item_id": binding.get("work_item_id"),
                "evidence_ids": binding.get("evidence_ids"),
                "replay_context": binding.get("replay_context"),
                "as_of": binding.get("as_of"),
                "rollback_ref": observation.get("rollback_ref") or binding.get("rollback_ref"),
                "rollback_of": observation.get("rollback_of") or binding.get("rollback_of"),
                "rollback_status": observation.get("rollback_status") or binding.get("rollback_status"),
                "rollback_evidence_ids": observation.get("rollback_evidence_ids") or binding.get("rollback_evidence_ids"),
                "observation_window": dict(observation) if observation else None,
                "evaluation_fingerprint": binding.get("evaluation_fingerprint"),
                "lineage_fingerprint": binding.get("lineage_fingerprint"),
                "outcome": outcome,
            })
        cls._add_doctrine_provenance(payload, doctrine_snapshot)
        return payload


__all__ = ["PebKeychainsAdapter"]
