"""Tests for the Jev (TypeSafe System One) adapter spike.

Acceptance criteria (from Jev 2 To Do):
- One bounded Noul, Choice, and Score request round-trip
- Timeout/error/unknown behavior is fail-visible
- No hosted Jev commitment and no governance writes
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
    OllamaConfig,
    JevAdapterConfig,
    OllamaTypeSafeAdapter,
    JevKnowledgeBase,
    SyncJevKnowledgeBase,
    create_jev_adapter,
    create_sync_jev_knowledge_base,
)


class TestOllamaConfig:
    """Test configuration defaults."""
    
    def test_default_config(self):
        config = OllamaConfig()
        assert config.base_url == "http://helium:11434"
        assert config.model == "qwen2.5-coder:latest"
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 2
        assert config.temperature == 0.1
    
    def test_custom_config(self):
        config = OllamaConfig(
            base_url="http://localhost:11434",
            model="llama3:latest",
            timeout_seconds=10.0,
        )
        assert config.base_url == "http://localhost:11434"
        assert config.model == "llama3:latest"
        assert config.timeout_seconds == 10.0


class TestJevAdapterConfig:
    """Test adapter configuration."""
    
    def test_default_config(self):
        config = JevAdapterConfig()
        assert isinstance(config.ollama, OllamaConfig)
        assert config.confidence_threshold == 0.75
        assert config.calibration is None


class TestOllamaTypeSafeAdapter:
    """Test the ollama adapter implementation."""
    
    @pytest.fixture
    def adapter_config(self):
        return JevAdapterConfig(
            ollama=OllamaConfig(base_url="http://test:11434", model="test-model"),
        )
    
    @pytest.fixture
    def adapter(self, adapter_config):
        return OllamaTypeSafeAdapter(adapter_config)
    
    def test_backend_identity(self, adapter):
        identity = adapter.get_backend_identity()
        assert identity == "ollama-helium:test-model"
    
    def test_build_prompt_noul(self, adapter):
        state = {"entity": "test", "value": 42}
        question = "Is the value greater than 40?"
        prompt = adapter._build_prompt(state, question, JevPrimitiveType.NOUL)
        
        assert "STATE CONTEXT:" in prompt
        assert "PROPOSITION:" in prompt
        assert question in prompt
        assert "probability" in prompt
        assert "confidence_concentration" in prompt
    
    def test_build_prompt_choice(self, adapter):
        state = {"drift_type": "transcript"}
        question = "What type of drift is this?"
        prompt = adapter._build_prompt(state, question, JevPrimitiveType.CHOICE)
        
        assert "label_probabilities" in prompt
        assert "top_label" in prompt
        assert "flag_for_review" in prompt
    
    def test_build_prompt_score(self, adapter):
        state = {"readiness": 0.8}
        question = "What is the readiness score?"
        prompt = adapter._build_prompt(state, question, JevPrimitiveType.SCORE)
        
        assert "expected_value" in prompt
        assert "legend" in prompt
    
    def test_summarize_state(self, adapter):
        state = {
            "simple": "value",
            "number": 42,
            "flag": True,
            "nested": {"a": 1, "b": 2},
            "items": [1, 2, 3, 4, 5],
        }
        summary = adapter._summarize_state(state)
        
        assert "value" in summary
        assert "42" in summary
        assert "true" in summary.lower() or "True" in summary
        assert "dict" in summary or "nested" in summary


class TestJevKnowledgeBase:
    """Test the KnowledgeBase wrapper."""
    
    @pytest.fixture
    def mock_adapter(self):
        adapter = MagicMock()
        adapter.get_backend_identity.return_value = "ollama-test:model"
        adapter.system_one = AsyncMock()
        return adapter
    
    @pytest.fixture
    def kb(self, mock_adapter):
        return JevKnowledgeBase(mock_adapter)
    
    @pytest.mark.asyncio
    async def test_query_noul_success(self, kb, mock_adapter):
        """Test successful Noul query round-trip."""
        mock_adapter.system_one.return_value = [
            NoulResult(probability=0.85, confidence=MagicMock(concentration=0.9, decision_mass=0.8))
        ]
        
        result = await kb.query("Is the proposition true?", {"entity": "test"})
        
        assert result is not None
        assert isinstance(result, NoulResult)
        assert result.probability == 0.85
        mock_adapter.system_one.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_query_choice_success(self, kb, mock_adapter):
        """Test successful Choice query round-trip."""
        mock_adapter.system_one.return_value = [
            ChoiceResult(
                label_probabilities={"drift": 0.7, "transcript": 0.3},
                top_label="drift",
                confidence=MagicMock(concentration=0.8, decision_mass=0.7),
                flag_for_review=False,
            )
        ]
        
        result = await kb.query("What type of drift?", {"context": "drift"})
        
        assert result is not None
        assert isinstance(result, ChoiceResult)
        assert result.top_label == "drift"
        assert result.flag_for_review is False
    
    @pytest.mark.asyncio
    async def test_query_score_success(self, kb, mock_adapter):
        """Test successful Score query round-trip."""
        mock_adapter.system_one.return_value = [
            ScoreResult(
                expected_value=0.82,
                confidence=MagicMock(concentration=0.85, decision_mass=0.8),
                legend={"high": ">0.8", "medium": "0.5-0.8", "low": "<0.5"},
            )
        ]
        
        result = await kb.query("What is the readiness score?", {"readiness": 0.8})
        
        assert result is not None
        assert isinstance(result, ScoreResult)
        assert result.expected_value == 0.82
    
    @pytest.mark.asyncio
    async def test_query_non_outcome_uncertain(self, kb, mock_adapter):
        """Test uncertain non-outcome (low confidence)."""
        mock_adapter.system_one.return_value = [
            JevNonOutcomeResult(kind="uncertain", reason="Low confidence", retryable=False)
        ]
        
        result = await kb.query("Ambiguous question?", {"context": "ambiguous"})
        
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "uncertain"
    
    @pytest.mark.asyncio
    async def test_query_failure_fail_visible(self, kb, mock_adapter):
        """Test that errors are fail-visible (return non-outcome, not exception)."""
        mock_adapter.system_one.side_effect = Exception("Connection failed")
        
        result = await kb.query("Question?", {"context": "test"})
        
        # Should return non-outcome, not raise
        assert result is not None
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "unevaluable"
        assert "Connection failed" in result.reason
        assert result.retryable is True
    
    @pytest.mark.asyncio
    async def test_cache_hit(self, kb, mock_adapter):
        """Test that cache returns previous result without calling adapter."""
        result_obj = NoulResult(probability=0.9, confidence=MagicMock())
        mock_adapter.system_one.return_value = [result_obj]
        
        # First call
        await kb.query("Cached question?", {"context": "test"})
        # Second call with same key
        await kb.query("Cached question?", {"context": "test"})
        
        # Adapter should only be called once
        assert mock_adapter.system_one.call_count == 1
    
    def test_add_knowledge(self, kb):
        """Test manual knowledge addition."""
        kb.add_knowledge("manual_fact", "known_value", {"context": "manual"})
        
        # Query should return the manual knowledge (query is async; run it on a
        # private loop — asyncio.run leaves the global loop unset and breaks
        # legacy get_event_loop callers in later tests)
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(kb.query("manual_fact", {"context": "manual"}))
        finally:
            loop.close()
        assert result == "known_value"


class TestSyncJevKnowledgeBase:
    """Test synchronous wrapper."""
    
    @pytest.fixture
    def sync_kb(self):
        return create_sync_jev_knowledge_base(
            ollama_url="http://test:11434",
            model="test-model",
        )
    
    def test_sync_query_noul(self, sync_kb):
        """Test synchronous Noul query."""
        # Mock the adapter's system_one
        sync_kb.adapter.system_one = AsyncMock(return_value=[
            NoulResult(probability=0.75, confidence=MagicMock(concentration=0.8, decision_mass=0.7))
        ])
        
        result = sync_kb.query("Sync test?", {"sync": True})
        
        assert result is not None
        assert isinstance(result, NoulResult)
        assert result.probability == 0.75


class TestIntegrationWithInferenceEngine:
    """Test integration with InferenceEngine.external_knowledge_base."""
    
    @pytest.fixture
    def sync_kb(self):
        return create_sync_jev_knowledge_base(
            ollama_url="http://test:11434",
            model="test-model",
        )
    
    def test_knowledge_base_interface(self, sync_kb):
        """Verify JevKnowledgeBase implements KnowledgeBase interface."""
        from solscript.inference_engine import KnowledgeBase
        assert isinstance(sync_kb, KnowledgeBase)
    
    def test_inference_engine_integration(self, sync_kb):
        """Test that the KB can be plugged into InferenceEngine."""
        from solscript.inference_engine import InferenceEngine
        from solscript.models import Entity
        
        # Create a mock interpreter
        class MockInterpreter:
            def get_concept(self, concept_id):
                return None
            def get_attribute(self, attr_id):
                return None
        
        engine = InferenceEngine(MockInterpreter())
        engine.external_knowledge_base = sync_kb
        
        # Add a fact that can be inferred
        engine.add_fact("test_key", "test_value")
        
        # The engine should be able to use the KB
        assert engine.external_knowledge_base is sync_kb


class TestGuardrails:
    """Test that guardrails are enforced."""
    
    def test_no_admission_authority_in_code(self):
        """Verify adapter has no admission logic."""
        # The adapter only returns judgments — no admission decisions
        adapter = create_sync_jev_knowledge_base()
        assert hasattr(adapter, 'query')
        assert not hasattr(adapter, 'admit')
        assert not hasattr(adapter, 'settle_review')
    
    def test_no_governance_writes(self):
        """Verify adapter has no governance mutation methods."""
        adapter = create_sync_jev_knowledge_base()
        # Only read/query methods
        methods = [m for m in dir(adapter) if not m.startswith('_')]
        assert 'query' in methods
        assert 'add_knowledge' in methods  # Base KB method, not governance
        governance_methods = [m for m in methods if any(g in m.lower() for g in ['admit', 'settle', 'mutate', 'write', 'decide'])]
        assert len(governance_methods) == 0, f"Found governance methods: {governance_methods}"


class TestFailVisibleBehavior:
    """Test that errors are fail-visible."""
    
    @pytest.mark.asyncio
    async def test_timeout_returns_non_outcome(self):
        """Timeout should return JevNonOutcomeResult, not raise."""
        config = JevAdapterConfig(
            ollama=OllamaConfig(base_url="http://unreachable:11434", timeout_seconds=0.001),
        )
        adapter = OllamaTypeSafeAdapter(config)
        
        # The adapter will fail to connect, but system_one should return non-outcome
        async with adapter:
            results = await adapter.system_one(
                state={"test": True},
                questions=["Will this timeout?"],
                expected_types=[JevPrimitiveType.NOUL],
            )
        
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, JevNonOutcomeResult)
        assert result.kind == "unevaluable"
        assert result.retryable is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])