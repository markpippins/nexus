"""Source-owned metadata stream projection with traceability.

This module projects a metadata stream from Expression observations while
maintaining full source ownership and traceability. Unlike Aspects (governed),
this stream retains source ownership and provides full traceability back to
source observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Iterable
from uuid import UUID, uuid4

from .boundary import expression_boundary
from .pipeline import extract_explicit_observations, segment_transcript, source_fingerprint


@dataclass
class MetadataStreamRecord:
    """A single record in the metadata stream."""
    stream_record_id: str
    stream_id: str
    source_identity: str
    source_revision: str
    namespace: str
    kind: str
    key: str
    raw_value: Any
    normalized_value: str
    observation_id: str
    source_fingerprint: str
    extractor_revision: str
    projection_timestamp: datetime
    provenance: Dict[str, Any] = field(default_factory=dict)
    traceability: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetadataStream:
    """A complete metadata stream with traceability."""
    stream_id: str
    stream_revision: str
    source_fingerprint: str
    transcript_id: str
    extractor_revision: str
    records: List[Dict[str, Any]]
    created_at: datetime
    boundary: Dict[str, Any]
    traceability_chain: List[Dict[str, Any]] = field(default_factory=list)


def _normalize_tag(value: Any) -> str:
    """Normalize tag value to lowercase with hyphens."""
    return str(value).strip().lower().replace(" ", "-").replace("_", "-")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _observation_identity(
    segment_id: str, kind: str, value: str, source_identity: str
) -> str:
    """Create deterministic identity for a metadata stream record."""
    return hashlib.sha256(
        f"{value}\n{kind}\n{source_identity}".encode("utf-8")
    ).hexdigest()[:32]


def project_metadata_stream(
    transcript: dict[str, Any],
    *,
    extractor_revision: str = "expression-metadata-v0.1",
    stream_id: Optional[str] = None,
) -> MetadataStream:
    """Project a source-owned metadata stream from a transcript.
    
    This function extracts explicit observations from a transcript and projects
    them as a source-owned metadata stream with full traceability. The stream
    maintains source ownership and provides full traceability back to source
    observations.
    
    Args:
        transcript: The transcript to project.
        extractor_revision: Revision identifier for the extractor.
        stream_id: Optional custom stream ID (generated if not provided).
        
    Returns:
        A MetadataStream with full traceability.
    """
    transcript_id = transcript.get("transcript_id") or transcript.get("id") or "unknown"
    source_fp = source_fingerprint(transcript)
    stream_id = stream_id or _digest(f"{transcript_id}\nmetadata-stream")[:32]
    stream_revision = datetime.utcnow().isoformat() + "Z"
    
    # Extract explicit observations with full provenance
    observations = extract_explicit_observations(
        transcript, extractor_revision=extractor_revision
    )
    
    segments = segment_transcript(transcript)
    segment_map = {s["segment_id"]: s for s in segments}
    
    records: List[Dict[str, Any]] = []
    traceability_chain: List[Dict[str, Any]] = []
    
    for obs in observations:
        # Determine source identity
        source_identity = obs.get("source", {}).get("source_identity", "")
        if not source_identity:
            segment = segment_map.get(obs.get("source", {}).get("segment_id", ""), {})
            source_identity = f"segment:{segment.get('segment_id', 'unknown')}"
        
        # Build traceability record
        trace_record = {
            "observation_id": obs["observation_id"],
            "kind": obs["kind"],
            "value": obs["value"],
            "source_identity": source_identity,
            "source_revision": obs.get("extractor_revision", "unknown"),
            "segment_id": obs.get("source", {}).get("segment_id"),
            "transcript_id": transcript_id,
            "extractor_revision": obs.get("extractor_revision"),
            "input_fingerprint": obs.get("input_fingerprint"),
            "disposition": obs.get("disposition", "unreviewed"),
            "authority_status": obs.get("authority_status", "non_authoritative"),
        }
        traceability_chain.append(trace_record)
        
        # Create stream record
        kind = obs["kind"]
        value = obs["value"]
        normalized = _normalize_tag(obs["value"]) if isinstance(obs["value"], str) else str(obs["value"])
        
        record = {
            "stream_record_id": _digest(f"{stream_id}\n{obs['observation_id']}")[:32],
            "stream_id": stream_id,
            "source_identity": source_identity,
            "source_revision": obs.get("extractor_revision", "unknown"),
            "namespace": "expression",
            "kind": kind,
            "key": kind,
            "raw_value": obs["value"],
            "normalized_value": normalized,
            "observation_id": obs["observation_id"],
            "source_fingerprint": source_fingerprint(transcript),
            "extractor_revision": extractor_revision,
            "projection_timestamp": datetime.utcnow().isoformat() + "Z",
            "provenance": {
                "source": "expression_pipeline",
                "extractor": "extract_explicit_observations",
                "transcript_id": transcript_id,
            },
            "traceability": {
                "observation_id": obs["observation_id"],
                "source_fingerprint": source_fingerprint(transcript),
                "transcript_id": transcript_id,
                "extractor_revision": extractor_revision,
            },
            "authority_status": "non_authoritative",
        }
        records.append(record)
    
    return MetadataStream(
        stream_id=stream_id,
        stream_revision=stream_revision,
        source_fingerprint=source_fingerprint(transcript),
        transcript_id=transcript_id,
        extractor_revision=extractor_revision,
        records=records,
        created_at=datetime.utcnow(),
        boundary=expression_boundary(),
        traceability_chain=traceability_chain,
    )


def project_metadata_stream_from_records(
    records: Iterable[Dict[str, Any]],
    *,
    extractor_revision: str = "expression-metadata-v0.1",
    stream_id: Optional[str] = None,
) -> MetadataStream:
    """Project a metadata stream from pre-extracted observation records.
    
    This is useful when observations have already been extracted and you want
    to project them as a metadata stream with traceability.
    """
    if not records:
        return MetadataStream(
            stream_id=stream_id or _digest("empty-stream")[:32],
            stream_revision=datetime.utcnow().isoformat() + "Z",
            source_fingerprint="",
            transcript_id="empty",
            extractor_revision=extractor_revision,
            records=[],
            created_at=datetime.utcnow(),
            boundary=expression_boundary(),
            traceability_chain=[],
        )
    
    # Use first record's transcript_id as stream transcript_id
    first = records[0]
    transcript_id = first.get("source", {}).get("transcript_id", "unknown")
    
    stream_id = stream_id or _digest(f"stream\n{first.get('transcript_id', 'unknown')}")[:32]
    stream_revision = datetime.utcnow().isoformat() + "Z"
    
    records_out: List[Dict[str, Any]] = []
    traceability_chain: List[Dict[str, Any]] = []
    
    for obs in records:
        source_identity = obs.get("source", {}).get("source_identity", "unknown")
        kind = obs["kind"]
        value = obs["value"]
        normalized = _normalize_tag(obs["value"]) if isinstance(obs["value"], str) else str(obs["value"])
        
        trace_record = {
            "observation_id": obs["observation_id"],
            "kind": obs["kind"],
            "value": obs["value"],
            "source_identity": source_identity,
            "source_revision": obs.get("extractor_revision", "unknown"),
            "segment_id": obs.get("source", {}).get("segment_id"),
            "extractor_revision": obs.get("extractor_revision"),
            "input_fingerprint": obs.get("input_fingerprint"),
            "disposition": obs.get("disposition", "unreviewed"),
            "authority_status": obs.get("authority_status", "non_authoritative"),
        }
        traceability_chain.append(trace_record)
        
        record = {
            "stream_record_id": _digest(f"stream\n{obs['observation_id']}")[:32],
            "stream_id": stream_id,
            "source_identity": source_identity,
            "source_revision": obs.get("extractor_revision", "unknown"),
            "namespace": "expression",
            "kind": kind,
            "key": kind,
            "raw_value": obs["value"],
            "normalized_value": _normalize_tag(obs["value"]) if isinstance(obs["value"], str) else str(obs["value"]),
            "observation_id": obs["observation_id"],
            "source_fingerprint": obs.get("input_fingerprint", ""),
            "extractor_revision": obs.get("extractor_revision", "unknown"),
            "projection_timestamp": datetime.utcnow().isoformat() + "Z",
            "provenance": {
                "source": "expression_pipeline",
                "extractor": "extract_explicit_observations",
            },
            "traceability": {
                "observation_id": obs["observation_id"],
                "source_fingerprint": obs.get("input_fingerprint", ""),
                "extractor_revision": extractor_revision,
            },
            "authority_status": "non_authoritative",
        }
        records_out.append(record)
    
    return MetadataStream(
        stream_id=stream_id,
        stream_revision=stream_revision,
        source_fingerprint=records[0].get("input_fingerprint", ""),
        transcript_id=transcript_id,
        extractor_revision=extractor_revision,
        records=records_out,
        created_at=datetime.utcnow(),
        boundary=expression_boundary(),
        traceability_chain=traceability_chain,
    )


def _normalize_tag(value: Any) -> str:
    """Normalize tag value to lowercase with hyphens."""
    return str(value).strip().lower().replace(" ", "-").replace("_", "-")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_metadata_stream(stream: MetadataStream) -> list[str]:
    """Validate a metadata stream against the expression boundary."""
    errors = []
    boundary = stream.boundary
    
    if boundary.get("authority_status") != "non_authoritative":
        errors.append("Metadata stream must be non_authoritative")
    
    if boundary.get("observation_storage") != "staging":
        errors.append("Metadata stream observations must be staging material")
    
    if not stream.records:
        errors.append("Metadata stream must have at least one record")
    
    for record in stream.records:
        if record.get("traceability", {}).get("authority_status") != "non_authoritative":
            errors.append(f"Record {record.get('stream_record_id')} must be non_authoritative")
        
        if not record.get("traceability"):
            errors.append(f"Record {record.get('stream_record_id')} missing traceability")
    
    return errors


def export_metadata_stream(stream: MetadataStream) -> dict[str, Any]:
    """Export a metadata stream as a reproducible JSON bundle."""
    return {
        "stream_id": stream.stream_id,
        "stream_revision": stream.stream_revision,
        "source_fingerprint": stream.source_fingerprint,
        "transcript_id": stream.transcript_id,
        "extractor_revision": stream.extractor_revision,
        "records": stream.records,
        "created_at": stream.created_at.isoformat() + "Z" if isinstance(stream.created_at, datetime) else str(stream.created_at),
        "boundary": stream.boundary,
        "traceability_chain": stream.traceability_chain,
    }


def import_metadata_stream(data: dict[str, Any]) -> MetadataStream:
    """Import a metadata stream from a JSON bundle."""
    return MetadataStream(
        stream_id=data["stream_id"],
        stream_revision=data["stream_revision"],
        source_fingerprint=data["source_fingerprint"],
        transcript_id=data["transcript_id"],
        extractor_revision=data["extractor_revision"],
        records=data["records"],
        created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
        boundary=data["boundary"],
        traceability_chain=data.get("traceability_chain", []),
    )
