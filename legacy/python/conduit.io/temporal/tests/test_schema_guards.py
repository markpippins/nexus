"""Unit tests for the _get_schema() schema guards.

Tests that _get_schema() blocks 'temporal' and 'public' (case-insensitive)
and allows valid schema names like 'conduit' and 'test_*'.

Uses pytest monkeypatch fixture for clean env-var setup/teardown between tests.
Importable without a PostgreSQL connection — pure env-var tests.
"""

import sys
from pathlib import Path

import pytest

_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from db_adapter import _get_schema


class TestGetSchemaGuard:
    """_get_schema() must block reserved schemas and allow valid ones."""

    # ── Blocked: temporal ───────────────────────────────────────

    def test_blocks_temporal_lowercase(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "temporal")
        with pytest.raises(ValueError, match="temporal"):
            _get_schema()

    def test_blocks_temporal_uppercase(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "TEMPORAL")
        with pytest.raises(ValueError, match="temporal"):
            _get_schema()

    def test_blocks_temporal_mixed_case(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "Temporal")
        with pytest.raises(ValueError, match="temporal"):
            _get_schema()

    # ── Blocked: public ─────────────────────────────────────────

    def test_blocks_public_lowercase(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "public")
        with pytest.raises(ValueError, match="public"):
            _get_schema()

    def test_blocks_public_uppercase(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "PUBLIC")
        with pytest.raises(ValueError, match="public"):
            _get_schema()

    def test_blocks_public_mixed_case(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "Public")
        with pytest.raises(ValueError, match="public"):
            _get_schema()

    # ── Allowed: valid schema names ─────────────────────────────

    def test_allows_conduit_default(self, monkeypatch):
        monkeypatch.delenv("CONDUIT_PG_SCHEMA", raising=False)
        assert _get_schema() == "conduit"

    def test_allows_conduit_explicit(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "conduit")
        assert _get_schema() == "conduit"

    def test_allows_test_temporal_prefixed(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "test_temporal_abc123")
        assert _get_schema() == "test_temporal_abc123"

    def test_allows_arbitrary_schema(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "my_custom_schema")
        assert _get_schema() == "my_custom_schema"

    def test_allows_empty_string(self, monkeypatch):
        monkeypatch.setenv("CONDUIT_PG_SCHEMA", "")
        # Empty string is not 'temporal' or 'public' after .lower()
        # The empty schema will later be rejected by _ConnectionProxy
        # (single-quote check) or PostgreSQL itself — but _get_schema
        # should not block it.
        assert _get_schema() == ""
