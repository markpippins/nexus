"""Tests for CHOICE label-vocabulary passthrough (Jev 6, item 2).

Acceptance criteria:
- Corpus labels reach the prompt verbatim when provided
- Legacy behavior (no labels) is unchanged
- Model output is constrained onto the canonical vocabulary (case-insensitive)
- Invented labels are dropped and unmappable top picks flag for review
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from solscript.adapters.jev.adapter import (
    JevAdapterConfig,
    JevPrimitiveType,
    OllamaTypeSafeAdapter,
)


LABELS = ["concept_drift", "transcript_drift", "schema_drift", "noise"]


@pytest.fixture
def adapter():
    return OllamaTypeSafeAdapter(JevAdapterConfig())


class TestPromptLabels:
    def test_labels_injected_verbatim(self, adapter):
        prompt = adapter._build_prompt(
            {"drift_magnitude": 0.6}, "Classify the drift.",
            JevPrimitiveType.CHOICE, LABELS,
        )
        for label in LABELS:
            assert f'"{label}"' in prompt

    def test_legacy_prompt_unchanged_without_labels(self, adapter):
        prompt = adapter._build_prompt(
            {"drift_magnitude": 0.6}, "Classify the drift.",
            JevPrimitiveType.CHOICE,
        )
        assert "ALLOWED LABELS" not in prompt
        for label in LABELS:
            assert label not in prompt

    def test_noul_prompt_ignores_labels(self, adapter):
        prompt = adapter._build_prompt(
            {"readiness_score": 0.92}, "Is it ready?",
            JevPrimitiveType.NOUL, LABELS,
        )
        assert "ALLOWED LABELS" not in prompt


class TestParseConstraining:
    def _response(self, probs, top):
        return json.dumps({
            "label_probabilities": probs,
            "top_label": top,
            "confidence_concentration": 0.8,
            "confidence_decision_mass": 0.7,
        })

    def test_canonical_labels_pass_through(self, adapter):
        probs = {"concept_drift": 0.1, "transcript_drift": 0.8,
                 "schema_drift": 0.05, "noise": 0.05}
        result = adapter._parse_response(
            self._response(probs, "transcript_drift"),
            JevPrimitiveType.CHOICE, LABELS,
        )
        assert result.top_label == "transcript_drift"
        assert result.label_probabilities == probs
        assert result.flag_for_review is False

    def test_case_variants_mapped_to_canonical(self, adapter):
        probs = {"Transcript_Drift": 0.8, "Concept_Drift": 0.2}
        result = adapter._parse_response(
            self._response(probs, "TRANSCRIPT_DRIFT"),
            JevPrimitiveType.CHOICE, LABELS,
        )
        assert result.top_label == "transcript_drift"
        assert result.label_probabilities == {
            "transcript_drift": 0.8, "concept_drift": 0.2}

    def test_invented_labels_dropped_and_flagged(self, adapter):
        probs = {"drift_type1": 0.8, "drift_type2": 0.2}
        result = adapter._parse_response(
            self._response(probs, "drift_type1"),
            JevPrimitiveType.CHOICE, LABELS,
        )
        assert result.label_probabilities == {}
        assert result.flag_for_review is True

    def test_no_labels_preserves_legacy_parse(self, adapter):
        probs = {"drift_type1": 0.8, "drift_type2": 0.2}
        result = adapter._parse_response(
            self._response(probs, "drift_type1"), JevPrimitiveType.CHOICE)
        assert result.top_label == "drift_type1"
        assert result.label_probabilities == probs


class TestSystemOneLabelSets:
    @pytest.mark.asyncio
    async def test_label_sets_reach_model_and_constrain_output(self, adapter):
        adapter.client = AsyncMock()  # bypass "not initialized" guard
        adapter._call_ollama = AsyncMock(return_value=json.dumps({
            "label_probabilities": {"transcript_drift": 0.75, "concept_drift": 0.25},
            "top_label": "transcript_drift",
            "confidence_concentration": 0.8,
            "confidence_decision_mass": 0.7,
        }))
        results = await adapter.system_one(
            {"drift_magnitude": 0.62},
            ["Classify the drift."],
            [JevPrimitiveType.CHOICE],
            [LABELS],
        )
        assert len(results) == 1
        assert results[0].top_label == "transcript_drift"
        # The prompt actually sent must contain the vocabulary
        sent_prompt = adapter._call_ollama.call_args[0][0]
        for label in LABELS:
            assert f'"{label}"' in sent_prompt

    @pytest.mark.asyncio
    async def test_none_label_sets_preserve_legacy(self, adapter):
        adapter.client = AsyncMock()
        adapter._call_ollama = AsyncMock(return_value=json.dumps({
            "label_probabilities": {"drift_type1": 0.8, "drift_type2": 0.2},
            "top_label": "drift_type1",
            "confidence_concentration": 0.8,
            "confidence_decision_mass": 0.7,
        }))
        results = await adapter.system_one(
            {"drift_magnitude": 0.62},
            ["Classify the drift."],
            [JevPrimitiveType.CHOICE],
        )
        assert results[0].top_label == "drift_type1"
