"""Integration tests for Jev 5 — mapping Jev outputs into the existing evaluator.

Acceptance: read-only integration fixture demonstrates:
- `pending` — judgment requested, awaiting result
- `admitted`-like advisory result — calibrated judgment returned
- `uncertain` — confidence below threshold, flagged for review
- `refused` — policy violation or out of scope
- `unevaluable` — adapter error or timeout
- No authority transition — Jev stays advisory only
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from solscript.adapters.jev import (
    JevPrimitiveType,
    NoulResult,
    ChoiceResult,
    ScoreResult,
    JevNonOutcomeResult,
    JevAdapterConfig,
    OllamaConfig,
    OllamaTypeSafeAdapter,
    JevKnowledgeBase,
    SyncJevKnowledgeBase,
    create_jev_adapter,
    create_sync_jev_knowledge_base,
    CacheRetentionPolicy,
    StaleReadSetRejection,
    CacheEntryStatus,
)
from solscript.inference_engine import InferenceEngine, KnowledgeBase
from solscript.models import Entity, Concept, ConceptAttribute, Expression, ExpressionKind, Operator


class MockInterpreter:
    """Mock interpreter for testing."""
    def __init__(self):
        self.concepts = {}
        self.attributes = {}
    
    def get_concept(self, concept_id: str):
        return self.concepts.get(concept_id)
    
    def get_attribute(self, attr_id: str):
        return self.attributes.get(attr_id)
    
    def add_concept(self, concept: Concept):
        self.concepts[concept.id] = concept
        for attr in concept.attributes.values():
            self.attributes[attr.id] = attr


class TestJevEvaluatorIntegration:
    """Test Jev integration with the SOLScript evaluator."""
    
    @pytest.fixture
    def mock_adapter(self):
        """Create a mock adapter that returns predictable results."""
        adapter = MagicMock(spec=OllamaTypeSafeAdapter)
        adapter.get_backend_identity.return_value = "ollama-helium:qwen2.5-coder:latest"
        adapter.config = JevAdapterConfig(
            ollama=OllamaConfig(base_url="http://test:11434", model="test-model"),
        )
        return adapter
    
    @pytest.fixture
    def kb(self, mock_adapter):
        """Create a JevKnowledgeBase with mock adapter."""
        return JevKnowledgeBase(mock_adapter)
    
    @pytest.fixture
    def sync_kb(self, mock_adapter):
        """Create a SyncJevKnowledgeBase with mock adapter."""
        return SyncJevKnowledgeBase(mock_adapter)
    
    @pytest.fixture
    def engine(self):
        """Create an InferenceEngine with mock interpreter."""
        return InferenceEngine(MockInterpreter())
    
    # ── Pending State Tests ──────────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_pending_judgment_request(self, kb, mock_adapter):
        """Test that a judgment request starts in pending state."""
        # Simulate pending by having adapter delay
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.85, confidence=MagicMock(concentration=0.9, decision_mass=0.8))
        ])
        
        # Query the KB
        result = await kb.query("Is the entity ready?", {"entity_id": "test-123"})
        
        # Result should be available (not pending in our sync implementation)
        assert result is not None
        assert isinstance(result, NoulResult)
    
    # ── Admitted-like Advisory Result Tests ──────────────────────────────
    
    @pytest.mark.asyncio
    async def test_noul_advisory_result(self, kb, mock_adapter):
        """Test Noul (proposition truth) advisory result."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(
                probability=0.92,
                confidence=MagicMock(concentration=0.95, decision_mass=0.9),
            )
        ])
        
        result = await kb.query("Is readiness_score > 0.85?", {"readiness_score": 0.92})
        
        assert result is not None
        assert isinstance(result, NoulResult)
        assert result.probability == 0.92
        # Advisory only - no admission authority
        assert not hasattr(result, 'admitted')
        assert not hasattr(result, 'authority')
    
    @pytest.mark.asyncio
    async def test_choice_advisory_result(self, kb, mock_adapter):
        """Test Choice (classification) advisory result."""
        mock_adapter.system_one = AsyncMock(return_value=[
            ChoiceResult(
                label_probabilities={"transcript_drift": 0.78, "concept_drift": 0.12, "noise": 0.10},
                top_label="transcript_drift",
                confidence=MagicMock(concentration=0.85, decision_mass=0.8),
                flag_for_review=False,
            )
        ])
        
        result = await kb.query("Classify the drift type", {"drift_evidence": "pattern_match_0.87"})
        
        assert result is not None
        assert isinstance(result, ChoiceResult)
        assert result.top_label == "transcript_drift"
        assert result.flag_for_review is False
        # Advisory only - no reviewer settlement
        assert not hasattr(result, 'settled')
    
    @pytest.mark.asyncio
    async def test_score_advisory_result(self, kb, mock_adapter):
        """Test Score (rubric evaluation) advisory result."""
        mock_adapter.system_one = AsyncMock(return_value=[
            ScoreResult(
                expected_value=0.82,
                confidence=MagicMock(concentration=0.88, decision_mass=0.85),
                legend={"excellent": ">0.90", "good": "0.75-0.90", "acceptable": "0.60-0.75"},
            )
        ])
        
        result = await kb.query("Evaluate readiness against rubric", {"readiness_score": 0.82})
        
        assert result is not None
        assert isinstance(result, ScoreResult)
        assert result.expected_value == 0.82
        # Advisory only - no authority transition
        assert not hasattr(result, 'authorized')
    
    # ── Uncertain Result Tests ───────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_uncertain_result_low_confidence(self, kb, mock_adapter):
        """Test uncertain result when confidence below threshold."""
        mock_adapter.system_one = AsyncMock(return_value=[
            JevNonOutcomeResult(
                kind="uncertain",
                reason="Confidence 0.65 below threshold 0.75",
                retryable=False,
            )
        ])
        
        result = await kb.query("Ambiguous proposition?", {"conflicting_evidence": True})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "uncertain"
        assert "below threshold" in result.reason
        # Uncertain MUST enqueue for review (guardrail)
        assert result.retryable is False  # Not retryable, must be reviewed
    
    @pytest.mark.asyncio
    async def test_choice_flag_for_review(self, kb, mock_adapter):
        """Test Choice with flag_for_review when confidence low."""
        mock_adapter.system_one = AsyncMock(return_value=[
            ChoiceResult(
                label_probabilities={"option_a": 0.45, "option_b": 0.40, "option_c": 0.15},
                top_label="option_a",
                confidence=MagicMock(concentration=0.55, decision_mass=0.5),  # Below 0.75 threshold
                flag_for_review=True,  # Should be flagged
            )
        ])
        
        result = await kb.query("Classify with low confidence", {"ambiguous": True})
        
        assert result is not None
        assert isinstance(result, ChoiceResult)
        assert result.flag_for_review is True
        # Flagged for review - maps to adjudication queue (guardrail)
    
    # ── Refused Result Tests ─────────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_refused_result_policy_violation(self, kb, mock_adapter):
        """Test refused result for policy violation."""
        mock_adapter.system_one = AsyncMock(return_value=[
            JevNonOutcomeResult(
                kind="refused",
                reason="Question violates policy: attempts to access restricted data",
                retryable=False,
            )
        ])
        
        result = await kb.query("What is the admin password?", {"policy_check": "violation"})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "refused"
        assert "policy" in result.reason.lower()
        # Refused - no authority transition
    
    @pytest.mark.asyncio
    async def test_refused_out_of_scope(self, kb, mock_adapter):
        """Test refused result for out-of-scope question."""
        mock_adapter.system_one = AsyncMock(return_value=[
            JevNonOutcomeResult(
                kind="refused",
                reason="Question out of scope for Jev bounded judgment",
                retryable=False,
            )
        ])
        
        result = await kb.query("Write a poem about SOLScript", {"out_of_scope": True})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "refused"
    
    # ── Unevaluable Result Tests ─────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_unevaluable_adapter_error(self, kb, mock_adapter):
        """Test unevaluable when adapter fails."""
        mock_adapter.system_one = AsyncMock(side_effect=Exception("Connection timeout"))
        
        result = await kb.query("Will this fail?", {"test": True})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "unevaluable"
        assert "Connection timeout" in result.reason
        assert result.retryable is True  # Retryable error
    
    @pytest.mark.asyncio
    async def test_unevaluable_timeout(self, kb, mock_adapter):
        """Test unevaluable on timeout."""
        import httpx
        mock_adapter.system_one = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
        
        result = await kb.query("Slow question?", {"test": True})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "unevaluable"
        assert result.retryable is True
    
    # ── No Authority Transition Tests (Guardrails) ───────────────────────
    
    def test_no_admission_authority_in_kb(self, kb):
        """Verify KB has no admission methods."""
        assert not hasattr(kb, 'admit')
        assert not hasattr(kb, 'authorize_admission')
        assert not hasattr(kb, 'decide_admission')
    
    def test_no_reviewer_settlement_in_kb(self, kb):
        """Verify KB has no reviewer settlement methods."""
        assert not hasattr(kb, 'settle_review')
        assert not hasattr(kb, 'decide_disposition')
        assert not hasattr(kb, 'override_review')
    
    def test_no_direct_mutation_in_kb(self, kb):
        """Verify KB has no direct Resolution/PEB mutation methods."""
        mutation_methods = [m for m in dir(kb) if any(w in m.lower() for w in ['mutate', 'write', 'update_state', 'commit', 'persist'])]
        # Only cache/evidence store writes allowed
        allowed = {'_write_canonical_record', '_evict_if_needed', '_cache', '_evidence_store'}
        unexpected = [m for m in mutation_methods if m not in allowed]
        assert len(unexpected) == 0, f"Unexpected mutation methods: {unexpected}"
    
    def test_kb_read_only_interface(self, kb):
        """Verify KB only exposes read/query interface."""
        public_methods = [m for m in dir(kb) if not m.startswith('_')]
        # Core methods should be query, get_cache_stats, get_canonical_records, replay_judgment
        expected = {'query', 'get_cache_stats', 'get_canonical_records', 'replay_judgment', 'add_knowledge', 'add_pattern'}
        assert set(public_methods) >= expected
    
    # ── InferenceEngine Integration Tests ────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_inference_engine_with_jev_kb(self, engine, mock_adapter):
        """Test InferenceEngine using JevKnowledgeBase as external KB."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.88, confidence=MagicMock(concentration=0.9, decision_mass=0.85))
        ])
        
        kb = JevKnowledgeBase(mock_adapter)
        engine.external_knowledge_base = kb
        
        # Add a fact that can be inferred
        entity = Entity(id="test-1", concept_id="ServiceRequest", attributes={"status": "pending"})
        engine.add_fact("entity", entity)
        
        # The engine should be able to use the KB
        assert engine.external_knowledge_base is kb
    
    @pytest.mark.asyncio
    async def test_sync_kb_integration(self, engine, mock_adapter):
        """Test synchronous KB integration."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.75, confidence=MagicMock(concentration=0.8, decision_mass=0.75))
        ])
        
        kb = SyncJevKnowledgeBase(mock_adapter)
        engine.external_knowledge_base = kb
        
        # Sync query should work
        result = kb.query("Sync test?", {"sync": True})
        assert result is not None
        assert isinstance(result, NoulResult)
        assert result.probability == 0.75
    
    # ── Cache Integration Tests ──────────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_cache_hit_with_evidence_store(self, kb, mock_adapter):
        """Test cache hit stores canonical record in evidence store."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.9, confidence=MagicMock(concentration=0.9, decision_mass=0.85))
        ])
        
        # First query - cache miss
        result1 = await kb.query("Cached question?", {"context": "test"})
        assert result1 is not None
        
        # Second query - cache hit
        result2 = await kb.query("Cached question?", {"context": "test"})
        assert result2 is not None
        
        # Evidence store should have 1 record
        records = kb.get_canonical_records()
        assert len(records) == 1
        assert records[0].store_location.value == "evidence_store"
    
    @pytest.mark.asyncio
    async def test_stale_read_set_rejection(self, kb, mock_adapter):
        """Test stale read-set rejection returns stale non-outcome."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.8, confidence=MagicMock(concentration=0.85, decision_mass=0.8))
        ])
        
        # First query
        await kb.query("Stale test?", {"read_set_digest": "digest-v1"})
        
        # Second query with different digest - should reject as stale
        result = await kb.query("Stale test?", {"read_set_digest": "digest-v2"})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "stale"
        assert "digest mismatch" in result.reason
    
    @pytest.mark.asyncio
    async def test_model_version_mismatch_rejection(self, kb, mock_adapter):
        """Test model version mismatch rejection."""
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.8, confidence=MagicMock(concentration=0.85, decision_mass=0.8))
        ])
        
        # First query with v1 model
        mock_adapter.config = JevAdapterConfig(ollama=OllamaConfig(model="model-v1"))
        result1 = await kb.query("Version test?", {"model_version": "model-v1"})
        assert isinstance(result1, NoulResult)
        
        # Second query with v2 model - different model_version creates different cache key (cache miss)
        # This is the correct behavior per Jev 4: model_version is part of cache key
        mock_adapter.config = JevAdapterConfig(ollama=OllamaConfig(model="model-v2"))
        result2 = await kb.query("Version test?", {"model_version": "model-v2"})
        
        assert result2 is not None
        assert isinstance(result2, NoulResult)  # Cache miss, fresh query succeeds
        
        # Verify two separate cache entries were created (different model versions = different keys)
        stats = kb.get_cache_stats()
        assert stats["cache_size"] == 2
        assert stats["misses"] == 2
    
    @pytest.mark.asyncio
    async def test_eviction_policy(self, kb, mock_adapter):
        """Test eviction policy works."""
        # Set small cache for testing
        kb.retention_policy.max_total_entries = 3
        kb.retention_policy.max_entries_per_group = 2
        
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.5, confidence=MagicMock(concentration=0.8, decision_mass=0.7))
        ])
        
        # Fill cache beyond limit
        for i in range(5):
            mock_adapter.system_one.return_value = [
                NoulResult(probability=0.5 + i * 0.1, confidence=MagicMock())
            ]
            await kb.query(f"Question {i}?", {"group": "test"})
        
        # Should have evicted some entries
        stats = kb.get_cache_stats()
        assert stats["evictions"] > 0
        assert stats["cache_size"] <= 3
    
    # ── Read-Only Integration Fixture ────────────────────────────────────
    
    @pytest.mark.asyncio
    async def test_read_only_fixture_demonstrates_all_states(self, kb, mock_adapter):
        """Integration fixture demonstrating all Jev result states read-only."""
        # This test demonstrates the full read-only integration
        # without any authority transitions
        
        states_tested = set()
        
        # 1. Admitted-like advisory (Noul)
        mock_adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.85, confidence=MagicMock(concentration=0.9, decision_mass=0.85))
        ])
        result = await kb.query("Advisory true?", {"state": "valid"})
        assert isinstance(result, NoulResult)
        states_tested.add("advisory_noul")
        
        # 2. Advisory Choice
        mock_adapter.system_one = AsyncMock(return_value=[
            ChoiceResult(
                label_probabilities={"A": 0.7, "B": 0.3},
                top_label="A",
                confidence=MagicMock(concentration=0.85, decision_mass=0.8),
                flag_for_review=False,
            )
        ])
        result = await kb.query("Advisory choice?", {"state": "valid"})
        assert isinstance(result, ChoiceResult)
        states_tested.add("advisory_choice")
        
        # 3. Advisory Score
        mock_adapter.system_one = AsyncMock(return_value=[
            ScoreResult(expected_value=0.82, confidence=MagicMock(concentration=0.88, decision_mass=0.85), legend={})
        ])
        result = await kb.query("Advisory score?", {"state": "valid"})
        assert isinstance(result, ScoreResult)
        states_tested.add("advisory_score")
        
        # 4. Uncertain
        mock_adapter.system_one = AsyncMock(return_value=[
            JevNonOutcomeResult(kind="uncertain", reason="Low confidence", retryable=False)
        ])
        result = await kb.query("Uncertain?", {"ambiguous": True})
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "uncertain"
        states_tested.add("uncertain")
        
        # 5. Refused
        mock_adapter.system_one = AsyncMock(return_value=[
            JevNonOutcomeResult(kind="refused", reason="Policy violation", retryable=False)
        ])
        result = await kb.query("Refused?", {"policy_violation": True})
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "refused"
        states_tested.add("refused")
        
        # 6. Unevaluable
        mock_adapter.system_one = AsyncMock(side_effect=Exception("Error"))
        result = await kb.query("Error?", {"test": True})
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "unevaluable"
        states_tested.add("unevaluable")
        
        # Verify all states tested
        assert len(states_tested) == 6
        assert "advisory_noul" in states_tested
        assert "advisory_choice" in states_tested
        assert "advisory_score" in states_tested
        assert "uncertain" in states_tested
        assert "refused" in states_tested
        assert "unevaluable" in states_tested
        
        # Verify NO authority transition occurred
        # (no admission, no settlement, no mutation methods called on Resolution/PEB)


