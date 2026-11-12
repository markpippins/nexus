"""Sol-native adapter (cutover 06).

Reads the `sol` database's `resolution` / `semantics` / `shrapnel` schemas
directly and produces the normalized SOLScript storage contract
(`SolStoragePort`). Fully standalone: works with Nexus absent. Depends only on
the contract dataclasses — no Nexus tables.

Source locations (current, verified in both sol and nexus):
  concepts/attributes/relationships  -> sol.resolution.concept*
  evidence                          -> sol.semantics.evidence_item
  revisions                         -> sol.semantics.asset_revision
  shrapnel facts                    -> sol.shrapnel.field + object_attribute_value

Adapter is async (mirrors DatabaseLoader). Queries use only the contract's
source locations; the DSN is injected so tests can point at a fixture or live
sol DB.
"""

from __future__ import annotations

from typing import Any, List, Optional

try:
    import asyncpg  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from .contract import (
    ContractAttribute,
    ContractConcept,
    ContractEvidence,
    ContractRelationship,
    ContractRevision,
    ContractShrapnelFact,
    ContractSubject,
    SolStoragePort,
)


class SolNativeAdapter:
    """Implements `SolStoragePort` against the `sol` database directly."""

    def __init__(
        self,
        dsn: str,
        pool: Optional[Any] = None,
        representation_id: Optional[str] = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        # Optional explicit representation selection (V217). Unset means the
        # concept's executable representations are used.
        self._representation_id = representation_id

    async def _conn(self) -> Any:
        if self._pool is not None:
            # acquire() returns a context manager wrapping a connection (or the
            # pool itself when it's a fake/test pool with a fetch method).
            acq = self._pool.acquire()
            conn = await acq.__aenter__()
            return conn
        if asyncpg is None:
            raise RuntimeError("asyncpg is required to connect without a pool")
        return await asyncpg.connect(self._dsn)

    async def _rows(self, sql: str, *args: Any) -> List[Any]:
        conn = await self._conn()
        try:
            return await conn.fetch(sql, *args)
        finally:
            if self._pool is None and asyncpg is not None:
                await conn.close()

    # ── Port methods ────────────────────────────────────────────────────

    async def list_concepts(self) -> List[ContractConcept]:
        rows = await self._rows(
            "SELECT id, name, description FROM resolution.concept "
            "WHERE expired_at IS NULL"
        )
        return [ContractConcept(id=str(r["id"]), name=r["name"], description=r["description"]) for r in rows]

    async def list_attributes(self) -> List[ContractAttribute]:
        rows = await self._rows(
            "SELECT id, concept_id, name, description, value_type, is_state_attribute "
            "FROM resolution.concept_attribute"
        )
        return [
            ContractAttribute(
                id=str(r["id"]),
                concept_id=str(r["concept_id"]),
                name=r["name"],
                value_type=r["value_type"],
                is_state_attribute=r["is_state_attribute"],
            )
            for r in rows
        ]

    async def list_relationships(self) -> List[ContractRelationship]:
        rows = await self._rows(
            "SELECT id, from_concept_id, to_concept_id, relationship_type "
            "FROM resolution.concept_relationship WHERE expired_at IS NULL"
        )
        return [
            ContractRelationship(
                id=str(r["id"]),
                from_concept_id=str(r["from_concept_id"]),
                to_concept_id=str(r["to_concept_id"]),
                relationship_type=r["relationship_type"],
            )
            for r in rows
        ]

    async def list_subjects(self, concept_id: str) -> List[ContractSubject]:
        # Subjects live in per-concept representation tables. For the
        # standalone sol-native case we do a conservative, schema-safe read:
        # only tables that exist and carry an id column. This returns empty
        # for concepts without a resolvable representation table.
        # One query that works before AND after V217. `to_jsonb(r)` exposes
        # evaluation_role when the column exists; pre-migration it is absent,
        # so the ELSE branch preserves the original resolution-only policy
        # instead of silently widening what this adapter reads.
        sql = (
            "SELECT r.schema_name, r.table_name FROM resolution.representation r "
            "JOIN resolution.concept c ON c.id = r.concept_id "
            "WHERE c.id = $1 AND r.expired_at IS NULL "
            "AND (CASE WHEN to_jsonb(r) ? 'evaluation_role' "
            "          THEN to_jsonb(r)->>'evaluation_role' = 'executable' "
            "          ELSE r.schema_name = 'resolution' END)"
        )
        args = [concept_id]
        if self._representation_id:
            sql += " AND r.id = $2"
            args.append(self._representation_id)
        tables = await self._rows(sql, *args)
        out: List[ContractSubject] = []
        for t in tables:
            schema, table = t["schema_name"], t["table_name"]
            try:
                rows = await self._rows(
                    f"SELECT * FROM {schema}.{table} WHERE concept_id = $1" if False else
                    f"SELECT * FROM {schema}.{table}"
                )
            except Exception:
                continue
            for row in rows:
                attrs = dict(row)
                sid = str(attrs.pop("id", ""))
                out.append(
                    ContractSubject(
                        id=sid,
                        concept_id=concept_id,
                        external_id=str(attrs["external_id"]) if "external_id" in attrs else None,
                        canonical_asset_id=str(attrs["asset_id"]) if "asset_id" in attrs else None,
                        attributes=attrs,
                    )
                )
        return out

    async def list_shrapnel_facts(self) -> List[ContractShrapnelFact]:
        # EAV read: object_instance joined to object_attribute_value -> field
        # property_name -> typed value. We read the value extension tables by
        # the field's field_type_code mapping (1..7) as in DatabaseLoader.
        rows = await self._rows(
            "SELECT o.id AS object_id, o.created_at AS created_at, "
            "       f.property_name AS field_name, "
            "       f.field_type_code, v.value_type_code, oav.value_id "
            "FROM shrapnel.object_instance o "
            "LEFT JOIN shrapnel.object_attribute_value oav ON oav.object_id = o.id "
            "LEFT JOIN shrapnel.field f ON f.id = oav.field_id "
            "LEFT JOIN shrapnel.value v ON v.id = oav.value_id"
        )
        # Group into per-object attribute dicts.
        objects: dict[int, dict[str, Any]] = {}
        created_at: dict[int, Any] = {}
        for r in rows:
            oid = r["object_id"]
            if oid is None:
                continue
            attr_key = r["field_name"]
            if attr_key is None:
                continue
            created_at[oid] = r["created_at"]
            value = await self._read_typed_value(r["value_id"], r["value_type_code"])
            objects.setdefault(oid, {})[attr_key] = value
        return [
            ContractShrapnelFact(
                object_id=str(oid),
                attributes=attrs,
                valid_from=created_at.get(oid),
            )
            for oid, attrs in objects.items()
        ]

    async def _read_typed_value(self, value_id: Optional[int], type_code: Optional[int]) -> Any:
        if value_id is None or type_code is None:
            return None
        table = {
            1: "value_long", 2: "value_string", 3: "value_double",
            4: "value_boolean", 5: "value_timestamp", 6: "value_jsonb",
            7: "value_uuid",
        }.get(type_code)
        if table is None:
            return None
        try:
            rows = await self._rows(f"SELECT value FROM shrapnel.{table} WHERE id = {int(value_id)}")
            return rows[0]["value"] if rows else None
        except Exception:
            return None

    async def list_revisions(self, subject_id: str) -> List[ContractRevision]:
        rows = await self._rows(
            "SELECT id, revision_id, asset_id, parent_revision_id, "
            "       recording_start, recording_end "
            "FROM semantics.asset_revision WHERE expired_at IS NULL"
        )
        return [
            ContractRevision(
                subject_id=str(r["asset_id"]),
                parent_revision_id=str(r["parent_revision_id"]) if r["parent_revision_id"] else None,
                valid_from=r["recording_start"],
                valid_until=r["recording_end"],
            )
            for r in rows
        ]

    async def list_evidence(self) -> List[ContractEvidence]:
        rows = await self._rows(
            "SELECT id, uri, excerpt, captured_at FROM semantics.evidence_item "
            "WHERE expired_at IS NULL"
        )
        return [
            ContractEvidence(
                id=str(r["id"]),
                source=str(r["uri"] or ""),
                content=r["excerpt"],
                captured_at=r["captured_at"],
            )
            for r in rows
        ]