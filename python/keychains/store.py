"""Keychain store adapters.

The projection logic talks to a small intent-named interface rather than to
Mongo query syntax, so the full base/delta/reservation protocol is testable
without a database. ``MemoryKeychainStore`` implements it over dicts;
``MongoKeychainStore`` is the production adapter over the ``keychains``
database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "KeychainStore",
    "MemoryKeychainStore",
    "MongoKeychainStore",
    "ReservationConflict",
    "DATABASE_NAME",
    "AGENT_RECORDS_KEY",
]

DATABASE_NAME = "keychains"
AGENT_RECORDS_KEY = "agent-records"

COMMITTED_OR_LEGACY = {"$or": [{"checkpoint_status": "committed"}, {"checkpoint_status": {"$exists": False}}]}


class ReservationConflict(RuntimeError):
    """Another delivery already owns this trigger's checkpoint construction."""


@runtime_checkable
class KeychainStore(Protocol):
    """Storage intents required by the contextual projection."""

    def upsert_doctrine_snapshot(self, snapshot: Mapping[str, Any]) -> None: ...

    def get_transition(self, keychain_event_id: str) -> dict[str, Any] | None: ...

    def find_committed_by_source(
        self, source_namespace: str, source_event_id: str
    ) -> dict[str, Any] | None: ...

    def archive_negative_outcome(self, document: Mapping[str, Any]) -> None: ...

    def reserve_transition(self, document: Mapping[str, Any]) -> None: ...

    def takeover_stale_reservation(
        self, keychain_event_id: str, stale_before: datetime
    ) -> bool: ...

    def deliver_transition(
        self, keychain_event_id: str, document: Mapping[str, Any], *, upsert: bool = False
    ) -> None: ...

    def latest_committed_checkpoint(self) -> dict[str, Any] | None: ...

    def nearest_base_version(self, at_or_below: int) -> int | None: ...

    def checkpoint_head(self) -> dict[str, Any] | None: ...

    def allocate_version(self, latest_committed_version: int) -> int: ...

    def insert_staged_checkpoint(self, document: Mapping[str, Any]) -> None: ...

    def commit_checkpoint(self, checkpoint_id: str, committed_at: str) -> None: ...

    def delete_staged_checkpoint(self, checkpoint_id: str) -> None: ...

    def promote_active_checkpoint(self, checkpoint: Mapping[str, Any]) -> None: ...

    def active_checkpoint(self) -> dict[str, Any] | None: ...

    def save_checkpoint_head(self, version: int, index: Mapping[str, str], updated_at: str) -> None: ...

    def add_drift_findings(self, findings: Sequence[Mapping[str, Any]]) -> None: ...

    def replace_entries(self, documents: Sequence[Mapping[str, Any]]) -> None: ...

    def replace_record_type_state(self, documents: Sequence[Mapping[str, Any]]) -> None: ...

    def entry_count(self) -> int: ...

    def query_transitions(
        self,
        *,
        limit: int,
        activation_ref: str | None = None,
        observation_status: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def reconcile(self, stale_before: datetime) -> None: ...


class MemoryKeychainStore:
    """In-memory implementation used by tests and dry runs."""

    def __init__(self) -> None:
        self.doctrine_snapshots: dict[str, dict[str, Any]] = {}
        self.transitions: dict[str, dict[str, Any]] = {}
        self.checkpoints: list[dict[str, Any]] = []
        self.sequence_version = 0
        self.head: dict[str, Any] | None = None
        self.active: dict[str, Any] | None = None
        self.drift_findings: list[dict[str, Any]] = []
        self.entries: list[dict[str, Any]] = []
        self.record_type_state: list[dict[str, Any]] = []

    # ── doctrine ──────────────────────────────────────────────────────────
    def upsert_doctrine_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if not snapshot:
            return
        snapshot_id = str(snapshot["snapshot_id"])
        if snapshot_id in self.doctrine_snapshots:
            return
        self.doctrine_snapshots[snapshot_id] = {
            **dict(snapshot),
            "created_at": snapshot.get("created_at"),
        }

    # ── transitions ───────────────────────────────────────────────────────
    def get_transition(self, keychain_event_id: str) -> dict[str, Any] | None:
        record = self.transitions.get(keychain_event_id)
        return dict(record) if record else None

    def find_committed_by_source(
        self, source_namespace: str, source_event_id: str
    ) -> dict[str, Any] | None:
        for checkpoint in sorted(
            self.checkpoints, key=lambda cp: int(cp.get("version") or 0), reverse=True
        ):
            if checkpoint.get("checkpoint_status") != "committed":
                continue
            if (
                checkpoint.get("source_namespace") == source_namespace
                and checkpoint.get("source_event_id") == source_event_id
            ):
                return dict(checkpoint)
        return None

    def archive_negative_outcome(self, document: Mapping[str, Any]) -> None:
        key = str(document["keychain_event_id"])
        if key in self.transitions:
            return  # $setOnInsert semantics
        self.transitions[key] = dict(document)

    def reserve_transition(self, document: Mapping[str, Any]) -> None:
        key = str(document["keychain_event_id"])
        if key in self.transitions:
            raise ReservationConflict(key)
        self.transitions[key] = dict(document)

    def takeover_stale_reservation(
        self, keychain_event_id: str, stale_before: datetime
    ) -> bool:
        record = self.transitions.get(keychain_event_id)
        if not record or not record.get("reservation"):
            return False
        reserved_at = record.get("reservation_at")
        if not isinstance(reserved_at, datetime) or reserved_at >= stale_before:
            return False
        record["reservation_at"] = record.get("_takeover_at")
        return True

    def deliver_transition(
        self, keychain_event_id: str, document: Mapping[str, Any], *, upsert: bool = False
    ) -> None:
        existing = self.transitions.get(keychain_event_id)
        if existing is None:
            if not upsert:
                raise KeyError(keychain_event_id)
            existing = {}
        merged = {**existing, **dict(document)}
        merged.pop("reservation", None)
        merged.pop("reservation_at", None)
        self.transitions[keychain_event_id] = merged

    # ── checkpoints ───────────────────────────────────────────────────────
    def latest_committed_checkpoint(self) -> dict[str, Any] | None:
        committed = [
            checkpoint
            for checkpoint in self.checkpoints
            if checkpoint.get("checkpoint_status") in (None, "committed")
        ]
        if not committed:
            return None
        return dict(max(committed, key=lambda cp: int(cp.get("version") or 0)))

    def nearest_base_version(self, at_or_below: int) -> int | None:
        candidates = [
            checkpoint
            for checkpoint in self.checkpoints
            if checkpoint.get("checkpoint_status") in (None, "committed")
            and int(checkpoint.get("version") or 0) <= at_or_below
            and (checkpoint.get("storage") == "base" or "state_vector" in checkpoint)
        ]
        if not candidates:
            return None
        return int(max(candidates, key=lambda cp: int(cp.get("version") or 0))["version"])

    def checkpoint_head(self) -> dict[str, Any] | None:
        return dict(self.head) if self.head else None

    def allocate_version(self, latest_committed_version: int) -> int:
        self.sequence_version = max(self.sequence_version, latest_committed_version) + 1
        return self.sequence_version

    def insert_staged_checkpoint(self, document: Mapping[str, Any]) -> None:
        self.checkpoints.append(dict(document))

    def _checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        for checkpoint in self.checkpoints:
            if checkpoint.get("checkpoint_id") == checkpoint_id:
                return checkpoint
        return None

    def commit_checkpoint(self, checkpoint_id: str, committed_at: str) -> None:
        checkpoint = self._checkpoint(checkpoint_id)
        if checkpoint is not None:
            checkpoint["checkpoint_status"] = "committed"
            checkpoint["committed_at"] = committed_at

    def delete_staged_checkpoint(self, checkpoint_id: str) -> None:
        self.checkpoints = [
            checkpoint
            for checkpoint in self.checkpoints
            if not (
                checkpoint.get("checkpoint_id") == checkpoint_id
                and checkpoint.get("checkpoint_status") == "staged"
            )
        ]

    def promote_active_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        version = int(checkpoint.get("version") or 0)
        if self.active and int(self.active.get("version") or 0) >= version:
            return  # a newer checkpoint already won the promotion race
        self.active = {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "version": version,
            "source_namespace": checkpoint.get("source_namespace") or None,
            "source_event_id": checkpoint.get("source_event_id") or None,
        }

    def active_checkpoint(self) -> dict[str, Any] | None:
        return dict(self.active) if self.active else None

    def save_checkpoint_head(
        self, version: int, index: Mapping[str, str], updated_at: str
    ) -> None:
        self.head = {"version": version, "index": dict(index), "updated_at": updated_at}

    def add_drift_findings(self, findings: Iterable[Mapping[str, Any]]) -> None:
        self.drift_findings.extend(dict(finding) for finding in findings)

    def replace_entries(self, documents: Iterable[Mapping[str, Any]]) -> None:
        self.entries = [dict(document) for document in documents]

    def replace_record_type_state(self, documents: Iterable[Mapping[str, Any]]) -> None:
        self.record_type_state = [dict(document) for document in documents]

    def entry_count(self) -> int:
        return len(self.entries)

    def query_transitions(
        self,
        *,
        limit: int,
        activation_ref: str | None = None,
        observation_status: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for record in self.transitions.values():
            if activation_ref:
                window = record.get("observation_window") or {}
                decision_context = record.get("decision_context") or {}
                activation = decision_context.get("activation") or {}
                if activation_ref not in (window.get("activation_ref"), activation.get("activation_ref")):
                    continue
            if observation_status:
                window = record.get("observation_window") or {}
                if window.get("phase") != observation_status:
                    continue
            if outcome and record.get("outcome") != outcome:
                continue
            selected.append(dict(record))
        selected.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
        return selected[:limit]

    def reconcile(self, stale_before: datetime) -> None:
        for record in self.transitions.values():
            reserved_at = record.get("reservation_at")
            if record.get("reservation") and isinstance(reserved_at, datetime) and reserved_at < stale_before:
                record.pop("reservation", None)
                record.pop("reservation_at", None)
                record.pop("checkpoint_id", None)
        self.checkpoints = [
            checkpoint
            for checkpoint in self.checkpoints
            if not (
                checkpoint.get("checkpoint_status") == "staged"
                and _dt(checkpoint.get("created_at")) is not None
                and _dt(checkpoint.get("created_at")) < stale_before  # type: ignore[operator]
            )
        ]
        latest = self.latest_committed_checkpoint()
        if latest and latest.get("checkpoint_id"):
            self.promote_active_checkpoint(latest)


def _dt(value: Any) -> datetime | None:
    from .models import parse_datetime

    if not value:
        return None
    try:
        return parse_datetime(str(value))
    except (TypeError, ValueError):
        return None


class MongoKeychainStore:
    """Production adapter over the ``keychains`` Mongo database."""

    def __init__(self, client: Any, database: str = DATABASE_NAME) -> None:
        self._db = client[database]

    # ── helpers ───────────────────────────────────────────────────────────
    def _collection(self, name: str) -> Any:
        return self._db[name]

    # ── doctrine ──────────────────────────────────────────────────────────
    def upsert_doctrine_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        if not snapshot:
            return
        self._collection("doctrine_snapshots").update_one(
            {"_id": snapshot["snapshot_id"]},
            {"$setOnInsert": dict(snapshot)},
            upsert=True,
        )

    # ── transitions ───────────────────────────────────────────────────────
    def get_transition(self, keychain_event_id: str) -> dict[str, Any] | None:
        return self._collection("transitions").find_one({"keychain_event_id": keychain_event_id})

    def find_committed_by_source(
        self, source_namespace: str, source_event_id: str
    ) -> dict[str, Any] | None:
        return self._collection("ar_snapshots").find_one(
            {
                "checkpoint_status": "committed",
                "source_namespace": source_namespace,
                "source_event_id": source_event_id,
            }
        )

    def archive_negative_outcome(self, document: Mapping[str, Any]) -> None:
        self._collection("transitions").update_one(
            {"keychain_event_id": document["keychain_event_id"]},
            {"$setOnInsert": dict(document)},
            upsert=True,
        )

    def reserve_transition(self, document: Mapping[str, Any]) -> None:
        try:
            self._collection("transitions").insert_one(dict(document))
        except Exception as exc:  # pymongo.errors.DuplicateKeyError
            if getattr(exc, "code", None) == 11000:
                raise ReservationConflict(str(document["keychain_event_id"])) from exc
            raise

    def takeover_stale_reservation(
        self, keychain_event_id: str, stale_before: datetime
    ) -> bool:
        result = self._collection("transitions").update_one(
            {
                "keychain_event_id": keychain_event_id,
                "reservation": True,
                "reservation_at": {"$lt": stale_before},
            },
            {"$set": {"reservation_at": datetime.now(tz=stale_before.tzinfo)}},
        )
        return bool(result.modified_count)

    def deliver_transition(
        self, keychain_event_id: str, document: Mapping[str, Any], *, upsert: bool = False
    ) -> None:
        self._collection("transitions").update_one(
            {"keychain_event_id": keychain_event_id},
            {"$set": dict(document), "$unset": {"reservation": "", "reservation_at": ""}},
            upsert=upsert,
        )

    # ── checkpoints ───────────────────────────────────────────────────────
    def latest_committed_checkpoint(self) -> dict[str, Any] | None:
        return self._collection("ar_snapshots").find_one(
            COMMITTED_OR_LEGACY, sort=[("version", -1)]
        )

    def nearest_base_version(self, at_or_below: int) -> int | None:
        found = self._collection("ar_snapshots").find_one(
            {
                "version": {"$lte": at_or_below},
                "$or": [
                    {"checkpoint_status": "committed"},
                    {"checkpoint_status": {"$exists": False}},
                ],
                "$and": [
                    {"$or": [{"storage": "base"}, {"state_vector": {"$exists": True}}]},
                ],
            },
            sort=[("version", -1)],
            projection={"version": 1},
        )
        return int(found["version"]) if found else None

    def checkpoint_head(self) -> dict[str, Any] | None:
        return self._collection("checkpoint_heads").find_one({"_id": AGENT_RECORDS_KEY})

    def allocate_version(self, latest_committed_version: int) -> int:
        sequences = self._collection("checkpoint_sequences")
        sequences.update_one(
            {"_id": AGENT_RECORDS_KEY},
            {"$max": {"version": latest_committed_version}},
            upsert=True,
        )
        result = sequences.find_one_and_update(
            {"_id": AGENT_RECORDS_KEY},
            {"$inc": {"version": 1}},
            upsert=True,
            return_document=True,  # pymongo ReturnDocument.AFTER
        )
        version = result.get("version") if isinstance(result, dict) else None
        if not isinstance(version, int) or version < 1:
            raise RuntimeError("checkpoint sequence did not return a valid version")
        return version

    def insert_staged_checkpoint(self, document: Mapping[str, Any]) -> None:
        self._collection("ar_snapshots").insert_one(dict(document))

    def commit_checkpoint(self, checkpoint_id: str, committed_at: str) -> None:
        self._collection("ar_snapshots").update_one(
            {"checkpoint_id": checkpoint_id, "checkpoint_status": "staged"},
            {"$set": {"checkpoint_status": "committed", "committed_at": committed_at}},
        )

    def delete_staged_checkpoint(self, checkpoint_id: str) -> None:
        self._collection("ar_snapshots").delete_many(
            {"checkpoint_id": checkpoint_id, "checkpoint_status": "staged"}
        )
        self._collection("checkpoint_entries").delete_many({"checkpoint_id": checkpoint_id})

    def promote_active_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        try:
            self._collection("active_checkpoints").update_one(
                {
                    "_id": AGENT_RECORDS_KEY,
                    "$or": [
                        {"version": {"$lt": checkpoint.get("version")}},
                        {"version": {"$exists": False}},
                    ],
                },
                {
                    "$set": {
                        "checkpoint_id": checkpoint.get("checkpoint_id"),
                        "version": checkpoint.get("version"),
                        "source_namespace": checkpoint.get("source_namespace") or None,
                        "source_event_id": checkpoint.get("source_event_id") or None,
                        "updated_at": datetime.now().astimezone().isoformat(),
                    },
                    "$setOnInsert": {"_id": AGENT_RECORDS_KEY},
                },
                upsert=True,
            )
        except Exception as exc:  # duplicate _id on the upsert race
            if getattr(exc, "code", None) != 11000:
                raise
            active = self.active_checkpoint()
            if not active or int(active.get("version") or 0) < int(checkpoint.get("version") or 0):
                raise

    def active_checkpoint(self) -> dict[str, Any] | None:
        return self._collection("active_checkpoints").find_one({"_id": AGENT_RECORDS_KEY})

    def save_checkpoint_head(
        self, version: int, index: Mapping[str, str], updated_at: str
    ) -> None:
        self._collection("checkpoint_heads").update_one(
            {"_id": AGENT_RECORDS_KEY},
            {"$set": {"version": version, "index": dict(index), "updated_at": updated_at}},
            upsert=True,
        )

    def add_drift_findings(self, findings: Sequence[Mapping[str, Any]]) -> None:
        if findings:
            self._collection("ar_drift_findings").insert_many([dict(f) for f in findings])

    def replace_entries(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self._collection("entries").delete_many({})
        if documents:
            self._collection("entries").insert_many([dict(document) for document in documents])

    def replace_record_type_state(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self._collection("record_type_state").delete_many({})
        if documents:
            self._collection("record_type_state").insert_many(
                [dict(document) for document in documents]
            )

    def entry_count(self) -> int:
        return int(self._collection("entries").count_documents({}))

    def query_transitions(
        self,
        *,
        limit: int,
        activation_ref: str | None = None,
        observation_status: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if activation_ref:
            query["$or"] = [
                {"observation_window.activation_ref": activation_ref},
                {"decision_context.activation.activation_ref": activation_ref},
            ]
        if observation_status:
            query["observation_window.phase"] = observation_status
        if outcome:
            query["outcome"] = outcome
        cursor = (
            self._collection("transitions")
            .find(query)
            .sort("created_at", -1)
            .limit(limit)
        )
        return list(cursor)

    def reconcile(self, stale_before: datetime) -> None:
        self._collection("transitions").update_many(
            {"reservation": True, "reservation_at": {"$lt": stale_before}},
            {"$unset": {"reservation": "", "reservation_at": "", "checkpoint_id": ""}},
        )
        stale = list(
            self._collection("ar_snapshots").find(
                {"checkpoint_status": "staged", "created_at": {"$lt": stale_before.isoformat()}},
                {"checkpoint_id": 1},
            )
        )
        stale_ids = [doc["checkpoint_id"] for doc in stale if doc.get("checkpoint_id")]
        if stale_ids:
            self._collection("ar_snapshots").delete_many(
                {"checkpoint_id": {"$in": stale_ids}, "checkpoint_status": "staged"}
            )
            self._collection("checkpoint_entries").delete_many(
                {"checkpoint_id": {"$in": stale_ids}}
            )
        latest = self.latest_committed_checkpoint()
        if latest and latest.get("checkpoint_id"):
            self.promote_active_checkpoint(latest)