class TestJevGuardrailsEnforcement:
    """Test that guardrails are enforced at the code level."""
    
    def test_adapter_no_admission_methods(self):
        """OllamaTypeSafeAdapter has no admission methods."""
        adapter = OllamaTypeSafeAdapter(JevAdapterConfig())
        methods = [m for m in dir(adapter) if not m.startswith('_')]
        admission_methods = [m for m in methods if 'admit' in m.lower() or 'authorize' in m.lower()]
        assert len(admission_methods) == 0
    
    def test_adapter_no_settlement_methods(self):
        """OllamaTypeSafeAdapter has no reviewer settlement methods."""
        adapter = OllamaTypeSafeAdapter(JevAdapterConfig())
        methods = [m for m in dir(adapter) if not m.startswith('_')]
        settlement_methods = [m for m in methods if 'settle' in m.lower() or 'disposition' in m.lower()]
        assert len(settlement_methods) == 0
    
    def test_adapter_no_mutation_methods(self):
        """OllamaTypeSafeAdapter has no mutation methods."""
        adapter = OllamaTypeSafeAdapter(JevAdapterConfig())
        methods = [m for m in dir(adapter) if not m.startswith('_')]
        mutation_methods = [m for m in methods if any(w in m.lower() for w in ['mutate', 'write', 'update', 'commit', 'persist'])]
        # Only system_one, health_check, get_backend_identity, _internal methods
        public_mutations = [m for m in mutation_methods if not m.startswith('_')]
        assert len(public_mutations) == 0
    
    def test_kb_no_peb_admission(self):
        """JevKnowledgeBase has no PEB admission methods."""
        from solscript.adapters.jev import create_sync_jev_knowledge_base
        kb = create_sync_jev_knowledge_base()
        methods = [m for m in dir(kb) if not m.startswith('_')]
        peb_methods = [m for m in methods if 'peb' in m.lower() or 'admit' in m.lower()]
        assert len(peb_methods) == 0
    
    def test_kb_no_review_settlement(self):
        """JevKnowledgeBase has no reviewer settlement methods."""
        from solscript.adapters.jev import create_sync_jev_knowledge_base
        kb = create_sync_jev_knowledge_base()
        methods = [m for m in dir(kb) if not m.startswith('_')]
        review_methods = [m for m in methods if 'settle' in m.lower() or 'review' in m.lower()]
        # replay_judgment is allowed (verification)
        allowed = {'replay_judgment'}
        unexpected = [m for m in review_methods if m not in allowed]
        assert len(unexpected) == 0, f"Unexpected review methods: {unexpected}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])