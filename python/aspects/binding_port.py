"""Governed Tag Binding Port for Aspects.

Implements the binding port protocol for querying governed tag vocabulary
and managing tag bindings between Expression projected tags and governed vocabulary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:  # the port is usable via dependency injection without the driver
    _ASYNCPG_AVAILABLE = False
    asyncpg = None

try:
    from solscript.adapters.contract import SolStoragePort
except ImportError:
    SolStoragePort = None


@dataclass
class GovernedTag:
    """Governed tag vocabulary entry."""
    id: UUID
    name: str
    normalized_name: str
    member_kind: str
    definition: str
    applies_to: str
    parent_id: Optional[UUID]
    ratified_by: Optional[str]
    notes: Optional[str]
    created_at: datetime
    expired_at: Optional[datetime]


@dataclass
class TagBinding:
    """Tag binding from Expression projected tag to governed vocabulary."""
    id: UUID
    governed_tag_id: UUID
    governed_tag_name: str
    governed_normalized_name: str
    member_kind: str
    applies_to: str
    source_identity: str
    source_revision: str
    namespace: str
    tag_key: str
    normalized_value: str
    expression_observation_id: Optional[str]
    status: str
    bound_by: Optional[str]
    created_at: datetime
    expired_at: Optional[datetime]


# ── Binding lifecycle (Aspect G2) ────────────────────────────────────────
# Explicit state machine for tag bindings: a binding is proposed by the
# Expression-side adapter, decided (approved/rejected) by governance, and
# eventually expired. Nothing else is a legal status, and expiring sets
# expired_at so active_only queries stop returning it.
BINDING_STATUSES = ("proposed", "approved", "rejected", "expired")

ALLOWED_TRANSITIONS = {
    "proposed": {"approved", "rejected", "expired"},
    "approved": {"expired"},
    "rejected": {"expired"},
    "expired": set(),
}


def validate_binding_transition(current: str, new_status: str) -> None:
    """Raise ValueError unless current -> new_status is a legal transition."""
    if current not in BINDING_STATUSES:
        raise ValueError(f"unknown binding status: {current!r}")
    if new_status not in BINDING_STATUSES:
        raise ValueError(f"unknown binding status: {new_status!r}")
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"illegal binding transition: {current!r} -> {new_status!r}; "
            f"allowed: {sorted(ALLOWED_TRANSITIONS[current]) or 'none'}"
        )


def _row_to_tag_binding(row: Any) -> TagBinding:
    """Map an asyncpg Row to TagBinding, tolerating tb.id AS binding_id aliases.

    2026-09-21 (Aspect G2): list_bindings aliased ``tb.id AS binding_id`` but
    constructed ``TagBinding(**dict(row))`` whose field is ``id`` — a
    guaranteed TypeError on the first governed binding ever listed (review
    record bc724f6b finding 4). Centralizing the row mapping makes that class
    of aliasing bug impossible to reintroduce silently.
    """
    data = dict(row)
    if "id" not in data and "binding_id" in data:
        data["id"] = data.pop("binding_id")
    return TagBinding(**data)


class AspectsBindingPort:
    """Binding port for Aspects governed tag vocabulary and bindings.
    
    Implements the binding port protocol for querying governed tag vocabulary
    and managing tag bindings between Expression projected tags and governed vocabulary.
    """
    
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    # ── Governed Tag Vocabulary ──────────────────────────────────────────
    
    async def list_governed_tags(
        self, 
        active_only: bool = True,
        member_kind: Optional[str] = None,
        applies_to: Optional[str] = None
    ) -> List[GovernedTag]:
        """List governed tag vocabulary entries."""
        conditions = []
        params = []
        param_idx = 1
        
        if active_only:
            conditions.append("expired_at IS NULL")
        
        if member_kind:
            conditions.append(f"member_kind = ${param_idx}")
            params.append(member_kind)
            param_idx += 1
        
        if applies_to:
            conditions.append(f"applies_to = ${param_idx}")
            params.append(applies_to)
            param_idx += 1
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT id, name, normalized_name, member_kind, definition, applies_to,
                   parent_id, ratified_by, notes, created_at, expired_at
            FROM aspects.governed_tag_vocabulary
            {where_clause}
            ORDER BY name
        """
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [GovernedTag(**dict(row)) for row in rows]
    
    async def get_governed_tag(self, tag_id: UUID) -> Optional[GovernedTag]:
        """Get a single governed tag by ID."""
        query = """
            SELECT id, name, normalized_name, member_kind, definition, applies_to,
                   parent_id, ratified_by, notes, created_at, expired_at
            FROM aspects.governed_tag_vocabulary
            WHERE id = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, tag_id)
            return GovernedTag(**dict(row)) if row else None
    
    async def get_governed_tag_by_name(self, name: str) -> Optional[GovernedTag]:
        """Get a governed tag by name."""
        query = """
            SELECT id, name, normalized_name, member_kind, definition, applies_to,
                   parent_id, ratified_by, notes, created_at, expired_at
            FROM aspects.governed_tag_vocabulary
            WHERE name = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, name)
            return GovernedTag(**dict(row)) if row else None
    
    async def get_governed_tag_by_normalized(self, normalized_name: str) -> Optional[GovernedTag]:
        """Get a governed tag by normalized name."""
        query = """
            SELECT id, name, normalized_name, member_kind, definition, applies_to,
                   parent_id, ratified_by, notes, created_at, expired_at
            FROM aspects.governed_tag_vocabulary
            WHERE normalized_name = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, normalized_name)
            return GovernedTag(**dict(row)) if row else None
    
    # ── Tag Bindings ─────────────────────────────────────────────────────
    
    async def list_bindings(
        self,
        status: Optional[str] = None,
        governed_tag_id: Optional[UUID] = None,
        source_identity: Optional[str] = None,
        active_only: bool = True
    ) -> List[TagBinding]:
        """List tag bindings with optional filters."""
        conditions = []
        params = []
        param_idx = 1
        
        if active_only:
            conditions.append("tb.expired_at IS NULL AND gtv.expired_at IS NULL")
        
        if status:
            conditions.append(f"tb.status = ${param_idx}")
            params.append(status)
            param_idx += 1
        
        if governed_tag_id:
            conditions.append(f"tb.governed_tag_id = ${param_idx}")
            params.append(governed_tag_id)
            param_idx += 1
        
        if source_identity:
            conditions.append(f"tb.source_identity = ${param_idx}")
            params.append(source_identity)
            param_idx += 1
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT 
                tb.id AS binding_id,
                gtv.id AS governed_tag_id,
                gtv.name AS governed_tag_name,
                gtv.normalized_name AS governed_normalized_name,
                gtv.member_kind,
                gtv.applies_to,
                tb.source_identity,
                tb.source_revision,
                tb.namespace,
                tb.tag_key,
                tb.normalized_value,
                tb.expression_observation_id,
                tb.status,
                tb.bound_by,
                tb.created_at,
                tb.expired_at
            FROM aspects.tag_binding tb
            JOIN aspects.governed_tag_vocabulary gtv ON gtv.id = tb.governed_tag_id
            {where_clause}
            ORDER BY tb.created_at DESC
        """
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [_row_to_tag_binding(row) for row in rows]
    
    async def create_binding(
        self,
        governed_tag_id: UUID,
        source_identity: str,
        source_revision: str,
        namespace: str,
        tag_key: str,
        normalized_value: str,
        expression_observation_id: Optional[str] = None,
        bound_by: Optional[str] = None,
        status: str = "proposed"
    ) -> TagBinding:
        """Create a new tag binding.

        The initial status must be a non-terminal binding status; 'expired'
        is rejected because a binding that has never existed cannot expire.
        """
        if status not in {"proposed", "approved", "rejected"}:
            raise ValueError(
                f"initial binding status must be proposed/approved/rejected, got {status!r}"
            )
        query = """
            INSERT INTO aspects.tag_binding (
                governed_tag_id, source_identity, source_revision, namespace,
                tag_key, normalized_value, expression_observation_id,
                status, bound_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, governed_tag_id, source_identity, source_revision,
                      namespace, tag_key, normalized_value, expression_observation_id,
                      status, bound_by, created_at, expired_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                governed_tag_id, source_identity, source_revision, namespace,
                tag_key, normalized_value, expression_observation_id,
                status, bound_by
            )
            # Fetch the governed tag details
            tag_row = await conn.fetchrow(
                "SELECT name, normalized_name, member_kind, applies_to FROM aspects.governed_tag_vocabulary WHERE id = $1",
                governed_tag_id
            )
            result = TagBinding(
                id=row["id"],
                governed_tag_id=row["governed_tag_id"],
                governed_tag_name=tag_row["name"],
                governed_normalized_name=tag_row["normalized_name"],
                member_kind=tag_row["member_kind"],
                applies_to=tag_row["applies_to"],
                source_identity=row["source_identity"],
                source_revision=row["source_revision"],
                namespace=row["namespace"],
                tag_key=row["tag_key"],
                normalized_value=row["normalized_value"],
                expression_observation_id=row["expression_observation_id"],
                status=row["status"],
                bound_by=row["bound_by"],
                created_at=row["created_at"],
                expired_at=row["expired_at"]
            )
            return result
    
    async def update_binding_status(
        self,
        binding_id: UUID,
        status: str,
        bound_by: Optional[str] = None
    ) -> Optional[TagBinding]:
        """Update binding status through the explicit lifecycle machine.

        proposed -> approved | rejected | expired; approved/rejected -> expired.
        Expiring stamps expired_at so active_only queries stop returning the
        binding. Unknown statuses and illegal transitions fail closed.
        """
        async with self.pool.acquire() as conn:
            current = await conn.fetchrow(
                "SELECT status FROM aspects.tag_binding WHERE id = $1 AND expired_at IS NULL",
                binding_id,
            )
            if not current:
                return None
            validate_binding_transition(current["status"], status)
            query = """
                UPDATE aspects.tag_binding
                SET status = $2,
                    bound_by = COALESCE($3, bound_by),
                    expired_at = CASE WHEN $2 = 'expired' THEN now() ELSE expired_at END
                WHERE id = $1 AND expired_at IS NULL
                RETURNING id, governed_tag_id, source_identity, source_revision,
                          namespace, tag_key, normalized_value, expression_observation_id,
                          status, bound_by, created_at, expired_at
            """
            row = await conn.fetchrow(query, binding_id, status, bound_by)
            if not row:
                return None
            tag_row = await conn.fetchrow(
                "SELECT name, normalized_name, member_kind, applies_to FROM aspects.governed_tag_vocabulary WHERE id = (SELECT governed_tag_id FROM aspects.tag_binding WHERE id = $1)",
                binding_id
            )
            return TagBinding(
                id=row["id"],
                governed_tag_id=row["governed_tag_id"],
                governed_tag_name=tag_row["name"],
                governed_normalized_name=tag_row["normalized_name"],
                member_kind=tag_row["member_kind"],
                applies_to=tag_row["applies_to"],
                source_identity=row["source_identity"],
                source_revision=row["source_revision"],
                namespace=row["namespace"],
                tag_key=row["tag_key"],
                normalized_value=row["normalized_value"],
                expression_observation_id=row["expression_observation_id"],
                status=row["status"],
                bound_by=row["bound_by"],
                created_at=row["created_at"],
                expired_at=row["expired_at"]
            )
    
    # ── SolStoragePort compatibility (read-only subset) ──────────────────
    
    async def list_concepts(self) -> List[Any]:
        """SolStoragePort: list concepts - maps governed tags to concepts."""
        tags = await self.list_governed_tags(active_only=True)
        # Convert to ContractConcept-like objects
        from solscript.adapters.contract import ContractConcept
        return [
            ContractConcept(id=str(tag.id), name=tag.name, description=tag.definition)
            for tag in tags
        ]
    
    async def list_relationships(self) -> List[Any]:
        """SolStoragePort: list relationships - maps tag hierarchy to relationships."""
        tags = await self.list_governed_tags(active_only=True)
        from solscript.adapters.contract import ContractRelationship
        relationships = []
        for tag in tags:
            if tag.parent_id:
                relationships.append(ContractRelationship(
                    id=str(tag.id),
                    from_concept_id=str(tag.parent_id),
                    to_concept_id=str(tag.id),
                    relationship_type="has_parent"
                ))
        return relationships
    
    # Other SolStoragePort methods (not implemented for binding port)
    async def list_attributes(self) -> List[Any]: return []
    async def list_subjects(self, concept_id: str) -> List[Any]: return []
    async def list_shrapnel_facts(self) -> List[Any]: return []
    async def list_revisions(self, subject_id: str) -> List[Any]: return []
    async def list_evidence(self) -> List[Any]: return []


# ── Convenience factory ────────────────────────────────────────────────

async def create_binding_port(dsn: str) -> AspectsBindingPort:
    """Create a binding port with a connection pool."""
    if not _ASYNCPG_AVAILABLE:
        # Fail fast with an actionable message. Without this guard a missing
        # driver surfaces later as AttributeError: 'NoneType' object has no
        # attribute 'create_pool' (asyncpg was soft-imported as None).
        raise RuntimeError(
            "asyncpg is not installed; it is the aspects binding port's "
            "runtime driver. Install the declared requirements: "
            "pip install -r python/aspects/requirements.txt"
        )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return AspectsBindingPort(pool)
