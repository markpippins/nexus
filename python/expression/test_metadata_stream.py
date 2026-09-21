"""Tests for metadata stream projection."""

from __future__ import annotations

import json
from datetime import datetime

from expression.metadata_stream import (
    MetadataStream,
    MetadataStreamRecord,
    project_metadata_stream,
    project_metadata_stream_from_records,
    validate_metadata_stream,
    export_metadata_stream,
    import_metadata_stream,
)
from expression.pipeline import extract_explicit_observations


def test_metadata_stream_projection():
    """Test projecting a metadata stream from a transcript."""
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123 to production"},
            {"role": "assistant", "content": "PR #123 deployed successfully at v2.3.4"},
        ],
    }
    
    stream = project_metadata_stream(
        transcript,
        extractor_revision="test-v0.1",
        stream_id="test-stream-001",
    )
    
    assert stream.stream_id == "test-stream-001"
    assert stream.transcript_id == "test-transcript-001"
    assert stream.extractor_revision == "test-v0.1"
    assert len(stream.records) > 0
    assert stream.source_fingerprint
    
    # Check records have required fields
    for record in stream.records:
        assert "stream_record_id" in record
        assert record["stream_id"] == "test-stream-001"
        assert record["authority_status"] == "non_authoritative"
        assert "traceability" in record
        assert "provenance" in record
    
    # Verify traceability chain
    assert len(stream.traceability_chain) > 0
    for trace in stream.traceability_chain:
        assert "observation_id" in trace
        assert "kind" in trace
        assert "source_identity" in trace
        assert "authority_status" in trace


def test_metadata_stream_from_records():
    """Test projecting from pre-extracted records."""
    records = [
        {
            "observation_id": "obs-001",
            "kind": "reference",
            "value": "PR #123",
            "source": {"source_identity": "test:transcript", "segment_id": "seg-001"},
            "extractor_revision": "test-v0.1",
            "input_fingerprint": "abc123",
            "disposition": "unreviewed",
            "authority_status": "non_authoritative",
        },
        {
            "observation_id": "obs-002",
            "kind": "version",
            "value": "v2.3.4",
            "source": {"source_identity": "test:transcript", "segment_id": "seg-002"},
            "extractor_revision": "test-v0.1",
            "input_fingerprint": "abc123",
            "disposition": "unreviewed",
            "authority_status": "non_authoritative",
        },
    ]
    
    stream = project_metadata_stream_from_records(
        records,
        extractor_revision="test-v0.1",
        stream_id="test-stream-002",
    )
    
    assert stream.stream_id == "test-stream-002"
    assert len(stream.records) == 2
    assert len(stream.traceability_chain) == 2
    
    for record in stream.records:
        assert record["authority_status"] == "non_authoritative"
        assert "traceability" in record


def test_metadata_stream_validation():
    """Test metadata stream validation."""
    from expression.metadata_stream import MetadataStream, MetadataStreamRecord
    from datetime import datetime
    
    # Valid stream
    stream = MetadataStream(
        stream_id="test",
        stream_revision="1.0",
        source_fingerprint="abc",
        transcript_id="test",
        extractor_revision="v0.1",
        records=[
            {
                "stream_record_id": "rec-1",
                "stream_id": "test",
                "source_identity": "test:1",
                "source_revision": "v0.1",
                "namespace": "expression",
                "kind": "reference",
                "key": "reference",
                "raw_value": "PR #123",
                "normalized_value": "pr-123",
                "observation_id": "obs-1",
                "source_fingerprint": "fp1",
                "extractor_revision": "v0.1",
                "projection_timestamp": datetime.utcnow().isoformat() + "Z",
                "provenance": {},
                "traceability": {"observation_id": "obs-1", "authority_status": "non_authoritative"},
            }
        ],
        created_at=datetime.utcnow(),
        boundary={"authority_status": "non_authoritative", "observation_storage": "staging"},
        traceability_chain=[],
    )
    
    from expression.metadata_stream import validate_metadata_stream
    errors = validate_metadata_stream(stream)
    assert errors == []
    
    # Invalid stream - wrong authority
    stream2 = MetadataStream(
        stream_id="test",
        stream_revision="1.0",
        source_fingerprint="abc",
        transcript_id="test",
        extractor_revision="v0.1",
        records=[{
            "stream_record_id": "rec-1",
            "stream_id": "test",
            "source_identity": "test:1",
            "source_revision": "v0.1",
            "namespace": "expression",
            "kind": "reference",
            "key": "reference",
            "raw_value": "PR #123",
            "normalized_value": "pr-123",
            "observation_id": "obs-1",
            "source_fingerprint": "fp1",
            "extractor_revision": "v0.1",
            "projection_timestamp": datetime.utcnow().isoformat() + "Z",
            "provenance": {},
            "traceability": {},
            "authority_status": "governed",  # Invalid - must be non_authoritative
        }],
        created_at=datetime.utcnow(),
        boundary={"authority_status": "governed", "observation_storage": "staging"},
        traceability_chain=[],
    )
    
    errors = validate_metadata_stream(stream2)
    assert len(errors) > 0
    assert any("non_authoritative" in e for e in errors)


def test_metadata_stream_export_import():
    """Test export and import round-trip."""
    from expression.metadata_stream import (
        project_metadata_stream,
        export_metadata_stream,
        import_metadata_stream,
    )
    
    transcript = {
        "transcript_id": "test-transcript-001",
        "turns": [
            {"role": "user", "content": "Deploy PR #123"},
            {"role": "assistant", "content": "Deployed at v1.0.0"},
        ],
    }
    
    stream = project_metadata_stream(
        {"transcript_id": "test", "turns": [{"role": "user", "content": "test"}]},
        stream_id="test-stream",
    )
    
    exported = export_metadata_stream(stream)
    imported = import_metadata_stream(exported)
    
    assert imported.stream_id == stream.stream_id
    assert imported.stream_revision == stream.stream_revision
    assert imported.source_fingerprint == stream.source_fingerprint
    assert imported.transcript_id == stream.transcript_id
    assert imported.extractor_revision == stream.extractor_revision
    assert len(imported.records) == len(stream.records)
    assert len(imported.traceability_chain) == len(stream.traceability_chain)
